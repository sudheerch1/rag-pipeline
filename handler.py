import json
import boto3
import os
import tempfile
from detector import detect
from parsers import parse
from chunker import chunk

s3_client = boto3.client('s3')

PROCESSED_BUCKET = os.environ.get('PROCESSED_BUCKET', 'rag-processed-911414006786')

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

    finally:
        # Always clean up temp file
        os.unlink(tmp_path)