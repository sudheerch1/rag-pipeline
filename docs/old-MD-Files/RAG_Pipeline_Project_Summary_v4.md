# Production-Grade RAG Pipeline on AWS — Updated Project Summary
## For continuing in a new thread (Updated: May 22, 2026)

---

## 1. Project Overview

**Goal:** Build a production-grade RAG (Retrieval Augmented Generation) pipeline on AWS as a POC, with deep understanding of why each component exists.

**Developer:** Sudheer Cherukuri
**OS:** Windows, VSCode, Python 3.11 virtual environment
**AWS Profile:** `rag-dev`
**AWS Account:** 911414006786
**AWS Region:** us-east-1
**GitHub (backend):** github.com/sudheerch1/rag-pipeline
**GitHub (frontend):** github.com/sudheerch1/rag-frontend (private)
**Demo Date:** May 21, 2026 (Ingestion pipeline — DONE ✅)
**Demo 2 Date:** June 4, 2026 (Query pipeline)
**Live Demo URL:** https://d2hsci73dlmw9w.cloudfront.net

---

## 2. Current Git Branch Status

```
main                        ← working v1 code, fallback if migration fails
feature/langchain-langgraph ← active branch, LangChain/LangGraph migration COMPLETE ✅
```

**Commands to switch between branches:**
```powershell
git checkout main                        # go back to working v1
git checkout feature/langchain-langgraph # resume migration
git branch                               # check which branch you are on (* = current)
```

---

## 3. Complete AWS Resources

```
S3 Buckets:
  rag-raw-911414006786           ← PDF/document uploads
  rag-processed-911414006786     ← processed chunks JSON + Lambda deployment zips
  rag-frontend-911414006786      ← React frontend static files

SQS:
  rag-ingestion-queue            ← S3 event notifications
  rag-ingestion-dlq              ← dead letter queue for failures
  ⚠️ Visibility Timeout: 300 seconds (MUST set after every recreation)

Lambda:
  rag-document-processor         ← ingestion pipeline (512MB, 300s timeout)
  rag-query                      ← query pipeline (512MB, 30s timeout)
  Handler: ingestion_graph.lambda_handler   ← UPDATED
  Handler: query_graph.lambda_handler       ← UPDATED
  IAM Role: arn:aws:iam::911414006786:role/service-role/rag-document-processor-role-ucg8h778
  ⚠️ Both Lambdas share the same IAM role
  ⚠️ Deploy via S3 (package >70MB, direct upload limit exceeded)

DynamoDB:
  Table: rag-metadata
  Partition Key: document_id
  GSI: content-hash-index (on content_hash field)
  Fields: document_id, content_hash, file_type, blocks, chunks, status, error, timestamp

OpenSearch:
  Domain: rag-vectors
  Endpoint: search-rag-vectors-xzipwfal5kzob35v4x7uzpnz7i.us-east-1.es.amazonaws.com
  Index: rag-chunks
  Engine: cosine similarity, faiss, HNSW
  Search Pipeline: hybrid-search-pipeline (BM25 + KNN, min_max normalization)
  ⚠️ Delete when not using (~$50-100/month)
  ⚠️ When recreating: run create_index.py BEFORE uploading any PDF

API Gateway:
  URL: https://k0injz47nk.execute-api.us-east-1.amazonaws.com/prod/query
  Type: REST API
  ID: k0injz47nk
  Stage: prod
  Route: POST /query
  API Key Required: True
  API Key: gPEWpbTVLq9NPD0YJYD4pCszNr7UcF4ln1etxZd0
  Usage Plan: rag-usage-plan-prod (10 req/sec, 1000/month)

Cognito:
  User Pool: us-east-1_carOA35Jg
  App Client: rag-query-app (ID: 6m9qrv1v2r1oq77i874hdph5nb)
  Domain: https://us-east-1caroa35jg.auth.us-east-1.amazoncognito.com
  Callback URLs: https://d2hsci73dlmw9w.cloudfront.net, http://localhost:3000
  Grant Type: Implicit grant
  Scopes: email, openid

CloudFront:
  Distribution ID: E1EG5P75323N5P
  Domain: d2hsci73dlmw9w.cloudfront.net
  Origin: S3 website endpoint (HTTP only)

Secrets Manager:
  rag/opensearch/credentials     ← OpenSearch username/password

Parameter Store:
  /rag/opensearch/endpoint       ← OpenSearch domain URL
  /rag/langsmith/api-key         ← LangSmith API key (SecureString)

Bedrock Models Used:
  amazon.titan-embed-text-v1    ← embeddings (1536 dimensions)
  amazon.nova-micro-v1:0        ← HyQ generation + HyDE + answer generation
  cohere.rerank-v3-5:0          ← reranking (cross-encoder)

LangSmith:
  Project: rag-pipeline
  URL: https://smith.langchain.com
  Status: ✅ ACTIVE — traces appearing for every query invocation
  Lambda env vars required:
    LANGCHAIN_TRACING_V2=true
    LANGCHAIN_API_KEY=ls__<from Parameter Store>
    LANGCHAIN_PROJECT=rag-pipeline
```

