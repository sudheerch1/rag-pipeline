# ingestion_graph.py
import json
import boto3
import os
import tempfile
import hashlib
import time
from datetime import datetime, timezone
from typing import TypedDict, Optional
from urllib.parse import unquote_plus

from langgraph.graph import StateGraph, END

from detector import detect
from parsers import parse
from chunker import chunk
from opensearch_client import get_opensearch_client
from llm_clients import get_embeddings, get_llm
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from config import get_opensearch_endpoint

s3_client = boto3.client('s3')
bedrock_client = boto3.client('bedrock-runtime',
    region_name=os.environ.get('AWS_REGION', 'us-east-1'))
dynamodb = boto3.resource('dynamodb', region_name=os.environ.get('AWS_REGION', 'us-east-1'))

METADATA_TABLE   = os.environ.get('METADATA_TABLE', 'rag-metadata')
PROCESSED_BUCKET = os.environ.get('PROCESSED_BUCKET', 'rag-processed-911414006786')
HASH_INDEX_NAME  = 'content-hash-index'


# ── State ─────────────────────────────────────────────────────────────────────
# LangGraph passes this dict between every node.
# Each node reads what it needs and writes its outputs back in.

class IngestionState(TypedDict):
    # inputs
    bucket_name:  str
    object_key:   str
    # computed along the pipeline
    file_bytes:   Optional[bytes]
    content_hash: Optional[str]
    file_type:    Optional[str]
    blocks:       Optional[list]
    chunks:       Optional[list]
    embeddings:   Optional[list]
    # control flow
    status:       Optional[str]   # completed | duplicate | rejected | failed
    skip_reason:  Optional[str]
    error:        Optional[str]


# ── Node 1: Download ──────────────────────────────────────────────────────────

def node_download(state: IngestionState) -> IngestionState:
    print(f"[1/7] Downloading {state['object_key']} from {state['bucket_name']}...")
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name
        s3_client.download_file(state['bucket_name'], state['object_key'], tmp_path)
    with open(tmp_path, 'rb') as f:
        file_bytes = f.read()
    os.unlink(tmp_path)
    print(f"[1/7] Downloaded ({len(file_bytes):,} bytes)")
    return {**state, "file_bytes": file_bytes}


# ── Node 2: Duplicate Check ───────────────────────────────────────────────────

def node_check_duplicate(state: IngestionState) -> IngestionState:
    content_hash = hashlib.sha256(state['file_bytes']).hexdigest()
    print(f"[2/7] SHA-256: {content_hash[:20]}...")

    table = dynamodb.Table(METADATA_TABLE)
    response = table.query(
        IndexName=HASH_INDEX_NAME,
        KeyConditionExpression=boto3.dynamodb.conditions.Key('content_hash').eq(content_hash)
    )
    items = response.get('Items', [])

    if items:
        existing = items[0]
        print(f"[SKIP] Duplicate detected — already ingested as {existing['document_id']}")
        return {**state, "content_hash": content_hash, "status": "duplicate"}

    return {**state, "content_hash": content_hash}


# ── Node 3: Detect File Type ──────────────────────────────────────────────────

def node_detect(state: IngestionState) -> IngestionState:
    filename = state['object_key'].split('/')[-1]
    result = detect(state['file_bytes'], filename)
    print(f"[3/7] File type: {result.parser_key} (confidence: {result.confidence})")

    if result.parser_key == 'unknown':
        print(f"[SKIP] Unrecognised file type for {state['object_key']}")
        _save_metadata(state['object_key'], 'unknown', 0, 0, 'rejected',
                       state['content_hash'],
                       error=f"unknown_file_type:{result.confidence}")
        return {**state, "file_type": "unknown", "status": "rejected"}

    return {**state, "file_type": result.parser_key}


# ── Node 4: Parse ─────────────────────────────────────────────────────────────

def node_parse(state: IngestionState) -> IngestionState:
    blocks = parse(state['file_bytes'], state['file_type'])
    print(f"[4/7] Parsed into {len(blocks)} paragraph-level blocks")

    if not blocks:
        print(f"[ERROR] No blocks extracted from {state['object_key']}")
        _save_metadata(state['object_key'], state['file_type'], 0, 0, 'failed',
                       state['content_hash'], error='no_blocks_extracted')
        return {**state, "blocks": [], "status": "failed", "skip_reason": "no_blocks_extracted"}

    return {**state, "blocks": blocks}


