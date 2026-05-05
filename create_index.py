import requests
from requests.auth import HTTPBasicAuth
from config import get_opensearch_endpoint, get_opensearch_credentials

# Get everything from AWS
endpoint = f"https://{get_opensearch_endpoint(profile_name='rag-dev')}"
username, password = get_opensearch_credentials(profile_name='rag-dev')
auth = HTTPBasicAuth(username, password)

print(f"Connecting to: {endpoint}")
print(f"Username: {username}")

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
    auth=auth,
    json=index_body,
    headers={"Content-Type": "application/json"}
)

print(f"Status: {response.status_code}")
print(f"Response: {response.text}")