---

## 4. Architecture

### Ingestion Pipeline (LangGraph)
```
PDF Upload
  → S3 (rag-raw-911414006786)
  → S3 Event Notification
  → SQS (rag-ingestion-queue)
  → Lambda (rag-document-processor) → ingestion_graph.lambda_handler
      → node_download: S3 → /tmp/
      → node_check_duplicate: SHA-256 hash → DynamoDB GSI check
          [BRANCH] duplicate → END
      → node_detect: magic bytes → file type
          [BRANCH] unknown → save rejected to DynamoDB → END
      → node_parse: bytes → List[ParsedBlock] (paragraph-level)
          [BRANCH] no blocks → save failed to DynamoDB → END
      → node_chunk: semantic chunking → List[Chunk]
          embed blocks via LangChain BedrockEmbeddings → cosine similarity → split at topic boundaries
          MAX_TOKENS=448, OVERLAP=64 → final chunk max 512 tokens
      → node_embed: LangChain BedrockEmbeddings → 1536-dim vector per chunk
                    + Nova Micro → 3 HyQ questions per chunk
      → node_store: delete_old_chunks → index in OpenSearch → save DynamoDB → backup S3
```

### Query Pipeline (LangChain LCEL)
```
User Question
  → React UI (CloudFront HTTPS)
  → Cognito auth (JWT token)
  → API Gateway (API Key + JWT validation)
  → Lambda (rag-query) → query_graph.lambda_handler
      → run_hyde:    Nova generates hypothetical answer → embed via LangChain (0.98s)
      → run_search:  Hybrid Search BM25 + KNN → top 10 (1.90s)
      → run_rerank:  Cohere Rerank 3.5 → best 5 (0.15s)
      → run_dedup:   deduplicate by page (0.00s)
      → run_answer:  Nova Micro → grounded answer via LangChain LCEL
  → JSON response with answer + sources
  → LangSmith trace: every step named, timed, token-counted ✅
```

---

## 5. Python Files — Current State

| File | Status | Notes |
|---|---|---|
| `ingestion_graph.py` | ✅ NEW — ACTIVE | LangGraph replacement for handler.py |
| `query_graph.py` | ✅ NEW — ACTIVE | LangChain LCEL replacement for query_handler.py |
| `llm_clients.py` | ✅ NEW | Shared BedrockEmbeddings + ChatBedrock (lru_cache) |
| `opensearch_client.py` | ✅ NEW | Shared OpenSearch connection logic |
| `handler.py` | kept as fallback | v1 ingestion, still in package for main branch |
| `query_handler.py` | kept as fallback | v1 query, still in package for main branch |
| `parsers.py` | ✅ UPDATED | parse_pdf() paragraph-level blocks with section tracking |
| `chunker.py` | ✅ UPDATED | get_embedding_bedrock() now uses LangChain embed_query() |
| `detector.py` | ✅ Keep as-is | Magic byte detection |
| `models.py` | ✅ Keep as-is | ParsedBlock and Chunk dataclasses |
| `config.py` | ✅ UPDATED | Module-level caching for SSM/Secrets Manager |
| `test_parser.py` | ✅ NEW | Local validation script for parse_pdf() |
| `delete_index.py` | ✅ NEW | Utility: delete rag-chunks index via basic auth |
| `check_mapping.py` | ✅ NEW | Utility: verify embedding field mapping |

