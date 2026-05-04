import json
import boto3
import os
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth

bedrock_client = boto3.client('bedrock-runtime', 
    region_name=os.environ.get('AWS_REGION', 'us-east-1'))

OPENSEARCH_ENDPOINT = os.environ.get('OPENSEARCH_ENDPOINT', 
    'search-rag-vectors-xzipwfal5kzob35v4x7uzpnz7i.us-east-1.es.amazonaws.com')

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

def search_opensearch(question_vector, top_k=5):
    client = get_opensearch_client()
    query = {
        "size": top_k,
        "query": {
            "knn": {
                "embedding": {
                    "vector": question_vector,
                    "k": top_k
                }
            }
        },
        "_source": ["text", "source_key", "page", "chunk_index"]
    }
    response = client.search(index='rag-chunks', body=query)
    return response['hits']['hits']


def generate_answer(question, chunks):
    # Build context from chunks
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
                    "content": [
                        {"text": prompt}
                    ]
                }
            ]
        })
    )

    result = json.loads(response['body'].read())
    return result['output']['message']['content'][0]['text']

def lambda_handler(event, context):
    # Get question from request
    body = json.loads(event.get('body', '{}'))
    question = body.get('question', '')

    if not question:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'No question provided'})
        }

    print(f"Question: {question}")

    # Convert question to vector
    question_vector = embed_question(question)
    print(f"Question embedded")

    # Search OpenSearch
    hits = search_opensearch(question_vector)
    print(f"Found {len(hits)} results")

    # Format chunks
    chunks = []
    for hit in hits:
        chunks.append({
            'text': hit['_source']['text'],
            'source': hit['_source']['source_key'],
            'page': hit['_source'].get('page'),
            'score': hit['_score']
        })

    # Generate answer using Claude
    answer = generate_answer(question, chunks)
    print(f"Answer generated")

    return {
        'statusCode': 200,
        'body': json.dumps({
            'question': question,
            'answer': answer,
            'sources': [
                {
                    'source': c['source'],
                    'page': c['page'],
                    'score': c['score']
                }
                for c in chunks
            ]
        })
    }