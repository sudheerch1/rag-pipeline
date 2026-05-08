import json
import boto3
import os
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth
from config import get_opensearch_endpoint

bedrock_client = boto3.client('bedrock-runtime',
    region_name=os.environ.get('AWS_REGION', 'us-east-1'))

OPENSEARCH_ENDPOINT = get_opensearch_endpoint()

# ── CORS Headers ──────────────────────────────────────────────────────────────
CORS_HEADERS = {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type,x-api-key,Authorization',
    'Access-Control-Allow-Methods': 'POST,OPTIONS'
}

def get_opensearch_client():
    credentials = boto3.Session().get_credentials()
    awsauth = AWS4Auth(
        credentials.access_key,
        credentials.secret_key,
        'us-east-1',
        'es',
        session_token=credentials.token
    )
    return OpenSearch(
        hosts=[{'host': OPENSEARCH_ENDPOINT, 'port': 443}],
        http_auth=awsauth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection
    )

def embed_question(question):
    response = bedrock_client.invoke_model(
        modelId='amazon.titan-embed-text-v1',
        body=json.dumps({'inputText': question})
    )
    result = json.loads(response['body'].read())
    return result['embedding']

def search_opensearch(question, question_vector, top_k=10):
    """
    Hybrid search combining BM25 + KNN + HyQ matching.
    Returns top_k=10 candidates for reranking (more candidates = better reranking).
    """
    client = get_opensearch_client()
    query = {
        "size": top_k,
        "query": {
            "hybrid": {
                "queries": [
                    {
                        "multi_match": {
                            "query": question,
                            "fields": [
                                "text^1.0",
                                "hyq_questions^2.0"
                            ]
                        }
                    },
                    {
                        "knn": {
                            "embedding": {
                                "vector": question_vector,
                                "k": top_k
                            }
                        }
                    }
                ]
            }
        },
        "_source": ["text", "source_key", "page", "chunk_index", "hyq_questions", "section"]
    }
    response = client.search(
        index='rag-chunks',
        body=query,
        params={"search_pipeline": "hybrid-search-pipeline"}
    )
    return response['hits']['hits']


def rerank_chunks(question: str, chunks: list, top_n: int = 5) -> list:
    """
    Rerank chunks using Cohere Rerank 3.5 on Amazon Bedrock.

    Why reranking improves results:
    - Hybrid search uses bi-encoders (question and chunks embedded separately)
    - Cohere Rerank uses cross-encoder (question + chunk fed together)
    - Cross-encoder is more accurate but slower → use AFTER initial search
    - We search top 10, rerank, keep best 5

    Falls back to original order if reranking fails (non-blocking).
    """
    if not chunks:
        return chunks

    try:
        # Prepare documents for reranking
        documents = [chunk['text'] for chunk in chunks]

        # Call Cohere Rerank via Bedrock
        request_body = {
            "query": question,
            "documents": documents,
            "top_n": top_n,
            "api_version": 2
        }

        response = bedrock_client.invoke_model(
            modelId='cohere.rerank-v3-5:0',
            body=json.dumps(request_body)
        )

        result = json.loads(response['body'].read())
        rerank_results = result.get('results', [])

        # Reorder chunks based on Cohere's ranking
        reranked = []
        for item in rerank_results:
            original_idx = item['index']
            reranked_chunk = chunks[original_idx].copy()
            reranked_chunk['rerank_score'] = item['relevance_score']
            reranked_chunk['original_score'] = reranked_chunk['score']
            reranked_chunk['score'] = item['relevance_score']
            reranked.append(reranked_chunk)

        print(f"Reranked {len(chunks)} chunks → top {len(reranked)}")
        return reranked

    except Exception as e:
        print(f"Reranking failed (non-blocking): {str(e)}")
        print("Falling back to original search order")
        return chunks[:top_n]


def generate_answer(question, chunks):
    context = "\n\n".join([
        f"Source: {chunk['source']} (Page {chunk['page']})\n{chunk['text']}"
        for chunk in chunks
    ])

    prompt = f"""You are a helpful assistant. Answer the question based only on the provided context.
If the answer is not in the context, say "I cannot find this information in the provided documents."

Context:
{context}

Question: {question}

Answer:"""

    response = bedrock_client.invoke_model(
        modelId='amazon.nova-micro-v1:0',
        body=json.dumps({
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": prompt}]
                }
            ]
        })
    )

    result = json.loads(response['body'].read())
    return result['output']['message']['content'][0]['text']


def lambda_handler(event, context):

    # ── Handle OPTIONS preflight ───────────────────────────────────────────
    if event.get('httpMethod') == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': CORS_HEADERS,
            'body': ''
        }

    # Parse body
    if isinstance(event.get('body'), str):
        body = json.loads(event.get('body', '{}'))
    elif isinstance(event.get('body'), dict):
        body = event.get('body', {})
    else:
        body = event

    question = body.get('question', '')

    if not question:
        return {
            'statusCode': 400,
            'headers': CORS_HEADERS,
            'body': json.dumps({'error': 'No question provided'})
        }

    print(f"Question: {question}")

    # Step 1: Embed question
    question_vector = embed_question(question)
    print(f"Question embedded")

    # Step 2: Hybrid search - get top 10 candidates
    hits = search_opensearch(question, question_vector, top_k=10)
    print(f"Hybrid search found {len(hits)} candidates")

    # Step 3: Format chunks from search results
    chunks = []
    for hit in hits:
        chunks.append({
            'text': hit['_source']['text'],
            'source': hit['_source']['source_key'],
            'page': hit['_source'].get('page'),
            'section': hit['_source'].get('section', ''),
            'score': hit['_score']
        })

    # Step 4: Rerank with Cohere → get best 5
    chunks = rerank_chunks(question, chunks, top_n=5)
    print(f"Reranking complete, using top {len(chunks)} chunks")

    # Step 4b: Deduplicate by page number
    seen_pages = set()
    unique_chunks = []
    for c in chunks:
        page_key = f"{c['source']}_{c['page']}"
        if page_key not in seen_pages:
            seen_pages.add(page_key)
            unique_chunks.append(c)
    chunks = unique_chunks
    print(f"After deduplication: {len(chunks)} unique chunks")

    # Step 5: Generate answer with Nova
    answer = generate_answer(question, chunks)
    print(f"Answer generated")

    return {
        'statusCode': 200,
        'headers': CORS_HEADERS,
        'body': json.dumps({
            'question': question,
            'answer': answer,
            'sources': [
                {
                    'source': c['source'],
                    'page': c['page'],
                    'section': c.get('section', ''),
                    'score': c['score']
                }
                for c in chunks
            ]
        })
    }