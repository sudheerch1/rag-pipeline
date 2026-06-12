# llm_clients.py
import json
import boto3
from functools import lru_cache
from langchain_aws import ChatBedrock

EMBED_MODEL_ID = "cohere.embed-v4:0"
LLM_MODEL_ID   = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

@lru_cache(maxsize=1)
def _get_bedrock_client():
    return boto3.client("bedrock-runtime", region_name="us-east-1")

def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed a batch of document chunks at ingest time."""
    client = _get_bedrock_client()
    body = json.dumps({
        "texts": texts,
        "input_type": "search_document",
        "embedding_types": ["float"]
    })
    response = client.invoke_model(modelId=EMBED_MODEL_ID, body=body)
    result = json.loads(response["body"].read())
    return result["embeddings"]["float"]

def embed_query(text: str) -> list[float]:
    """Embed a single query string at query time."""
    client = _get_bedrock_client()
    body = json.dumps({
        "texts": [text],
        "input_type": "search_query",
        "embedding_types": ["float"]
    })
    response = client.invoke_model(modelId=EMBED_MODEL_ID, body=body)
    result = json.loads(response["body"].read())
    return result["embeddings"]["float"][0]

@lru_cache(maxsize=1)
def get_llm():
    return ChatBedrock(
        model_id=LLM_MODEL_ID,
        region_name="us-east-1",
        model_kwargs={"temperature": 0.3, "max_tokens": 512}
    )