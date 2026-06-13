# Production-Grade RAG Pipeline on AWS — Project Summary v7
## New Thread Carry-Forward (Updated: June 1, 2026)

---

## Quick Reference

| Item | Value |
|---|---|
| **Active branch** | `feature/langchain-langgraph` |
| **Demo 2 date** | June 4, 2026 (query pipeline) |
| **Live URL** | https://d2hsci73dlmw9w.cloudfront.net |
| **AWS Profile** | `rag-dev` / Account `911414006786` / Region `us-east-1` |
| **LangSmith project** | `rag-pipeline` |
| **Eval dataset** | `johnson-v-superior-court-eval` (8 Q&A pairs) |
| **Latest eval scores** | faithfulness 1.00 · correctness 0.50 · relevance 0.62 · context_precision 0.45 |
| **OpenSearch** | ⚠️ DELETE when not using (~$50–100/month) |

---

## 1. Project Overview

**Goal:** Build a production-grade RAG pipeline POC on AWS with deep understanding of every design decision.

**Developer:** Sudheer Cherukuri  
**Stack:** Python 3.11 · LangChain/LangGraph · AWS Bedrock · OpenSearch · Lambda · React frontend

### Bedrock Models
| Model | Used for |
|---|---|
| `amazon.titan-embed-text-v1` | Embeddings (1536 dimensions) |
| `amazon.nova-micro-v1:0` | HyQ generation + HyDE + answer generation |
| `cohere.rerank-v3-5:0` | Reranking (cross-encoder) |

---

## 2. AWS Resources

```
S3:
  rag-raw-911414006786           ← PDF uploads
  rag-processed-911414006786     ← chunks JSON + Lambda deployment zips
  rag-frontend-911414006786      ← React static files

SQS:
  rag-ingestion-queue            ← S3 event notifications
  rag-ingestion-dlq              ← dead letter queue
  ⚠️ Visibility Timeout must be 300s (re-set after every recreation)
  ⚠️ DLQ has 2 messages (intentional for demo — alarm in red)

Lambda:
  rag-document-processor         ← ingestion (512MB, 300s timeout)
  rag-query                      ← query (512MB, 30s timeout)
  Handlers: ingestion_graph.lambda_handler / query_graph.lambda_handler
  IAM Role: arn:aws:iam::911414006786:role/service-role/rag-document-processor-role-ucg8h778
  ⚠️ Both Lambdas share the same IAM role
  ⚠️ Deploy via S3 (package >70MB exceeds direct upload limit)
  ⚠️ X-Ray active tracing enabled on both
  ⚠️ OTEL_TRACES_EXPORTER=awsxray must be set on both

DynamoDB:
  Table: rag-metadata
  Partition Key: document_id
  GSI: content-hash-index (on content_hash)
  Fields: document_id, content_hash, file_type, blocks, chunks, status, error, timestamp

OpenSearch:
  Domain: rag-vectors
  Endpoint: search-rag-vectors-xzipwfal5kzob35v4x7uzpnz7i.us-east-1.es.amazonaws.com
  Index: rag-chunks
  Engine: FAISS / HNSW / cosine similarity
  Search Pipeline: hybrid-search-pipeline (BM25 + KNN, min_max normalization)
  ⚠️ run create_index.py BEFORE uploading any PDF
  ⚠️ run setup_hybrid_search.py after recreating
  ⚠️ Re-map IAM role in Dashboards: Security → Roles → all_access → Mapped users → Backend roles
      Use IAM role ARN (NOT Lambda function ARN)

API Gateway:
  POST /query      → rag-query Lambda (API key required)
  POST /v1/query   → rag-query Lambda (API key required)
  GET  /health     → rag-query Lambda (no API key, public)
  API Key: gPEWpbTVLq9NPD0YJYD4pCszNr7UcF4ln1etxZd0
  Usage Plan: rag-usage-plan-prod (10 req/sec, 1000/month)

Cognito:
  User Pool: us-east-1_carOA35Jg
  App Client: rag-query-app (ID: 6m9qrv1v2r1oq77i874hdph5nb)
  Grant Type: Implicit grant / Scopes: email, openid

CloudFront:
  Distribution ID: E1EG5P75323N5P

CloudWatch:
  Alarm: rag-dlq-depth-alarm (fires when DLQ ≥ 1 → SNS → email)
  Dashboard: rag-pipeline-dashboard
  ⚠️ Dashboard widgets were LOST — must be rebuilt before June 4 demo (see Section 7)

Secrets Manager:  rag/opensearch/credentials
Parameter Store:
  /rag/opensearch/endpoint
  /rag/langsmith/api-key (SecureString)

LangSmith:
  URL: https://smith.langchain.com
  Lambda env vars: LANGCHAIN_TRACING_V2=true · LANGCHAIN_API_KEY · LANGCHAIN_PROJECT=rag-pipeline
  Eval experiment: johnson-eval-60622841
```