# ── Node 5: Chunk ─────────────────────────────────────────────────────────────

def node_chunk(state: IngestionState) -> IngestionState:
    chunks = chunk(state['blocks'], state['file_type'])
    for c in chunks:
        c.source_key = state['object_key']
    print(f"[5/7] Semantic chunking → {len(chunks)} chunks from {len(state['blocks'])} blocks")
    return {**state, "chunks": chunks}


# ── Node 6: Embed + HyQ ───────────────────────────────────────────────────────

hyq_prompt = PromptTemplate.from_template("""Given the following text passage, generate exactly 3 questions that this passage directly answers.
Return ONLY the questions, one per line, with no numbering, bullets, or extra text.

Passage:
{chunk_text}

Questions:""")

hyq_chain = hyq_prompt | get_llm() | StrOutputParser()

def _embed_with_retry(text: str, max_retries: int = 3) -> list:
    for attempt in range(max_retries):
        try:
            return get_embeddings().embed_query(text)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt
            print(f"[Bedrock] Attempt {attempt+1} failed, retrying in {wait}s: {e}")
            time.sleep(wait)

def _generate_hyq(chunk_text: str) -> list[str]:
    try:
        raw = hyq_chain.invoke({"chunk_text": chunk_text})
        questions = [q.strip() for q in raw.strip().split('\n') if q.strip()]
        return [q for q in questions if q.endswith('?')][:3]
    except Exception as e:
        print(f"[HyQ] Failed (non-blocking): {e}")
        return []

def node_embed(state: IngestionState) -> IngestionState:
    chunks = state['chunks']
    print(f"[6/7] Generating embeddings + HyQ for {len(chunks)} chunks...")
    embeddings = []
    for i, c in enumerate(chunks):
        print(f"  Chunk {i+1}/{len(chunks)} | tokens: {c.token_count} | page: {c.page}")
        embedding  = _embed_with_retry(c.text)
        hyq        = _generate_hyq(c.text)
        if hyq:
            print(f"  [HyQ] Chunk {i+1}: {len(hyq)} questions")
        embeddings.append({
            'chunk_index':   c.chunk_index,
            'text':          c.text,
            'embedding':     embedding,
            'token_count':   c.token_count,
            'page':          c.page,
            'source_key':    c.source_key,
            'hyq_questions': ' | '.join(hyq),
            'section':       getattr(c, 'section', '') or '',
        })
    total_hyq = sum(1 for e in embeddings if e['hyq_questions']) * 3
    print(f"[6/7] Done — {len(embeddings)} embeddings | ~{total_hyq} HyQ questions")
    return {**state, "embeddings": embeddings}


# ── Node 7: Store ─────────────────────────────────────────────────────────────

def node_store(state: IngestionState) -> IngestionState:
    client = get_opensearch_client()
    object_key = state['object_key']

    # Delete stale chunks
    try:
        response = client.delete_by_query(
            index='rag-chunks',
            body={"query": {"term": {"source_key": object_key}}}
        )
        deleted = response.get('deleted', 0)
        if deleted:
            print(f"[OpenSearch] Deleted {deleted} stale chunks for {object_key}")
        else:
            print(f"[OpenSearch] No stale chunks (first ingestion)")
    except Exception as e:
        print(f"[OpenSearch] Warning: could not delete old chunks: {e}")

    # Index new chunks
    for item in state['embeddings']:
        client.index(index='rag-chunks', body={
            'text':          item['text'],
            'embedding':     item['embedding'],
            'source_key':    item['source_key'],
            'page':          item['page'],
            'chunk_index':   item['chunk_index'],
            'hyq_questions': item['hyq_questions'],
            'section':       item['section'],
        })
    print(f"[7/7] Stored {len(state['embeddings'])} chunks in OpenSearch")

    # Save metadata
    _save_metadata(object_key, state['file_type'],
                   len(state['blocks']), len(state['chunks']),
                   'completed', state['content_hash'])

    # Backup chunks JSON to S3
    output_key  = f"chunks/{object_key}.json"
    chunks_data = [c.__dict__ for c in state['chunks']]
    s3_client.put_object(
        Bucket=PROCESSED_BUCKET,
        Key=output_key,
        Body=json.dumps(chunks_data, indent=2),
        ContentType='application/json'
    )
    print(f"[S3] Backed up to s3://{PROCESSED_BUCKET}/{output_key}")

    # Summary
    print("-" * 60)
    print(f"PIPELINE SUMMARY")
    print(f"  File   : {object_key}")
    print(f"  Type   : {state['file_type']}")
    print(f"  Blocks : {len(state['blocks'])}")
    print(f"  Chunks : {len(state['chunks'])}")
    print(f"  Hash   : {state['content_hash'][:20]}...")
    print(f"  Status : completed ✅")
    print("-" * 60)

    return {**state, "status": "completed"}


