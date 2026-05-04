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

    # Format results
    results = []
    for hit in hits:
        results.append({
            'text': hit['_source']['text'],
            'source': hit['_source']['source_key'],
            'page': hit['_source'].get('page'),
            'score': hit['_score']
        })

    return {
        'statusCode': 200,
        'body': json.dumps({
            'question': question,
            'results': results
        })
    }