---

## 3. Key Source Files

| File | Role |
|---|---|
| `query_graph.py` | Query Lambda handler + full LCEL pipeline |
| `ingestion_graph.py` | Ingestion Lambda handler + LangGraph nodes |
| `langsmith_eval.py` | 4-evaluator LangSmith eval script |
| `llm_clients.py` | lru_cached Bedrock Nova + Titan clients |
| `opensearch_client.py` | AWS4Auth OpenSearch client (lru_cached) |
| `config.py` | Reads Parameter Store for OpenSearch endpoint |
| `parsers.py` | Custom paragraph-level PDF parsing |
| `detector.py` | Magic-byte file type detection |
| `chunker.py` | 448-token chunks with 64-token overlap |
| `create_index.py` | Creates OpenSearch index with knn_vector mapping |
| `setup_hybrid_search.py` | Creates hybrid-search-pipeline in OpenSearch |
| `langsmith_eval.py` | Evaluation runner (4 metrics) |
| `reset_document.py` | Clears DynamoDB hash so PDF can be re-ingested |

---

## 4. Architecture

### Ingestion Pipeline (LangGraph)
```
PDF Upload → S3 → SQS → rag-document-processor Lambda
  node_download       → S3 to /tmp/
  node_check_duplicate→ SHA-256 → DynamoDB GSI (branch: duplicate → END)
  node_detect         → magic bytes (branch: unknown → rejected → END)
  node_parse          → paragraph-level blocks
  node_chunk          → 448 tokens / 64 overlap
  node_hyq            → Nova Micro generates 3 hypothetical questions per chunk
  node_embed          → Titan embeds chunk text → 1536-dim vector
  node_store          → OpenSearch + DynamoDB
```

### Query Pipeline (LangChain LCEL)
```
React UI → Cognito JWT → API Gateway → rag-query Lambda
  Input validation (type, length 3–1000 chars)
  run_hyde    → Nova generates hypothetical answer → Titan embeds → hyde_vector
  run_search  → Hybrid: BM25 on text+hyq_questions (raw question) + KNN on embedding (hyde_vector)
  run_rerank  → Cohere cross-encoder → top 5
  run_dedup   → remove duplicate pages
  run_answer  → token budget (2000 tokens) → Nova → grounded answer + sources
  → JSON response → React renders answer + citations
```

---

## 5. Query Pipeline — Deep Dive

### LCEL Pipe Syntax
```python
hyde_chain = hyde_prompt | get_llm() | StrOutputParser()
query_pipeline = (
    RunnableLambda(run_hyde)
    | RunnableLambda(run_search)
    | RunnableLambda(run_rerank)
    | RunnableLambda(run_dedup)
    | RunnableLambda(run_answer)
)
```
The same `state` dict travels through all steps, each adding new keys:
`{question}` → `+hyde_text` → `+chunks(10)` → `chunks(5, reranked)` → `chunks(deduped)` → `+answer`

