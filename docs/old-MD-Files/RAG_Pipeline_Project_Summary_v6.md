# Production-Grade RAG Pipeline on AWS — Updated Project Summary
## For continuing in a new thread (Updated: May 23, 2026)

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
  ⚠️ DLQ currently has 2 messages (test messages — left intentionally for demo)

Lambda:
  rag-document-processor         ← ingestion pipeline (512MB, 300s timeout)
  rag-query                      ← query pipeline (512MB, 30s timeout)
  Handler: ingestion_graph.lambda_handler   ← UPDATED
  Handler: query_graph.lambda_handler       ← UPDATED
  IAM Role: arn:aws:iam::911414006786:role/service-role/rag-document-processor-role-ucg8h778
  LogFormat: JSON (structured logging enabled on both)
  X-Ray: Active tracing enabled on both
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
  ⚠️ After recreating: run setup_hybrid_search.py to recreate the search pipeline
  ⚠️ After recreating: re-map Lambda IAM role in Dashboards (Security → Roles → all_access → Mapped users → Backend roles)
      Use IAM role ARN: arn:aws:iam::911414006786:role/service-role/rag-document-processor-role-ucg8h778
      NOT the Lambda function ARN

API Gateway:
  URL: https://k0injz47nk.execute-api.us-east-1.amazonaws.com/prod/query
  v1 URL: https://k0injz47nk.execute-api.us-east-1.amazonaws.com/prod/v1/query  ← NEW
  Health: https://k0injz47nk.execute-api.us-east-1.amazonaws.com/prod/health
  Type: REST API
  ID: k0injz47nk
  Stage: prod
  Routes:
    POST /query      — API Key required, Lambda proxy integration
    POST /v1/query   — API Key required, Lambda proxy integration ← NEW
    GET  /health     — No API key, Lambda proxy integration, public
  API Key: gPEWpbTVLq9NPD0YJYD4pCszNr7UcF4ln1etxZd0
  Usage Plan: rag-usage-plan-prod (10 req/sec, 1000/month)
  ⚠️ When adding new routes manually: must add Lambda resource-based permission
     aws lambda add-permission --function-name rag-query --statement-id <id> \
       --action lambda:InvokeFunction --principal apigateway.amazonaws.com \
       --source-arn "arn:aws:execute-api:us-east-1:911414006786:k0injz47nk/prod/POST/v1/query"

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

SNS:
  Topic: rag-alerts
  ARN: arn:aws:sns:us-east-1:911414006786:rag-alerts
  Subscription: sudheer.cat2010@gmail.com (EMAIL, Confirmed)

CloudWatch:
  Alarm: rag-dlq-depth-alarm
    Metric: SQS ApproximateNumberOfMessagesVisible on rag-ingestion-dlq
    Threshold: >= 1 for 1 datapoint within 1 minute
    Statistic: Maximum
    Action: SNS rag-alerts
    State: IN ALARM (2 test messages in DLQ — intentional for demo)
  Dashboard: rag-pipeline-dashboard
    Widgets: Invocation Count, Error Rate, Duration p50/p99, Throttles, DLQ Depth

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
  Dataset: johnson-v-superior-court-eval (8 Q&A pairs, answer_correctness: 1.00)
  Eval script: langsmith_eval.py
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
      → Input Validation: type check, strip, min 3 chars, max 1000 chars ← NEW
      → GET /health → health_check() → OpenSearch ping → 200/503
      → run_hyde:    Nova generates hypothetical answer → embed via LangChain (0.98s)
      → run_search:  Hybrid Search BM25 + KNN → top 10 (1.90s)
      → run_rerank:  Cohere Rerank 3.5 → best 5 (0.15s)
      → run_dedup:   deduplicate by page (0.00s)
      → run_answer:  Token budget enforcement (2000 tokens) → Nova Micro → grounded answer ← NEW
  → JSON response with answer + sources
  → LangSmith trace: every step named, timed, token-counted ✅
