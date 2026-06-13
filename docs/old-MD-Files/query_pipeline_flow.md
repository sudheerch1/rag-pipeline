# Query Pipeline — End-to-End Data Flow
## "What was Gregory Johnson convicted of?"

---

## Overview

```
React UI (CloudFront)
  → Cognito (JWT)
  → API Gateway (API Key + JWT)
  → Lambda: query_graph.lambda_handler
      → Input Validation
      → run_hyde       ← HyDE: generate hypothetical answer → embed it
      → run_search     ← Hybrid Search: BM25 + KNN on OpenSearch
      → run_rerank     ← Cohere: cross-encoder reranking → best 5
      → run_dedup      ← deduplicate by page number
      → run_answer     ← Token budget → Nova Micro → grounded answer
  → JSON response (answer + sources)
  → LangSmith trace (every step named, timed, token-counted)
```

---

## Step 0 — React UI (CloudFront)

**File:** React frontend (`rag-frontend` repo)  
**URL:** `https://d2hsci73dlmw9w.cloudfront.net`

The user types a question in the search box and hits Submit.

```
User input:  "What was Gregory Johnson convicted of?"
```

Before the API call, the frontend does two things:

**Cognito authentication** — The user already logged in via the Cognito Hosted UI, so the browser has a JWT (ID token) in `localStorage`. The React app attaches it as a header.

**HTTP POST** constructed:
```http
POST https://k0injz47nk.execute-api.us-east-1.amazonaws.com/prod/query
Headers:
  x-api-key:     gPEWpbTVLq9NPD0YJYD4pCszNr7UcF4ln1etxZd0
  Authorization: Bearer eyJraWQiO...  (Cognito JWT)
  Content-Type:  application/json

Body:
{
  "question": "What was Gregory Johnson convicted of?"
}
```

---

## Step 1 — API Gateway

**AWS Resource:** REST API `k0injz47nk`, stage `prod`, route `POST /query`

API Gateway does three things in order:

1. **API Key check** — validates `x-api-key` against the usage plan (`rag-usage-plan-prod`, 10 req/sec, 1000/month). Rejects with 403 if missing or wrong.
2. **JWT validation** — validates the `Authorization` Bearer token against the Cognito User Pool (`us-east-1_carOA35Jg`). Rejects with 401 if expired or tampered.
3. **Lambda proxy integration** — packages the full request (headers, body, path, method) into a Lambda event and invokes `rag-query`.

Lambda event payload handed to `query_graph.lambda_handler`:
```json
{
  "httpMethod": "POST",
  "path": "/query",
  "headers": { "x-api-key": "...", "Authorization": "Bearer ..." },
  "body": "{\"question\": \"What was Gregory Johnson convicted of?\"}"
}
```

---

## Step 2 — Lambda Entry Point

**File:** `query_graph.py`  
**Function:** `lambda_handler(event, context)`

This is the Lambda handler. It does two things immediately:

**Parse the body:**
```python
body = json.loads(event.get("body", "{}"))
question = body.get("question", "")
# question = "What was Gregory Johnson convicted of?"
```

**Route to health check if GET /health:**
```python
if event.get("httpMethod") == "GET" and event.get("path") == "/health":
    return health_check()
```
Not a health check here, so we continue.

---

## Step 3 — Input Validation

**File:** `query_graph.py`  
**Function:** Inline validation block inside `lambda_handler`

```python
if not isinstance(question, str):       # type check
    return 400, "question must be a string"

question = question.strip()             # strip whitespace

if len(question) < 3:                   # too short
    return 400, "question too short"

if len(question) > 1000:               # too long
    return 400, "question too long"
```

Our question:
```
"What was Gregory Johnson convicted of?"   → 39 chars, passes all checks
```

After validation, the question is passed into the LangChain LCEL pipeline.

---

## Step 4 — `run_hyde` — HyDE (Hypothetical Document Embedding)

**File:** `query_graph.py`  
**Function:** `run_hyde(question: str) → np.ndarray`  
**LLM used:** `amazon.nova-micro-v1:0` (via `llm_clients.py`)  
**Timing:** ~0.98s

**Why HyDE?** Real user questions are short and sparse. Real answer passages are long and dense. A question like "What was Gregory Johnson convicted of?" lives in a very different region of the 1536-dim vector space than the actual paragraph that answers it. HyDE bridges that gap by generating a *hypothetical answer* first, then embedding that answer. An answer embedding is geometrically closer to real answer chunks than the question itself.

**What happens:**

Nova Micro is called with a prompt like:
```
You are a helpful assistant. Given this question, write a short 2-3 sentence
hypothetical answer passage as if it came from a legal document:

Question: What was Gregory Johnson convicted of?
```

Nova returns something like:
```
"Gregory Johnson was convicted of flag desecration under a Texas statute
that prohibited the desecration of venerated objects. The conviction arose
from his burning of an American flag during a political protest."
```

That hypothetical answer is then embedded via LangChain `BedrockEmbeddings`:
```python
hyde_vector = embeddings.embed_query(hypothetical_answer)
# Returns: [0.0231, -0.0847, 0.1203, ...] — 1536 float values
```

