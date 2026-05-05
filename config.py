import boto3
import json

def get_parameter(name, profile_name=None):
    session = boto3.Session(profile_name=profile_name)
    client = session.client('ssm', region_name='us-east-1')
    response = client.get_parameter(Name=name)
    return response['Parameter']['Value']

def get_opensearch_endpoint(profile_name=None):
    return get_parameter('/rag/opensearch/endpoint', profile_name)

def get_opensearch_credentials(profile_name=None):
    session = boto3.Session(profile_name=profile_name)
    client = session.client('secretsmanager', region_name='us-east-1')
    response = client.get_secret_value(
        SecretId='rag/opensearch/credentials'
    )
    secret = json.loads(response['SecretString'])
    return secret['OPENSEARCH_USER'], secret['OPENSEARCH_PASSWORD']