### Why Each Step Exists

| Step | Reason |
|---|---|
| HyDE | Question vector ≠ answer vector in high-dim space; hypothetical answer bridges the gap |
| Hybrid BM25+KNN | BM25 catches exact legal terms; KNN catches semantic meaning |
| BM25 on `hyq_questions^2.0` | Question-to-question match is a stronger signal — deserves 2× weight |
| Cohere rerank | Cross-encoder sees Q+chunk together; bi-encoder (KNN) scores them independently |
| Dedup by page | Prevents same source page from dominating context window |
| Token budget 2000 | Caps Nova context → bounded cost and latency |

### OpenSearch Index Fields
```
text           → paragraph text (BM25)
embedding      → 1536-dim Titan vector (KNN)
hyq_questions  → 3 generated questions per chunk (BM25, 2× weight)
source_key     → S3 filename
page           → page number
section        → detected section heading
chunk_index    → position in document
```

### Cohere Rerank — Cross-Encoder vs Bi-Encoder
```
Bi-encoder (KNN):   Question → Vector A  ┐  compared separately
                    Chunk    → Vector B  ┘
Cross-encoder:      [Question + Chunk together] → Relevance score  ← more accurate
```
Cross-encoder can't replace KNN for full-corpus search (too slow), so: OpenSearch narrows to 10 → Cohere deeply judges those 10.

---

## 6. LangSmith Observability & Evaluation

### What LangSmith Does
```
LangSmith = storage + analytics + experiment tracking
Nova Micro = does the actual judging (LLM-as-a-Judge)
```
LangSmith does NOT evaluate. It records scores your evaluator functions produce.

### Tracing — Per-step visibility
```
run_hyde    → input: question, output: hyde_text, latency, tokens
run_search  → output: 10 chunks, latency
run_rerank  → output: 5 chunks, latency
run_dedup   → output: unique chunks
run_answer  → input: context+question, output: answer, tokens
```

**Cold start insight:**
```
run_hyde    94 tokens  → 1.42s  (FIRST Bedrock call — pays HTTPS setup cost)
run_answer  524 tokens → 0.63s  (SECOND call — reuses warm connection)
```

### The 4 Evaluators

| Evaluator | What it measures | Needs chunks? |
|---|---|---|
| `faithfulness` | Did Nova hallucinate? | ✅ Yes |
| `answer_relevance` | Did answer address the question? | ❌ No |
| `answer_correctness` | Was the answer factually right? | ❌ No |
| `context_precision` | Were retrieved chunks useful? | ✅ Yes |

```
context_precision  → question + chunks              (retrieval layer)
faithfulness       → question + chunks + answer     (retrieval→generation boundary)
answer_relevance   → question + answer              (generation layer)
answer_correctness → question + answer + expected   (end-to-end)
```

### Evaluation Results (May 30, 2026)
```
faithfulness      1.00  ← Nova never hallucinated (8/8)
answer_relevance  0.62
answer_correctness 0.50
context_precision  0.45
```

**Key insight:** Faithfulness 1.00 + correctness 0.50 = **retrieval problem, not generation problem**.
For 4 questions, wrong chunks were retrieved → Nova correctly said "I cannot find this" → scored 0 on correctness. Nova did the right thing — it had nothing to work with.

### Code Change Made (query_graph.py)
Added `context` field to API response so evaluators can access chunk texts:
```python
'context': [c['text'] for c in state["chunks"]],   # added for faithfulness + context_precision
```

---

## 7. X-Ray Tracing

**Fix applied:** Added `OTEL_TRACES_EXPORTER=awsxray` to both Lambdas.

**What you can see:**
```
CloudWatch → Application Signals → Trace Map
Client → rag-query (Lambda Context) → rag-query (Lambda Function)

Lambda Context:  11.63s  (cold start + code)
Lambda Function:  6.32s  (just lambda_handler)
Cold start cost:  5.31s  (AWS container init overhead)
```