---

## 6. LangChain + LangGraph + LangSmith — Confirmed Working ✅

All three are live in production Lambda:

**LangChain** — LCEL pipeline in `query_graph.py`, `BedrockEmbeddings` and `ChatBedrock` in `llm_clients.py`. Returns grounded answers with sources and relevance scores.

**LangGraph** — `StateGraph` with 7 nodes and 3 conditional branches in `ingestion_graph.py`. Runs full ingestion pipeline: 116 blocks → 77 chunks → 231 HyQ questions → OpenSearch indexed.

**LangSmith** — traces appearing automatically for every query. Waterfall view shows:
- `run_hyde` — 0.98s, 125 tokens, ChatBedrock (Nova)
- `run_search` — 1.90s, OpenSearch hybrid
- `run_rerank` — 0.15s, Cohere
- `run_dedup` — 0.00s
- `run_answer` — ChatBedrock (Nova)

### LCEL chain structure
```python
query_pipeline = (
    RunnableLambda(run_hyde)
    | RunnableLambda(run_search)
    | RunnableLambda(run_rerank)
    | RunnableLambda(run_dedup)
    | RunnableLambda(run_answer)
)
```

### Why LangGraph for ingestion, LCEL for query
- **Ingestion** has branching: duplicate/rejected/failed exits at each step → LangGraph explicit named nodes
- **Query** is linear: HyDE → search → rerank → dedup → answer → LCEL pipes
- **Cohere Rerank** stays as direct boto3 — no stable LangChain-AWS wrapper exists yet

---

## 7. Deployment Notes (IMPORTANT)

### Package size
LangChain adds ~60MB. Direct upload limit is 70MB. Always deploy via S3:
```powershell
aws s3 cp lambda_package.zip s3://rag-processed-911414006786/deployments/lambda_package.zip --profile rag-dev
aws lambda update-function-code --function-name rag-document-processor --s3-bucket rag-processed-911414006786 --s3-key deployments/lambda_package.zip --profile rag-dev
```

### Platform-specific binaries
Must install for Linux arm64, not Windows:
```powershell
pip install langgraph langchain langchain-aws langchain-core langsmith pydantic -t package/ --platform manylinux2014_aarch64 --only-binary=:all: --python-version 3.11
pip install pypdf opensearch-py requests-aws4auth boto3 -t package/ --platform manylinux2014_aarch64 --only-binary=:all: --python-version 3.11
```

### Lambda memory
Both Lambdas must be **512MB**. LangChain cold start uses ~285MB. 128MB causes OOM.

---

## 8. Deploy Commands

### Deploy ingestion Lambda
```powershell
cd C:\Tech\PROD-Grade-POC\RAG\rag-pipeline
Remove-Item lambda_package.zip -ErrorAction SilentlyContinue
Compress-Archive -Path ingestion_graph.py, handler.py, detector.py, parsers.py, chunker.py, models.py, config.py, llm_clients.py, opensearch_client.py -DestinationPath lambda_package.zip
Compress-Archive -Path .\package\* -Update -DestinationPath lambda_package.zip
aws s3 cp lambda_package.zip s3://rag-processed-911414006786/deployments/lambda_package.zip --profile rag-dev
aws lambda update-function-code --function-name rag-document-processor --s3-bucket rag-processed-911414006786 --s3-key deployments/lambda_package.zip --profile rag-dev
```