**Output:** `hyde_vector` — a 1536-dim numpy array representing a *hypothetical answer*, not the raw question.

---

## Step 5 — `run_search` — Hybrid Search (BM25 + KNN)

**File:** `query_graph.py` + `opensearch_client.py`  
**Function:** `run_search(question: str, hyde_vector: np.ndarray) → List[dict]`  
**AWS Resource:** OpenSearch domain `rag-vectors`, index `rag-chunks`, search pipeline `hybrid-search-pipeline`  
**Timing:** ~1.90s

**Why hybrid search?** BM25 is keyword-based — great for exact legal terms ("flag desecration", "Texas statute"). KNN is semantic — great for meaning even when words differ. Using both catches what either alone would miss.

**What happens:**

OpenSearch is called with a hybrid query that combines both:

```python
query_body = {
  "query": {
    "hybrid": {
      "queries": [
        {
          "match": {
            "text": {
              "query": "What was Gregory Johnson convicted of?"  # BM25
            }
          }
        },
        {
          "knn": {
            "embedding": {
              "vector": hyde_vector,    # the HyDE vector
              "k": 10
            }
          }
        }
      ]
    }
  },
  "search_pipeline": "hybrid-search-pipeline"  # min_max normalization
}
```

The `hybrid-search-pipeline` (created by `setup_hybrid_search.py`) normalizes BM25 and KNN scores separately using min-max normalization, then combines them. This ensures neither drowns out the other.

**OpenSearch authentication** (`opensearch_client.py`):
```python
auth = AWS4Auth(credentials, region, "es")   # IAM role-based, not username/password
client = OpenSearch(hosts=[endpoint], http_auth=auth, ...)
```

OpenSearch returns top 10 chunks. Each chunk looks like:
```json
{
  "_score": 0.94,
  "_source": {
    "text": "Gregory Johnson was charged with flag desecration under Texas Penal Code §42.09(a)(3)...",
    "document_id": "johnson_v._superior_court_1.pdf",
    "page": 3,
    "chunk_id": "johnson_..._chunk_12",
    "hyq_questions": [
      "What statute was Johnson charged under?",
      "What did Johnson do to the flag?",
      "What was the legal basis for Johnson's arrest?"
    ]
  }
}
```

The `hyq_questions` field matters here — these were embedded at ingestion time (3 questions per chunk via Nova Micro). OpenSearch's KNN search matched the HyDE vector against **both** the chunk `embedding` field and the proximity of question-to-question similarity. Question-to-question matching is geometrically stronger than question-to-chunk-text.

**Output:** List of 10 candidate chunks with scores.

---

## Step 6 — `run_rerank` — Cohere Reranking

**File:** `query_graph.py`  
**Function:** `run_rerank(question: str, chunks: List[dict]) → List[dict]`  
**Model:** `cohere.rerank-v3-5:0` (direct boto3 call — no LangChain wrapper yet)  
**Timing:** ~0.15s

**Why rerank?** BM25+KNN scores are computed independently for each chunk — the model never sees the question and chunk *together*. Cohere Rerank is a cross-encoder: it reads the question AND the chunk together in one pass, producing a much more accurate relevance score. Think of it as a second-pass judge that re-orders the shortlist with full context.

**What happens:**

The 10 chunks from OpenSearch are sent to Cohere Rerank:
```python
response = bedrock_client.invoke_model(
    modelId="cohere.rerank-v3-5:0",
    body=json.dumps({
        "query": "What was Gregory Johnson convicted of?",
        "documents": [chunk["text"] for chunk in chunks],  # 10 texts
        "top_n": 5
    })
)
```

Cohere scores each (question, chunk) pair together and returns the top 5, re-ordered by cross-encoder relevance.

**Output:** Top 5 chunks, re-ordered by Cohere's cross-encoder score.

---

## Step 7 — `run_dedup` — Deduplicate by Page

**File:** `query_graph.py`  
**Function:** `run_dedup(chunks: List[dict]) → List[dict]`  
**Timing:** ~0.00s (pure Python, no I/O)

**Why dedup?** Multiple chunks can come from the same page — adjacent paragraphs that both discuss Johnson's conviction. If two chunks from page 3 both pass reranking, the final answer would repeat the same source context. This step ensures at most one chunk per page is kept.

```python
seen_pages = set()
deduped = []
for chunk in reranked_chunks:
    page = chunk["page"]
    if page not in seen_pages:
        seen_pages.add(page)
        deduped.append(chunk)
```

**Output:** Deduplicated chunks (still up to 5, but guaranteed unique pages).

---

## Step 8 — `run_answer` — Token Budget + Nova Answer Generation

**File:** `query_graph.py`  
**Function:** `run_answer(question: str, chunks: List[dict]) → dict`  
**LLM used:** `amazon.nova-micro-v1:0`  
**Config:** `CONTEXT_TOKEN_BUDGET = 2000` tokens  
**Timing:** depends on answer length