```

---

## 5. Python Files — Current State

| File | Status | Notes |
|---|---|---|
| `ingestion_graph.py` | ✅ ACTIVE | LangGraph, structured JSON logging, zero print() |
| `query_graph.py` | ✅ ACTIVE | LangChain LCEL, input validation, token budget |
| `langsmith_eval.py` | ✅ NEW | 8 Q&A pairs, answer_correctness evaluator, retrieval_relevance evaluator |
| `query_handler.py` | ❌ DEAD CODE | Old boto3 version — not in execution path |
| `llm_clients.py` | ✅ ACTIVE | lru_cache on LLM + embeddings clients |
| `opensearch_client.py` | ✅ ACTIVE | AWS4Auth, lru_cache |
| `config.py` | ✅ ACTIVE | get_logger, get_opensearch_endpoint |
| `parsers.py` | ✅ ACTIVE | Custom paragraph-level PDF parsing |
| `detector.py` | ✅ ACTIVE | Magic byte file type detection |
| `chunker.py` | ✅ ACTIVE | Semantic chunking, MAX_TOKENS=448, OVERLAP=64 |
| `create_index.py` | ✅ ACTIVE | Creates knn_vector mapping — MUST run before first PDF upload |
| `setup_hybrid_search.py` | ✅ ACTIVE | Creates hybrid-search-pipeline — MUST run after create_index.py |
| `reset_document.py` | ✅ ACTIVE | Clears DynamoDB hash to allow re-ingestion |
| `delete_index.py` | ✅ ACTIVE | Deletes OpenSearch index |
| `check_mapping.py` | ✅ ACTIVE | Verifies index field mappings |

---

## 6. Ingested Documents

| Document | Blocks | Chunks | HyQ Questions | Status |
|---|---|---|---|---|
| `johnson_v._superior_court_1.pdf` | 116 | 77 | 231 | ✅ completed |
| `raceway_ford_cases.pdf` | 85 | 60 | 180 | ✅ completed |

---

## 7. Production Improvements — Completed ✅

| Feature | Details |
|---|---|
| Structured JSON logging | CloudWatch Insights queryable, both Lambdas |
| Health check endpoint | GET /health, returns 200/503 |
| DLQ CloudWatch alarm | Fires within 1 min, SNS notification |
| CloudWatch dashboard | 5 widgets, one-screen system view |
| X-Ray tracing | Full Lambda execution traces including cold start |
| LangSmith tracing | Per-step latency, token counts, input/output at every node |
| Input validation | Type check, strip, min 3 chars, max 1000 chars on query Lambda |
| Token budget enforcement | 2000 token cap on Nova context (CONTEXT_TOKEN_BUDGET = 2000) |
| API versioning | /v1/query alongside /query — both active, same Lambda |
| LangSmith Evaluators | 8 Q&A pairs, answer_correctness: 1.00 (8/8 questions) |
| Multi-document ingestion | 2 PDFs ingested — Johnson (legal case) + Raceway Ford (consumer protection) |
| Duplicate detection | SHA-256 hash + DynamoDB GSI |
| Retry with backoff | Embedding retries, 3 attempts, exponential backoff |
| Paragraph-level parsing | Better chunk boundaries vs page-level |

---

## 8. Remaining Items (Before June 4 Demo)

### Still To Do
**Token budget enforcement** — ⏳ code written in query_graph.py, needs deploy + test

### Medium Priority
**Conversation history** — store last N turns in DynamoDB, pass as context to query Lambda.

**Guardrails** — if answer contains "I cannot find", return structured out-of-scope response.

### Lower Priority
**DynamoDB TTL** — auto-expire old/rejected document records. (10 min)

**Response caching** — API Gateway 60s TTL on /query for repeated questions. (15 min)

**DLQ Lambda processor** — classify failures as transient/permanent, retry or archive to S3.

**Terraform/IaC** — convert Console setup to infrastructure as code.

---

## 9. Deploy Commands

### Deploy query Lambda
```powershell
Remove-Item query_package.zip -ErrorAction SilentlyContinue
Compress-Archive -Path query_graph.py, query_handler.py, models.py, config.py, llm_clients.py, opensearch_client.py -DestinationPath query_package.zip
Compress-Archive -Path .\package\* -Update -DestinationPath query_package.zip
aws s3 cp query_package.zip s3://rag-processed-911414006786/deployments/query_package.zip --profile rag-dev
aws lambda update-function-code --function-name rag-query --s3-bucket rag-processed-911414006786 --s3-key deployments/query_package.zip --profile rag-dev
```

### Deploy ingestion Lambda
```powershell
Remove-Item ingestion_package.zip -ErrorAction SilentlyContinue
Compress-Archive -Path ingestion_graph.py, detector.py, parsers.py, chunker.py, opensearch_client.py, llm_clients.py, config.py -DestinationPath ingestion_package.zip
Compress-Archive -Path .\package\* -Update -DestinationPath ingestion_package.zip
aws s3 cp ingestion_package.zip s3://rag-processed-911414006786/deployments/ingestion_package.zip --profile rag-dev
aws lambda update-function-code --function-name rag-document-processor --s3-bucket rag-processed-911414006786 --s3-key deployments/ingestion_package.zip --profile rag-dev
```

### Recreate OpenSearch (full checklist)
```powershell
# 1. Map Lambda IAM role in Dashboards (NOT the Lambda function ARN):
#    Security → Roles → all_access → Mapped users → Backend roles → add:
#    arn:aws:iam::911414006786:role/service-role/rag-document-processor-role-ucg8h778

# 2. Create index with correct knn_vector mapping (MUST do before uploading any PDF)
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

# 6. Reset document hashes
python reset_document.py --file johnson_v._superior_court_1.pdf
python reset_document.py --file raceway_ford_cases.pdf

# 7. Upload PDFs
aws s3 cp "C:\Tech\PROD-Grade-POC\Dataset\johnson_v._superior_court_1.pdf" s3://rag-raw-911414006786/ --profile rag-dev
aws s3 cp "C:\Tech\PROD-Grade-POC\Dataset\raceway_ford_cases.pdf" s3://rag-raw-911414006786/ --profile rag-dev

# 8. Watch logs
aws logs tail /aws/lambda/rag-document-processor --follow --profile rag-dev
```

### Other useful commands
```powershell
python reset_document.py --list
python langsmith_eval.py
python check_mapping.py
python delete_index.py
aws logs tail /aws/lambda/rag-document-processor --follow --profile rag-dev
aws dynamodb scan --table-name rag-metadata --profile rag-dev --max-items 5
aws sqs get-queue-attributes --queue-url "https://sqs.us-east-1.amazonaws.com/911414006786/rag-ingestion-dlq" --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --profile rag-dev

# Test /query endpoint
Invoke-WebRequest -Uri "https://k0injz47nk.execute-api.us-east-1.amazonaws.com/prod/query" `
  -Method POST `
  -Headers @{"x-api-key"="gPEWpbTVLq9NPD0YJYD4pCszNr7UcF4ln1etxZd0"; "Content-Type"="application/json"} `
  -Body '{"question":"What was Gregory Johnson convicted of?"}' -UseBasicParsing

# Test /v1/query endpoint
Invoke-WebRequest -Uri "https://k0injz47nk.execute-api.us-east-1.amazonaws.com/prod/v1/query" `
  -Method POST `
  -Headers @{"x-api-key"="gPEWpbTVLq9NPD0YJYD4pCszNr7UcF4ln1etxZd0"; "Content-Type"="application/json"} `
  -Body '{"question":"What was Gregory Johnson convicted of?"}' -UseBasicParsing

# Test health check
(Invoke-WebRequest -Uri "https://k0injz47nk.execute-api.us-east-1.amazonaws.com/prod/health" -UseBasicParsing).Content

