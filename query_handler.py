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

def search_opensearch(question, question_vector, top_k=5):
    """
    Hybrid search combining:
    - BM25 text search on both 'text' and 'hyq_questions' fields
      (hyq_questions boosted 2x for better question-to-question matching)
    - KNN vector search for semantic similarity

    Uses hybrid-search-pipeline for score normalization and combination.
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

    # Embed question for vector search
    question_vector = embed_question(question)
    print(f"Question embedded")

    # Hybrid search (BM25 + KNN + HyQ)
    hits = search_opensearch(question, question_vector)
    print(f"Hybrid search found {len(hits)} results")

    # Format chunks — include section for attribution
    chunks = []
    for hit in hits:
        chunks.append({
            'text': hit['_source']['text'],
            'source': hit['_source']['source_key'],
            'page': hit['_source'].get('page'),
            'section': hit['_source'].get('section', ''),   # ← section attribution
            'score': hit['_score']
        })

    # Generate answer
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
                    'section': c.get('section', ''),        # ← safe .get()
                    'score': c['score']
                }
                for c in chunks
            ]
        })
    }