### Three-Layer Observability
```
CloudWatch Dashboard  → "Is the system up and healthy?"   (infra metrics)
X-Ray Trace Map       → "Where does the time go at AWS level?"  (cold start, service boundaries)
LangSmith Tracing     → "What did each AI step do?"  (per-step latency, tokens, I/O)
LangSmith Evaluation  → "How good are the answers and why?"  (4 metrics)
```

**One-liner for demo:**
> *"CloudWatch tells me my infrastructure is healthy. X-Ray tells me where the time goes across AWS services. LangSmith tells me what my AI pipeline did with that time — and whether the answers were good."*

---

## 8. Things To Do Before June 4 Demo

| Priority | Task | Status |
|---|---|---|
| 🔴 High | Rebuild CloudWatch dashboard (5 widgets — see below) | ❌ Not done |
| 🔴 High | Practice demo narrative out loud | ❌ Not done |
| 🟡 Medium | Verify X-Ray traces appear after a fresh query | ✅ Working |
| 🟡 Medium | Confirm LangSmith experiment scores visible | ✅ Working |
| 🟢 Low | Consider increasing top_k to 15 to improve context_precision | Optional |

### CloudWatch Dashboard Widgets to Rebuild
| Widget | Metric | Lambda |
|---|---|---|
| Invocation Count | Lambda → Invocations | rag-query + rag-document-processor |
| Error Rate | Lambda → Errors | rag-query + rag-document-processor |
| Duration p50/p99 | Lambda → Duration | rag-query |
| Throttles | Lambda → Throttles | rag-query |
| DLQ Depth | SQS → ApproximateNumberOfMessagesVisible | rag-ingestion-dlq |

---

## 9. Demo Narrative (June 4)

**Show in order:**
1. `rag-pipeline-dashboard` → 5 widgets → DLQ Depth = 2, alarm in red → failure handling story
2. `GET /health` → `{"status": "degraded", "opensearch": "unreachable"}`
3. Start OpenSearch → `/health` again → `{"status": "healthy"}`
4. Upload PDF → S3 → watch Lambda logs (JSON structured) → `Status: completed ✅`
5. LangSmith → ingestion trace → 7 nodes, 3 branches
6. Ask question via live demo URL
7. LangSmith → query trace → run_hyde, run_search, run_rerank, run_answer with latencies
8. X-Ray → full execution trace, cold start cost
9. CloudWatch dashboard → metrics updating live
10. Ask question about second document (Raceway Ford) → cross-document retrieval
11. LangSmith evaluator results → scores table

**Key talking points:**
- "Every design decision has a reason — HyDE, HyQ, hybrid search, Cohere rerank"
- "Faithfulness 1.00 means Nova never hallucinated — every claim came from the retrieved chunks"
- "Correctness 0.50 + context_precision 0.45 together tell me the problem is retrieval, not generation"
- "X-Ray separates cold start (5.3s) from actual code execution (6.3s) — in production you'd eliminate cold start with provisioned concurrency"
- "LangSmith gives me application-level observability; X-Ray gives me infrastructure-level"
- "Silent degradation is not safe — DLQ alarm fires within 1 minute of any ingestion failure"
- "Input validation rejects bad inputs before any Bedrock or OpenSearch call is made"
- "Token budget 2000 caps context size — no runaway costs, no prompt explosions"

---

## 10. Deploy Commands

### Query Lambda
```powershell
Remove-Item query_package.zip -ErrorAction SilentlyContinue
Compress-Archive -Path query_graph.py, query_handler.py, models.py, config.py, llm_clients.py, opensearch_client.py -DestinationPath query_package.zip
Compress-Archive -Path .\package\* -Update -DestinationPath query_package.zip
aws s3 cp query_package.zip s3://rag-processed-911414006786/deployments/query_package.zip --profile rag-dev
aws lambda update-function-code --function-name rag-query --s3-bucket rag-processed-911414006786 --s3-key deployments/query_package.zip --profile rag-dev
```

