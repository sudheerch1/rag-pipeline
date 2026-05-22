import requests
from requests.auth import HTTPBasicAuth
from config import get_opensearch_endpoint, get_opensearch_credentials

endpoint = "https://" + get_opensearch_endpoint(profile_name='rag-dev')
username, password = get_opensearch_credentials(profile_name='rag-dev')
auth = HTTPBasicAuth(username, password)
response = requests.get(endpoint + "/rag-chunks/_mapping", auth=auth)
print(response.text)