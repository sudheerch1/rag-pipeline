# llm_clients.py
import boto3
from functools import lru_cache
from langchain_aws import BedrockEmbeddings, ChatBedrock

@lru_cache(maxsize=1)
def get_embeddings():
    return BedrockEmbeddings(
        model_id="amazon.titan-embed-text-v1",
        region_name="us-east-1"
    )

@lru_cache(maxsize=1)
def get_llm():
    return ChatBedrock(
        model_id="amazon.nova-micro-v1:0",
        region_name="us-east-1",
        model_kwargs={"temperature": 0.3, "max_tokens": 512}
    )