**Why token budget?** Nova Micro has a context window limit. More importantly, passing 5 full chunks unbounded can balloon the prompt to thousands of tokens, increasing latency and cost. The token budget caps the total context passed to Nova at 2000 tokens (~8000 chars).

**What happens:**

First, the chunks are assembled into a context string, trimmed to budget:
```python
context_parts = []
token_count = 0
for chunk in deduped_chunks:
    chunk_tokens = len(chunk["text"]) // 4  # ~4 chars per token
    if token_count + chunk_tokens > CONTEXT_TOKEN_BUDGET:
        break
    context_parts.append(chunk["text"])
    token_count += chunk_tokens

context = "\n\n".join(context_parts)
```

Then Nova Micro is called:
```python
prompt = f"""You are a helpful assistant. Answer the question using ONLY the context below.
If the answer is not in the context, say "I cannot find this information in the documents."

Context:
{context}

Question: {question}

Answer:"""
```

Nova returns:
```
"Gregory Johnson was convicted of flag desecration under Texas Penal Code
§42.09(a)(3), which prohibited the desecration of venerated objects. His
conviction stemmed from burning an American flag during a 1984 political
protest in Dallas."
```

The sources are also assembled:
```python
sources = [
    {"document": chunk["document_id"], "page": chunk["page"]}
    for chunk in deduped_chunks
]
```

**Output:**
```json
{
  "answer": "Gregory Johnson was convicted of flag desecration under Texas Penal Code §42.09(a)(3)...",
  "sources": [
    { "document": "johnson_v._superior_court_1.pdf", "page": 3 },
    { "document": "johnson_v._superior_court_1.pdf", "page": 7 }
  ]
}
```

---

## Step 9 — Response Back to React

Lambda returns to API Gateway:
```python
return {
    "statusCode": 200,
    "headers": { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
    "body": json.dumps({
        "answer": "Gregory Johnson was convicted of flag desecration...",
        "sources": [...]
    })
}
```

API Gateway forwards the response to the React frontend via CloudFront HTTPS. React renders the answer and source citations in the UI.

---

## Step 10 — LangSmith Trace (Observability)

Every step above is automatically traced in LangSmith because `LANGCHAIN_TRACING_V2=true` is set as a Lambda env var. The trace for this query looks like:

```
Query Run
  ├── run_hyde       input="What was Gregory Johnson..."  output=[0.023, ...]  latency=0.98s  tokens=87
  ├── run_search     input=hyde_vector                   output=10 chunks      latency=1.90s
  ├── run_rerank     input=10 chunks                     output=5 chunks       latency=0.15s
  ├── run_dedup      input=5 chunks                      output=4 chunks       latency=0.00s
  └── run_answer     input=question+context              output=answer+sources latency=1.2s   tokens=312
```

X-Ray shows the infrastructure layer: Lambda cold start duration, API Gateway latency, Bedrock call duration, OpenSearch network roundtrip.

---

## Complete Function Map

| Step | Function | File | What it does |
|---|---|---|---|
| Entry | `lambda_handler` | `query_graph.py` | Parses event, routes to health or query |
| Validation | inline block | `query_graph.py` | Type check, strip, min 3 / max 1000 chars |
| Step 4 | `run_hyde` | `query_graph.py` | Nova generates hypothetical answer → embed → 1536-dim vector |
| Step 5 | `run_search` | `query_graph.py` + `opensearch_client.py` | BM25 + KNN hybrid search → top 10 chunks |
| Step 6 | `run_rerank` | `query_graph.py` | Cohere cross-encoder → top 5 re-ordered |
| Step 7 | `run_dedup` | `query_graph.py` | Remove duplicate pages |
| Step 8 | `run_answer` | `query_graph.py` | Token budget trim → Nova → grounded answer + sources |
| Config | `get_opensearch_endpoint` | `config.py` | Reads Parameter Store for OpenSearch URL |
| Auth | `opensearch_client.py` | `opensearch_client.py` | AWS4Auth with IAM role, lru_cached client |
| LLMs | `llm_clients.py` | `llm_clients.py` | lru_cached Bedrock Nova + Titan clients |

---

## Why Each Design Decision Exists (Interview Talking Points)

| Decision | Reason |
|---|---|
| HyDE before KNN | Question vector ≠ answer vector in high-dim space; hypothetical answer bridges the gap |
| Hybrid BM25 + KNN | BM25 catches exact legal terms; KNN catches semantic meaning — both needed |
| Cohere cross-encoder rerank | Bi-encoder (KNN) scores Q and chunk independently; cross-encoder sees them together — more accurate |
| Dedup by page | Prevents same source page from dominating the context window |
| Token budget 2000 | Caps Nova context → bounded cost and latency, no prompt explosions |
| Nova Micro not Pro | Micro sufficient for grounded Q&A over short context; cost ~10x lower |
| lru_cache on clients | Lambda containers reuse across warm invocations — init Bedrock client once, not per request |
| AWS4Auth not username/password | IAM role = STS temporary credentials, auto-rotate, no secrets to leak |
| Input validation at Lambda boundary | Bad inputs rejected before any Bedrock or OpenSearch call — nothing expensive is touched |
