import json
import boto3
import os
import tempfile
from detector import detect
from parsers import parse
from chunker import chunk
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth
from config import get_opensearch_endpoint

s3_client = boto3.client('s3')
bedrock_client = boto3.client('bedrock-runtime',region_name=os.environ.get('AWS_REGION', 'us-east-1'))

dynamodb = boto3.resource('dynamodb')
METADATA_TABLE = os.environ.get('METADATA_TABLE', 'rag-metadata')

PROCESSED_BUCKET = os.environ.get('PROCESSED_BUCKET', 'rag-processed-911414006786')

OPENSEARCH_ENDPOINT = get_opensearch_endpoint()

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


def embed_chunks(chunks):
    """
    Convert chunks to vectors using Bedrock Titan Embeddings
    """
    embeddings = []
    for chunk in chunks:
        response = bedrock_client.invoke_model(
            modelId='amazon.titan-embed-text-v1',
            body=json.dumps({
                'inputText': chunk.text
            })
        )
        result = json.loads(response['body'].read())
        embeddings.append({
            'chunk_index': chunk.chunk_index,
            'text': chunk.text,
            'embedding': result['embedding'],
            'token_count': chunk.token_count,
            'page': chunk.page,
            'source_key': chunk.source_key
        })
    return embeddings


def store_in_opensearch(embeddings):
    client = get_opensearch_client()
    for item in embeddings:
        client.index(
            index='rag-chunks',
            body={
                'text': item['text'],
                'embedding': item['embedding'],
                'source_key': item['source_key'],
                'page': item['page'],
                'chunk_index': item['chunk_index']
            }
        )
    print(f"Stored {len(embeddings)} embeddings in OpenSearch")


def save_metadata(object_key, file_type, blocks, chunks, status, error=None):
    table = dynamodb.Table(METADATA_TABLE)
    from datetime import datetime, timezone
    table.put_item(
        Item={
            'document_id': object_key,
            'status': status,
            'file_type': file_type,
            'blocks': blocks,
            'chunks': chunks,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'error': error or 'none'
        }
    )
    print(f"Metadata saved for: {object_key}")

def lambda_handler(event, context):
    """
    Main Lambda handler - triggered by SQS
    Each SQS message contains an S3 event notification
    """
    processed = []
    failed = []

    for record in event['Records']:
        try:
            # Parse SQS message
            body = json.loads(record['body'])

            # Skip S3 test events
            if 'Event' in body and body['Event'] == 's3:TestEvent':
                print("Skipping S3 test event")
                continue

            # Extract S3 details
            s3_event = body['Records'][0]
            bucket_name = s3_event['s3']['bucket']['name']
            object_key = s3_event['s3']['object']['key']

            print(f"Processing file: {object_key} from bucket: {bucket_name}")

            # Process the file
            result = process_file(bucket_name, object_key)
            processed.append(result)

        except Exception as e:
            print(f"Error processing record: {str(e)}")
            failed.append(str(e))
            raise  # Re-raise so SQS can retry

    return {
        'statusCode': 200,
        'processed': len(processed),
        'failed': len(failed)
    }


def process_file(bucket_name, object_key):
    """
    Download file from S3, parse it, chunk it, save results
    """
    # Download file to temp location
    with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
        tmp_path = tmp_file.name
        s3_client.download_file(bucket_name, object_key, tmp_path)
        print(f"Downloaded to: {tmp_path}")

    try:
        # Detect file type
        with open(tmp_path, 'rb') as f:
            file_bytes = f.read()
        result = detect(file_bytes, object_key.split('/')[-1])
        file_type = result.parser_key
        print(f"Detected file type: {file_type}")

        # Parse file into blocks
        blocks = parse(file_bytes, file_type)
        print(f"Parsed {len(blocks)} blocks")

        # Chunk blocks
        chunks = chunk(blocks, file_type)
        print(f"Created {len(chunks)} chunks")

        # Generate embeddings
        embeddings = embed_chunks(chunks)
        print(f"Generated {len(embeddings)} embeddings")

        # Store in OpenSearch
        store_in_opensearch(embeddings)
        
        # Save metadata
        save_metadata(
            object_key=object_key,
            file_type=file_type,
            blocks=len(blocks),
            chunks=len(chunks),
            status='completed'
        )

        # Save chunks to processed bucket
        output_key = f"chunks/{object_key}.json"
        chunks_data = [chunk.__dict__ for chunk in chunks]

        s3_client.put_object(
            Bucket=PROCESSED_BUCKET,
            Key=output_key,
            Body=json.dumps(chunks_data, indent=2),
            ContentType='application/json'
        )
        print(f"Saved chunks to: {output_key}")

        return {
            'source_key': object_key,
            'file_type': file_type,
            'blocks': len(blocks),
            'chunks': len(chunks),
            'output_key': output_key
        }

    except Exception as e:
        print(f"Error processing record: {str(e)}")
        save_metadata(
            object_key=object_key,
            file_type='unknown',
            blocks=0,
            chunks=0,
            status='failed',
            error=str(e)
        )
        raise

    finally:
        # Always clean up temp file
        os.unlink(tmp_path)