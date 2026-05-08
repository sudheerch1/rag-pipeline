import json
import boto3
import os
import tempfile
import hashlib
from detector import detect
from parsers import parse
from chunker import chunk
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth
from config import get_opensearch_endpoint

s3_client = boto3.client('s3')
bedrock_client = boto3.client('bedrock-runtime', region_name=os.environ.get('AWS_REGION', 'us-east-1'))

dynamodb = boto3.resource('dynamodb')
METADATA_TABLE = os.environ.get('METADATA_TABLE', 'rag-metadata')
PROCESSED_BUCKET = os.environ.get('PROCESSED_BUCKET', 'rag-processed-911414006786')
HASH_INDEX_NAME = 'content-hash-index'

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


# ── Duplicate Detection ───────────────────────────────────────────────────────

def compute_hash(file_bytes: bytes) -> str:
    """
    Compute SHA-256 hash of file contents.
    Returns a 64-character hex string — unique fingerprint of the file.
    """
    return hashlib.sha256(file_bytes).hexdigest()


def check_duplicate(content_hash: str) -> dict | None:
    """
    Query DynamoDB GSI to check if this content hash already exists.

    Returns:
        dict  → existing item if duplicate found (contains document_id, timestamp etc.)
        None  → no duplicate, safe to process
    """
    table = dynamodb.Table(METADATA_TABLE)
    response = table.query(
        IndexName=HASH_INDEX_NAME,
        KeyConditionExpression=boto3.dynamodb.conditions.Key('content_hash').eq(content_hash)
    )
    items = response.get('Items', [])
    return items[0] if items else None


# ── Embedding ─────────────────────────────────────────────────────────────────

def embed_chunks(chunks):
    """
    Convert chunks to vectors using Bedrock Titan Embeddings.
    """
    embeddings = []
    for chunk in chunks:
        response = bedrock_client.invoke_model(
            modelId='amazon.titan-embed-text-v1',
            body=json.dumps({'inputText': chunk.text})
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


# ── OpenSearch ────────────────────────────────────────────────────────────────

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


# ── DynamoDB Metadata ─────────────────────────────────────────────────────────

def save_metadata(object_key, file_type, blocks, chunks, status, content_hash, error=None):
    """
    Save document metadata to DynamoDB.
    Now includes content_hash for duplicate detection.
    """
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
            'content_hash': content_hash,
            'error': error or 'none'
        }
    )
    print(f"Metadata saved for: {object_key}")


# ── Lambda Handler ────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    """
    Main Lambda handler - triggered by SQS.
    Each SQS message contains an S3 event notification.
    """
    processed = []
    failed = []
    skipped = []

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

            if result.get('skipped'):
                skipped.append(result)
            else:
                processed.append(result)

        except Exception as e:
            print(f"Error processing record: {str(e)}")
            failed.append(str(e))
            raise  # Re-raise so SQS can retry

    return {
        'statusCode': 200,
        'processed': len(processed),
        'skipped': len(skipped),
        'failed': len(failed)
    }


# ── File Processing ───────────────────────────────────────────────────────────

def process_file(bucket_name, object_key):
    """
    Download file from S3, check for duplicates, then parse/chunk/embed.

    Duplicate detection flow:
      1. Download file bytes
      2. Compute SHA-256 hash
      3. Query DynamoDB GSI for existing hash
      4a. Hash found → log and skip (no Bedrock calls, no OpenSearch writes)
      4b. Hash not found → full ingestion pipeline
    """
    # Download file to temp location
    with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
        tmp_path = tmp_file.name
        s3_client.download_file(bucket_name, object_key, tmp_path)
        print(f"Downloaded to: {tmp_path}")

    try:
        # Read file bytes
        with open(tmp_path, 'rb') as f:
            file_bytes = f.read()

        # ── DUPLICATE DETECTION ──────────────────────────────────────────
        content_hash = compute_hash(file_bytes)
        print(f"Computed SHA-256 hash: {content_hash}")

        existing = check_duplicate(content_hash)
        if existing:
            print(
                f"DUPLICATE DETECTED: {object_key} has same content as "
                f"'{existing['document_id']}' (ingested at {existing['timestamp']}). "
                f"Skipping ingestion."
            )
            return {
                'skipped': True,
                'source_key': object_key,
                'duplicate_of': existing['document_id'],
                'content_hash': content_hash
            }
        # ── END DUPLICATE DETECTION ──────────────────────────────────────

        # Detect file type
        result = detect(file_bytes, object_key.split('/')[-1])
        file_type = result.parser_key
        print(f"Detected file type: {file_type}")

        # Parse file into blocks
        blocks = parse(file_bytes, file_type)
        print(f"Parsed {len(blocks)} blocks")

        # Chunk blocks
        chunks = chunk(blocks, file_type)
        for c in chunks:
            c.source_key = object_key
        print(f"Created {len(chunks)} chunks")

        # Generate embeddings
        embeddings = embed_chunks(chunks)
        print(f"Generated {len(embeddings)} embeddings")

        # Store in OpenSearch
        store_in_opensearch(embeddings)

        # Save metadata with content_hash
        save_metadata(
            object_key=object_key,
            file_type=file_type,
            blocks=len(blocks),
            chunks=len(chunks),
            status='completed',
            content_hash=content_hash
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
            'output_key': output_key,
            'content_hash': content_hash
        }

    except Exception as e:
        print(f"Error processing file: {str(e)}")
        save_metadata(
            object_key=object_key,
            file_type='unknown',
            blocks=0,
            chunks=0,
            status='failed',
            content_hash=content_hash if 'content_hash' in dir() else 'unknown',
            error=str(e)
        )
        raise

    finally:
        # Always clean up temp file
        os.unlink(tmp_path)