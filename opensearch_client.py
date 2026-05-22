# opensearch_client.py
import boto3
from functools import lru_cache
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth
from config import get_opensearch_endpoint, get_opensearch_credentials

@lru_cache(maxsize=1)
def get_opensearch_client():
    endpoint = get_opensearch_endpoint()
    
    # Use AWS4Auth when running in Lambda (IAM role)
    # Fall back to basic auth for local development
    try:
        session = boto3.Session()
        credentials = session.get_credentials()
        region = "us-east-1"
        awsauth = AWS4Auth(
            credentials.access_key,
            credentials.secret_key,
            region,
            "es",
            session_token=credentials.token
        )
        auth = awsauth
    except Exception:
        user, password = get_opensearch_credentials()
        auth = (user, password)

    return OpenSearch(
        hosts=[{"host": endpoint, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection
    )