import boto3
import json
import logging
import json as _json

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

class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "level":   record.levelname,
            "message": record.getMessage(),
            "logger":  record.name,
        }
        # merge any extra fields passed via extra={}
        for key, value in record.__dict__.items():
            if key not in (
                'args', 'asctime', 'created', 'exc_info', 'exc_text',
                'filename', 'funcName', 'id', 'levelname', 'levelno',
                'lineno', 'module', 'msecs', 'message', 'msg', 'name',
                'pathname', 'process', 'processName', 'relativeCreated',
                'stack_info', 'thread', 'threadName', 'logger'
            ):
                log_record[key] = value
        return _json.dumps(log_record)

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger