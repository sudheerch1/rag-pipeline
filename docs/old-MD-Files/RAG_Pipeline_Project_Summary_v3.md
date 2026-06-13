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
feature/langchain-langgraph ← active branch, LangChain/LangGraph migration COMPLETE
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
  Handler: ingestion_graph.lambda_handler   ← UPDATED (was handler.lambda_handler)
  Handler: query_graph.lambda_handler       ← UPDATED (was query_handler.lambda_handler)
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

Bedrock Models Used:
  amazon.titan-embed-text-v1    ← embeddings (1536 dimensions)
  amazon.nova-micro-v1:0        ← HyQ generation + HyDE + answer generation
  cohere.rerank-v3-5:0          ← reranking (cross-encoder)
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
      → run_hyde: Nova generates hypothetical answer → embed via LangChain
      → run_search: Hybrid Search BM25 + KNN → top 10
      → run_rerank: Cohere Rerank 3.5 → best 5
      → run_dedup: deduplicate by page
      → run_answer: Nova Micro → grounded answer via LangChain LCEL
  → JSON response with answer + sources
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

## 6. Local Project Paths

```
Backend:   C:\Tech\PROD-Grade-POC\RAG\rag-pipeline\
Frontend:  C:\Tech\PROD-Grade-POC\rag-frontend\
Dataset:   C:\Tech\PROD-Grade-POC\Dataset\
```

---

## 7. LangChain/LangGraph Migration — What Changed and Why

### llm_clients.py
Shared module with `lru_cache` — initialized once per Lambda container, not per invocation.
```python
@lru_cache(maxsize=1)
def get_embeddings():
    return BedrockEmbeddings(model_id="amazon.titan-embed-text-v1", region_name="us-east-1")

@lru_cache(maxsize=1)
def get_llm():
    return ChatBedrock(model_id="amazon.nova-micro-v1:0", region_name="us-east-1", model_kwargs={"temperature": 0.3, "max_tokens": 512})
```

### chunker.py change
Only one function changed — `get_embedding_bedrock()` now delegates to LangChain:
```python
# OLD
response = bedrock_client.invoke_model(modelId='amazon.titan-embed-text-v1', ...)

# NEW
from llm_clients import get_embeddings
def get_embedding_bedrock(text: str, bedrock_client=None) -> List[float]:
    return get_embeddings().embed_query(text[:512])
```

### Why LangGraph for ingestion, LCEL for query
- **Ingestion** has branching: duplicate → skip, unknown file → skip, no blocks → skip. LangGraph makes these explicit named decision points (interview-friendly).
- **Query** is linear: HyDE → search → rerank → dedup → answer. LCEL `|` pipes are cleaner.
- **Cohere Rerank** stays as direct boto3 — no stable LangChain-AWS wrapper exists yet.

### LCEL chain structure (query_graph.py)
```python
query_pipeline = (
    RunnableLambda(run_hyde)
    | RunnableLambda(run_search)
    | RunnableLambda(run_rerank)
    | RunnableLambda(run_dedup)
    | RunnableLambda(run_answer)
)
```

---

## 8. Deployment Notes (IMPORTANT)

### Package size issue
LangChain adds ~60MB to the package. Direct upload limit is 70MB. Always deploy via S3:
```powershell
aws s3 cp lambda_package.zip s3://rag-processed-911414006786/deployments/lambda_package.zip --profile rag-dev
aws lambda update-function-code --function-name rag-document-processor --s3-bucket rag-processed-911414006786 --s3-key deployments/lambda_package.zip --profile rag-dev
```

### Platform-specific binaries
pydantic_core and other compiled packages must be installed for Linux arm64, not Windows:
```powershell
pip install langgraph langchain langchain-aws langchain-core langsmith pydantic -t package/ --platform manylinux2014_aarch64 --only-binary=:all: --python-version 3.11
pip install pypdf opensearch-py requests-aws4auth boto3 -t package/ --platform manylinux2014_aarch64 --only-binary=:all: --python-version 3.11
```

### Lambda memory
Both Lambdas must be 512MB. LangChain cold start uses ~285MB. 128MB causes OOM.

---

## 9. Deploy Commands

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

## 10. Immediate Next Steps

```
1. ✅ LangChain/LangGraph migration complete
2. ✅ Both Lambdas deployed and tested end-to-end
3. ✅ Query pipeline returning grounded answers with sources
4. ⏳ git commit + push feature/langchain-langgraph branch
5. ⏳ Deploy query Lambda update to frontend (change handler if not done)
6. ⏳ Test via live demo URL: https://d2hsci73dlmw9w.cloudfront.net
7. ⏳ Prep for June 4 query pipeline demo
```

---

## 11. Key Technical Decisions

| Decision | Why |
|---|---|
| LangGraph for ingestion | Branching logic — duplicate/rejected/failed exits at each step, explicit named nodes |
| LangChain LCEL for query | Linear pipeline, no branching, cleaner `\|` pipe syntax |
| Keep parsers.py as-is | Custom paragraph-level PDF parsing is better than LangChain loaders |
| Keep detector.py as-is | Magic byte detection has no LangChain equivalent |
| Keep Cohere as direct boto3 | No stable LangChain-AWS wrapper for Cohere Rerank on Bedrock yet |
| lru_cache on LLM clients | Initialized once per Lambda container, not per invocation |
| Deploy via S3 | Package >70MB exceeds direct upload limit |
| Linux arm64 binaries | pydantic_core is a compiled Rust extension — Windows binary won't run on Lambda |
| 512MB Lambda memory | LangChain cold start uses ~285MB; 128MB causes OOM |
| Paragraph-level PDF parsing | One paragraph = one block = better chunk boundaries |
| MAX_TOKENS=448 | 448 + 64 overlap = 512 correct max |
| Cosine similarity | Ignores magnitude, correct for high-dim vectors |
| HyDE at query time | Answer embeddings closer to real answers in vector space |
| HyQ at ingestion | Question-to-question matching stronger than question-to-text |
| Cohere Rerank | Cross-encoder sees Q+chunk together, more accurate than bi-encoder |
| IAM role not access keys | Temporary STS credentials, auto-rotate |
| SQS visibility timeout 300s | Default 30s < Lambda ~35s processing → caused duplicates |
| create_index.py before PDF upload | Auto-mapping creates float type for embedding; must pre-create knn_vector mapping |

---

## 12. Cost Breakdown

```
OpenSearch:       ~$50-100/month  ← DELETE when not using!
Bedrock Titan:    ~$0.50/month
Bedrock Nova:     ~$1-2/month
Cohere Rerank:    ~$0.001/query
Secrets Manager:  ~$0.80/month
Lambda/S3/SQS/DynamoDB/API Gateway/Cognito/CloudFront: ~$0/month (free tier)
TOTAL:            ~$52-104/month (mostly OpenSearch)
```

---

## 13. Planned Next Improvements (Post-Demo)

```
- Duplicate detection improvements
- API authentication hardening
- CloudWatch monitoring dashboard
- Multi-format document testing
- LangSmith tracing (add env vars to Lambda)
- Terraform/IaC conversion
```

### LangSmith setup (when ready)
Add these to Lambda environment variables:
```
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=ls__your_key_here
LANGCHAIN_PROJECT=rag-pipeline
```
Once set, every invoke() call automatically appears in LangSmith dashboard — no extra code needed.
