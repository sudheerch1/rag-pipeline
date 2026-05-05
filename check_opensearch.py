import requests
from requests.auth import HTTPBasicAuth
import os

endpoint = 'https://search-rag-vectors-xzipwfal5kzob35v4x7uzpnz7i.us-east-1.es.amazonaws.com'
auth = HTTPBasicAuth(
    os.environ.get('OPENSEARCH_USER', ''),
    os.environ.get('OPENSEARCH_PASSWORD', '')
)
# Check document count
response = requests.get(
    f"{endpoint}/rag-chunks/_count",
    auth=auth,
    headers={"Content-Type": "application/json"}
)
print(f"Document count: {response.text}")