### Ingestion Lambda
```powershell
Remove-Item ingestion_package.zip -ErrorAction SilentlyContinue
Compress-Archive -Path ingestion_graph.py, detector.py, parsers.py, chunker.py, opensearch_client.py, llm_clients.py, config.py -DestinationPath ingestion_package.zip
Compress-Archive -Path .\package\* -Update -DestinationPath ingestion_package.zip
aws s3 cp ingestion_package.zip s3://rag-processed-911414006786/deployments/ingestion_package.zip --profile rag-dev
aws lambda update-function-code --function-name rag-document-processor --s3-bucket rag-processed-911414006786 --s3-key deployments/ingestion_package.zip --profile rag-dev
```

### Recreate OpenSearch (full checklist)
```powershell
# 1. Map IAM role in Dashboards (NOT Lambda function ARN):
#    Security → Roles → all_access → Mapped users → Backend roles → add:
#    arn:aws:iam::911414006786:role/service-role/rag-document-processor-role-ucg8h778

# 2. Create index (MUST do before any PDF upload)
python create_index.py

# 3. Create hybrid search pipeline
python setup_hybrid_search.py

# 4. Set SQS visibility timeout
aws sqs set-queue-attributes `
  --queue-url "https://sqs.us-east-1.amazonaws.com/911414006786/rag-ingestion-queue" `
  --attributes VisibilityTimeout=300 --profile rag-dev

# 5. Update Parameter Store if endpoint changed
aws ssm put-parameter --name "/rag/opensearch/endpoint" --value "NEW-ENDPOINT" `
  --type String --overwrite --profile rag-dev

# 6. Reset document hashes and re-upload PDFs
python reset_document.py --file johnson_v._superior_court_1.pdf
python reset_document.py --file raceway_ford_cases.pdf
aws s3 cp "C:\Tech\PROD-Grade-POC\Dataset\johnson_v._superior_court_1.pdf" s3://rag-raw-911414006786/ --profile rag-dev
aws s3 cp "C:\Tech\PROD-Grade-POC\Dataset\raceway_ford_cases.pdf" s3://rag-raw-911414006786/ --profile rag-dev
```

### Other Useful Commands
```powershell
# Watch logs
aws logs tail /aws/lambda/rag-document-processor --follow --profile rag-dev

# Test endpoints
Invoke-WebRequest -Uri "https://k0injz47nk.execute-api.us-east-1.amazonaws.com/prod/query" `
  -Method POST `
  -Headers @{"x-api-key"="gPEWpbTVLq9NPD0YJYD4pCszNr7UcF4ln1etxZd0"; "Content-Type"="application/json"} `
  -Body '{"question":"What was Gregory Johnson convicted of?"}' -UseBasicParsing

(Invoke-WebRequest -Uri "https://k0injz47nk.execute-api.us-east-1.amazonaws.com/prod/health" -UseBasicParsing).Content

# DLQ check
aws sqs get-queue-attributes `
  --queue-url "https://sqs.us-east-1.amazonaws.com/911414006786/rag-ingestion-dlq" `
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --profile rag-dev

# Other scripts
python langsmith_eval.py
python reset_document.py --list
python check_mapping.py
$env:AWS_PROFILE = "rag-dev"
```

---

## 11. Cost Breakdown

```
OpenSearch:    ~$50–100/month  ← DELETE when not using!
Bedrock Titan: ~$0.50/month
Bedrock Nova:  ~$1–2/month
Cohere Rerank: ~$0.001/query
Secrets Mgr:   ~$0.80/month
Everything else (Lambda/S3/SQS/DynamoDB/API GW/Cognito/CloudFront/CW/X-Ray): ~$0 (free tier)
TOTAL:         ~$52–104/month
```