### Deploy query Lambda
```powershell
Remove-Item query_package.zip -ErrorAction SilentlyContinue
Compress-Archive -Path query_graph.py, query_handler.py, models.py, config.py, llm_clients.py, opensearch_client.py -DestinationPath query_package.zip
Compress-Archive -Path .\package\* -Update -DestinationPath query_package.zip
aws s3 cp query_package.zip s3://rag-processed-911414006786/deployments/query_package.zip --profile rag-dev
aws lambda update-function-code --function-name rag-query --s3-bucket rag-processed-911414006786 --s3-key deployments/query_package.zip --profile rag-dev
```

### Recreate OpenSearch (full checklist)
```powershell
# 1. Map Lambda role in Dashboards:
#    Security → Roles → all_access → Mapped users → add:
#    arn:aws:iam::911414006786:role/service-role/rag-document-processor-role-ucg8h778

# 2. Create index (MUST do before uploading any PDF)
python create_index.py

# 3. Create hybrid search pipeline
python setup_hybrid_search.py

# 4. Set SQS visibility timeout to 300
aws sqs set-queue-attributes `
  --queue-url "https://sqs.us-east-1.amazonaws.com/911414006786/rag-ingestion-queue" `
  --attributes VisibilityTimeout=300 `
  --profile rag-dev

# 5. Update Parameter Store if endpoint changed
aws ssm put-parameter `
  --name "/rag/opensearch/endpoint" `
  --value "NEW-ENDPOINT.us-east-1.es.amazonaws.com" `
  --type String --overwrite --profile rag-dev

# 6. Reset document hash
python reset_document.py --file johnson_v._superior_court_1.pdf

# 7. Upload PDF
aws s3 cp "C:\Tech\PROD-Grade-POC\Dataset\johnson_v._superior_court_1.pdf" s3://rag-raw-911414006786/ --profile rag-dev

# 8. Watch logs
aws logs tail /aws/lambda/rag-document-processor --follow --profile rag-dev
```

### Other useful commands
```powershell
python reset_document.py --list
python test_query_lambda.py
python check_mapping.py
python delete_index.py
aws logs tail /aws/lambda/rag-query --follow --profile rag-dev
aws dynamodb scan --table-name rag-metadata --profile rag-dev --max-items 5
aws sqs get-queue-attributes --queue-url "https://sqs.us-east-1.amazonaws.com/911414006786/rag-ingestion-queue" --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --profile rag-dev
```

---

## 9. Immediate Next Steps (Before June 4 Demo)

```
1. ✅ LangChain/LangGraph migration complete
2. ✅ Both Lambdas deployed and tested end-to-end
3. ✅ LangSmith tracing live
4. ⏳ git commit + push feature/langchain-langgraph branch
5. ⏳ Test via live demo URL: https://d2hsci73dlmw9w.cloudfront.net
6. ⏳ CloudWatch dashboard (high priority for demo)
7. ⏳ LangSmith Evaluators (dataset of 5-10 Q&A pairs)
8. ⏳ Ingest a second document to prove generalization
```

---

## 10. Production Improvements Roadmap

### High Impact (do before June 4)

**CloudWatch Dashboard**
One screen showing Lambda duration, error rate, memory usage, OpenSearch latency.
- Lambda → Monitor → View CloudWatch metrics
- Create dashboard with: invocation count, error rate, duration p50/p99, memory used
- ~30 minutes to set up, extremely strong demo visual
- Answers the interview question: "how would you monitor this in production?"

**LangSmith Evaluators**
Automated evaluation beyond just tracing — score retrieval quality and answer correctness.
- Create a dataset of 5-10 questions with expected answers in LangSmith
- Run evaluators: answer correctness, context relevance, faithfulness
- Shows you think about quality measurement, not just functionality
- Go to LangSmith → Evaluators → create dataset → run eval

### Medium Impact

**Conversation History**
Frontend uses localStorage — history lost on refresh, not sent to Lambda.
- Store last N conversation turns in DynamoDB
- Pass last 3 turns as context to query Lambda
- Makes it a real conversational RAG system, not just single-turn Q&A

