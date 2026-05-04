import boto3
import requests
from requests.auth import HTTPBasicAuth

# Basic authentication


# OpenSearch endpoint
endpoint = 'https://search-rag-vectors-xzipwfal5kzob35v4x7uzpnz7i.us-east-1.es.amazonaws.com'

# AWS authentication
session = boto3.Session(profile_name='rag-dev')
credentials = session.get_credentials()

awsauth = HTTPBasicAuth('ragadmin', 'Jahnavi2012*')

# Index mapping
index_body = {
    "mappings": {
        "properties": {
            "text": {"type": "text"},
            "embedding": {
                "type": "knn_vector",
                "dimension": 1536
            },
            "source_key": {"type": "keyword"},
            "page": {"type": "integer"},
            "chunk_index": {"type": "integer"}
        }
    },
    "settings": {
        "index.knn": True
    }
}

# Create index
url = f"{endpoint}/rag-chunks"
response = requests.put(
    url,
    auth=awsauth,
    json=index_body,
    headers={"Content-Type": "application/json"}
)

print(f"Status: {response.status_code}")
print(f"Response: {response.text}")