# ── Routing ───────────────────────────────────────────────────────────────────
# This is what makes it LangGraph instead of a plain chain.
# After duplicate check and detect, we branch instead of continuing blindly.

def route_after_duplicate(state: IngestionState) -> str:
    return END if state.get("status") == "duplicate" else "node_detect"

def route_after_detect(state: IngestionState) -> str:
    return END if state.get("status") == "rejected" else "node_parse"

def route_after_parse(state: IngestionState) -> str:
    return END if state.get("status") == "failed" else "node_chunk"


# ── Build Graph ───────────────────────────────────────────────────────────────

def build_ingestion_graph():
    graph = StateGraph(IngestionState)

    graph.add_node("node_download",         node_download)
    graph.add_node("node_check_duplicate",  node_check_duplicate)
    graph.add_node("node_detect",           node_detect)
    graph.add_node("node_parse",            node_parse)
    graph.add_node("node_chunk",            node_chunk)
    graph.add_node("node_embed",            node_embed)
    graph.add_node("node_store",            node_store)

    graph.set_entry_point("node_download")

    graph.add_edge("node_download",        "node_check_duplicate")
    graph.add_conditional_edges("node_check_duplicate", route_after_duplicate,
                                 {"node_detect": "node_detect", END: END})
    graph.add_conditional_edges("node_detect", route_after_detect,
                                 {"node_parse": "node_parse", END: END})
    graph.add_conditional_edges("node_parse", route_after_parse,
                                 {"node_chunk": "node_chunk", END: END})
    graph.add_edge("node_chunk",  "node_embed")
    graph.add_edge("node_embed",  "node_store")
    graph.add_edge("node_store",  END)

    return graph.compile()

ingestion_graph = build_ingestion_graph()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _save_metadata(object_key, file_type, blocks, chunks, status, content_hash, error=None):
    dynamodb.Table(METADATA_TABLE).put_item(Item={
        'document_key':  object_key,
        'document_id':   object_key,
        'status':        status,
        'file_type':     file_type,
        'blocks':        blocks,
        'chunks':        chunks,
        'timestamp':     datetime.now(timezone.utc).isoformat(),
        'content_hash':  content_hash,
        'error':         error or 'none'
    })
    print(f"[DynamoDB] Saved | {object_key} | {status}")


# ── Lambda Handler ────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    print("=" * 60)
    print("RAG Ingestion Pipeline — triggered")
    print(f"Records in batch: {len(event['Records'])}")
    print("=" * 60)

    processed, failed, skipped = [], [], []

    for record in event['Records']:
        try:
            body = json.loads(record['body'])

            if 'Event' in body and body['Event'] == 's3:TestEvent':
                print("[SQS] Skipping S3 test event")
                continue

            s3_event    = body['Records'][0]
            bucket_name = s3_event['s3']['bucket']['name']
            object_key  = unquote_plus(s3_event['s3']['object']['key'])
            file_size   = s3_event['s3']['object'].get('size', 0)

            print(f"[SQS] Bucket: {bucket_name} | File: {object_key} | Size: {file_size:,} bytes")

            final_state = ingestion_graph.invoke({
                "bucket_name":  bucket_name,
                "object_key":   object_key,
                "file_bytes":   None,
                "content_hash": None,
                "file_type":    None,
                "blocks":       None,
                "chunks":       None,
                "embeddings":   None,
                "status":       None,
                "skip_reason":  None,
                "error":        None,
            })

            status = final_state.get("status")
            if status in ("duplicate", "rejected", "failed"):
                skipped.append(object_key)
            else:
                processed.append(object_key)

        except Exception as e:
            print(f"[ERROR] {str(e)}")
            failed.append(str(e))
            raise

    print("=" * 60)
    print(f"BATCH COMPLETE | processed: {len(processed)} | skipped: {len(skipped)} | failed: {len(failed)}")
    print("=" * 60)

    return {
        'statusCode': 200,
        'processed':  len(processed),
        'skipped':    len(skipped),
        'failed':     len(failed)
    }