**Guardrails**
If question is completely unrelated to ingested documents, return clean response.
- Check answer for "I cannot find" → return structured "out of scope" response
- Prevents hallucination on off-topic queries
- One `if` check on the answer before returning

### Lower Effort, High Signal

**Multi-document testing**
Ingest 2-3 different PDFs and show cross-document retrieval.
- Right now only one legal case document
- Adding a second document proves the pipeline generalizes
- Good demo story: "the system retrieves across all ingested documents"

**Frontend error handling**
Show user-friendly message when API key missing or OpenSearch is down.
- Instead of raw error, show "Service temporarily unavailable"
- Production systems never show raw stack traces to users

---

## 11. Key Technical Decisions

| Decision | Why |
|---|---|
| LangGraph for ingestion | Branching logic — duplicate/rejected/failed exits, explicit named nodes |
| LangChain LCEL for query | Linear pipeline, cleaner `\|` pipe syntax, auto-traced in LangSmith |
| LangSmith for observability | Per-step latency, token counts, input/output at every node — production monitoring |
| Keep parsers.py as-is | Custom paragraph-level PDF parsing better than LangChain loaders |
| Keep detector.py as-is | Magic byte detection has no LangChain equivalent |
| Keep Cohere as direct boto3 | No stable LangChain-AWS wrapper for Cohere Rerank on Bedrock yet |
| lru_cache on LLM clients | Initialized once per Lambda container, not per invocation |
| Deploy via S3 | Package >70MB exceeds direct upload limit |
| Linux arm64 binaries | pydantic_core is Rust extension — Windows binary won't run on Lambda |
| 512MB Lambda memory | LangChain cold start uses ~285MB; 128MB causes OOM |
| Paragraph-level PDF parsing | One paragraph = one block = better chunk boundaries |
| MAX_TOKENS=448 | 448 + 64 overlap = 512 correct max |
| Cosine similarity | Ignores magnitude, correct for high-dim vectors |
| HyDE at query time | Answer embeddings closer to real answers in vector space |
| HyQ at ingestion | Question-to-question matching stronger than question-to-text |
| Cohere Rerank | Cross-encoder sees Q+chunk together, more accurate than bi-encoder |
| IAM role not access keys | Temporary STS credentials, auto-rotate |
| SQS visibility timeout 300s | Default 30s < Lambda ~35s processing → caused duplicates |
| create_index.py before PDF upload | Auto-mapping creates float type; must pre-create knn_vector mapping |

---

## 12. Cost Breakdown

```
OpenSearch:       ~$50-100/month  ← DELETE when not using!
Bedrock Titan:    ~$0.50/month
Bedrock Nova:     ~$1-2/month
Cohere Rerank:    ~$0.001/query
Secrets Manager:  ~$0.80/month
LangSmith:        Free tier (covers POC usage)
Lambda/S3/SQS/DynamoDB/API Gateway/Cognito/CloudFront: ~$0/month (free tier)
TOTAL:            ~$52-104/month (mostly OpenSearch)
```

---

## 13. Demo Story for June 4

**What to show in order:**

1. Upload a PDF → S3 → watch Lambda logs → `Status: completed ✅`
2. Open LangSmith → show ingestion trace → 7 nodes, 3 branches
3. Ask a question via live demo URL
4. Open LangSmith → show query trace → run_hyde, run_search, run_rerank, run_answer with latencies
5. Open CloudWatch dashboard → show Lambda metrics
6. Ask a second question from a different document → proves generalization

**Key talking points:**
- "Every design decision has a reason — HyDE, HyQ, hybrid search, Cohere rerank"
- "Silent degradation is not safe — zero vector fallback was removed, retry-with-backoff added"
- "LangGraph makes branching explicit — duplicate/rejected/failed are named decision points"
- "LangSmith gives me full observability — I can see every step, latency, and token count"
- "Production concerns: IAM roles over access keys, SQS visibility timeout, paragraph-level parsing"
