import requests
from requests.auth import HTTPBasicAuth

endpoint = 'https://search-rag-vectors-xzipwfal5kzob35v4x7uzpnz7i.us-east-1.es.amazonaws.com'
auth = HTTPBasicAuth('ragadmin', 'Jahnavi2012*')

# Check document count
response = requests.get(
    f"{endpoint}/rag-chunks/_count",
    auth=auth,
    headers={"Content-Type": "application/json"}
)
print(f"Document count: {response.text}")