# Set AWS profile for local scripts
$env:AWS_PROFILE = "rag-dev"
```

---

## 10. Key Technical Decisions

| Decision | Why |
|---|---|
| LangGraph for ingestion | Branching logic — duplicate/rejected/failed exits, explicit named nodes |
| LangChain LCEL for query | Linear pipeline, cleaner pipe syntax, auto-traced in LangSmith |
| LangSmith for observability | Per-step latency, token counts, input/output at every node |
| Structured JSON logging | CloudWatch Insights queryable, no mixed print()/logger noise |
| Maximum statistic for DLQ alarm | Average can miss single message if queue drains between samples |
| GET /health no API key | Health checks must be unauthenticated — load balancers, monitoring tools |
| X-Ray + LangSmith together | X-Ray = infrastructure layer (cold start, S3, DynamoDB); LangSmith = app layer (HyDE, search, rerank) |
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
| Input validation before Lambda logic | Bad inputs rejected at door — Nova and OpenSearch never called |
| Token budget 2000 tokens | Caps Nova context size — prevents runaway costs, ~4 chars per token |
| API versioning /v1/query | Forward compatibility — /query still works, both point to same Lambda |
| Lambda resource-based policy | API Gateway needs explicit permission per route — not just integration config |
| LangSmith evaluators | answer_correctness 1.00 on 8/8 questions — quantified retrieval quality |

---

## 11. Cost Breakdown

```
OpenSearch:       ~$50-100/month  ← DELETE when not using!
Bedrock Titan:    ~$0.50/month
Bedrock Nova:     ~$1-2/month
Cohere Rerank:    ~$0.001/query
Secrets Manager:  ~$0.80/month
LangSmith:        Free tier (covers POC usage)
Lambda/S3/SQS/DynamoDB/API Gateway/Cognito/CloudFront: ~$0/month (free tier)
CloudWatch:       ~$0/month (free tier covers dashboard + alarms at POC scale)
X-Ray:            ~$0/month (free tier: 100K traces/month)
TOTAL:            ~$52-104/month (mostly OpenSearch)
```

---

## 12. Demo Story for June 4

**What to show in order:**

1. Open `rag-pipeline-dashboard` — show all 5 widgets. Point to DLQ Depth = 2 and alarm in red. Explain the failure handling story.
2. Hit `GET /health` in browser — show `{"status": "degraded", "opensearch": "unreachable"}`. Explain 200/503 behavior.
3. Spin up OpenSearch → hit `/health` again → show `{"status": "healthy"}`.
4. Upload a PDF → S3 → watch Lambda logs in CloudWatch (JSON structured logs) → `Status: completed ✅`
5. Open LangSmith → show ingestion trace → 7 nodes, 3 branches
6. Ask a question via live demo URL
7. Open LangSmith → show query trace → run_hyde, run_search, run_rerank, run_answer with latencies
8. Open X-Ray → show full execution trace including cold start
9. Open CloudWatch dashboard → show metrics updating in real time
10. Ask a question about the second document (Raceway Ford) → proves cross-document generalization
11. Show LangSmith evaluator results → answer_correctness: 1.00 on 8 questions

**Key talking points:**
- "Every design decision has a reason — HyDE, HyQ, hybrid search, Cohere rerank"
- "Silent degradation is not safe — zero vector fallback was removed, retry-with-backoff added"
- "LangGraph makes branching explicit — duplicate/rejected/failed are named decision points"
- "LangSmith gives me application-level observability — every step, latency, token count"
- "X-Ray gives me infrastructure-level observability — cold start, S3 download, DynamoDB latency"
- "Structured JSON logging means I can query my logs like a database in CloudWatch Insights"
- "The health check endpoint is how any monitoring tool or load balancer knows the system is up"
- "The DLQ alarm means nothing silently fails — I know within 1 minute if ingestion is broken"
- "Input validation prevents bad inputs from reaching Nova — production systems don't trust user input"
- "Token budget enforcement caps context size — prevents runaway costs and prompt explosions"
- "API versioning with /v1/query — forward compatible, old clients still work on /query"
- "LangSmith evaluators give me a number — 1.00 answer correctness on 8 questions from the real document"
- "Two documents from different legal domains — cross-document retrieval works"
