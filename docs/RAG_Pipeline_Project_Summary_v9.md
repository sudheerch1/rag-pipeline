# Production-Grade RAG Pipeline on AWS — Project Summary v9
## New Thread Carry-Forward (Updated: June 13, 2026)

---

## Quick Reference

| Item | Value |
|---|---|
| **Active branch** | `feature/langchain-langgraph` |
| **Demo 2** | June 4, 2026 ✅ Delivered (~20 engineers/architects, well received) |
| **Live URL** | https://d2hsci73dlmw9w.cloudfront.net |
| **AWS Profile** | `rag-dev` / Account `911414006786` / Region `us-east-1` |
| **LangSmith project** | `rag-pipeline` |
| **Eval dataset** | `johnson-v-superior-court-eval` → expanded to 50 questions, 3 legal docs |
| **Best eval scores** | correctness 0.94 · faithfulness 0.96 · relevance 0.88 · context_precision 0.44 |
| **OpenSearch** | ⚠️ CURRENTLY DELETED — recreate before next session |
| **GitHub** | `sudheerch1/rag-pipeline` (backend) · `sudheerch1/rag-frontend` (frontend) |

---

## 1. Current Model Stack

> ⚠️ **Major change since v7/v8:** Models upgraded from Titan+Nova to Cohere+Haiku.

| Component | Old (Baseline) | Current |
|---|---|---|
| Embeddings | `amazon.titan-embed-text-v1` (1536-dim) | `Cohere Embed v4` (1536-dim) |
| LLM (HyDE / HyQ / Answer) | `amazon.nova-micro-v1:0` | `claude-haiku-4-5` |
| Reranker | `cohere.rerank-v3-5:0` | `cohere.rerank-v3-5:0` (unchanged) |
| Eval judge | `amazon.nova-micro-v1:0` | `claude-haiku-4-5` |

---

## 2. Best Query Config (Current)

| Parameter | Old Baseline | Current Best |
|---|---|---|
| `top_k` | 10 | **15** |
| `top_n` (reranker) | 5 | **5** |
| `CONTEXT_TOKEN_BUDGET` | 2000 | **3000** |
| Hybrid weights (semantic/BM25) | — | **0.5 / 0.5** |
| HyQ questions per chunk | 3 | **3** |

---

## 3. Eval Score History

| Metric | Baseline (Titan+Nova, 8q) | Current Best (Cohere+Haiku, 50q) |
|---|---|---|
| Answer Correctness | 0.50 | **0.94** |
| Faithfulness | 1.00 | **0.96** |
| Answer Relevance | 0.62 | **0.88** |
| Context Precision | 0.45 | **0.44** |

**Key insight:** Context precision is resistant to retrieval tuning. The real fix is chunking (Phase C experiments). Everything else improved dramatically with model upgrades + parameter tuning.

---

## 4. Experiment History

| Experiment | Change | Result | Kept? |
|---|---|---|---|
| Model upgrade | Titan+Nova → Cohere Embed v4 + Haiku 4.5 | Correctness 0.50→0.94 | ✅ Yes |
| top_k | 10 → 15 | Improved recall | ✅ Yes |
| top_n | 5 → 7 | Context precision 0.44→0.34 (hurt) | ❌ Reverted to 5 |
| Token budget | 2000 → 3000 | Improved correctness | ✅ Yes |
| Hybrid weights | 0.7/0.3 (semantic/BM25) | No improvement (legal text needs BM25) | ❌ Reverted to 0.5/0.5 |

### Remaining Experiments (not priority — moving to next project)

| # | Experiment | Requires re-ingestion? |
|---|---|---|
| B1 | Per-document scoring in `langsmith_eval.py` | No |
| A4 | HyQ 3→5 questions | Yes |
| C1 | similarity_threshold 0.75→0.70 | Yes |
| C2 | MAX_TOKENS 448→512 | Yes |

---

## 5. Documents Ingested

| Document | Chunks |
|---|---|
| `johnson_v._superior_court_1.pdf` | 77 |
| `raceway_ford_cases.pdf` | 60 |
| Amiodarone case | ~unknown (added this session) |

---

## 6. AWS Resources

```
S3:
  rag-raw-911414006786           ← PDF uploads
  rag-processed-911414006786     ← chunks JSON + Lambda deployment zips
  rag-frontend-911414006786      ← React static files

SQS:
  rag-ingestion-queue            ← S3 event notifications
  rag-ingestion-dlq              ← dead letter queue
  ⚠️ Visibility Timeout must be 300s (re-set after every recreation)

Lambda:
  rag-document-processor         ← ingestion (512MB, 300s timeout, arm64)
  rag-query                      ← query (512MB, 30s timeout, arm64)
  Handlers: ingestion_graph.lambda_handler / query_graph.lambda_handler
  IAM Role: arn:aws:iam::911414006786:role/service-role/rag-document-processor-role-ucg8h778
  ⚠️ Both Lambdas share the same IAM role
  ⚠️ Deploy via S3 (package >70MB exceeds direct upload limit)
  ⚠️ X-Ray active tracing enabled on both; OTEL_TRACES_EXPORTER=awsxray on both
  ⚠️ Linux arm64 binaries required (--platform manylinux2014_aarch64)
  ⚠️ Always use the package/ folder — pre-built Linux arm64 binaries live there

DynamoDB:
  Table: rag-metadata
  Partition Key: document_id
  GSI: content-hash-index (on content_hash)
  Fields: document_id, content_hash, file_type, blocks, chunks, status, error, timestamp

OpenSearch:
  Domain: rag-vectors
  ⚠️ CURRENTLY DELETED — must recreate before next session
  Index: rag-chunks
  Engine: FAISS / HNSW / cosine similarity
  Search Pipeline: hybrid-search-pipeline (BM25 + KNN, 0.5/0.5 weights, min_max normalization)
  ⚠️ Recreation order is strict — see Section 9

API Gateway:
  ID: k0injz47nk
  POST /query      → rag-query Lambda (API key required)
  POST /v1/query   → rag-query Lambda (API key required)
  GET  /health     → rag-query Lambda (no API key, public; checks OpenSearch reachability)
  API Key: gPEWpbTVLq9NPD0YJYD4pCszNr7UcF4ln1etxZd0
  Usage Plan: rag-usage-plan-prod (10 req/sec, 1000/month)

Cognito:
  User Pool: us-east-1_carOA35Jg
  App Client: rag-query-app (ID: 6m9qrv1v2r1oq77i874hdph5nb)
  Grant Type: Implicit grant (deprecated; production fix: authorization code + PKCE)

CloudFront:
  Distribution ID: E1EG5P75323N5P
  URL: https://d2hsci73dlmw9w.cloudfront.net
  ⚠️ POC gap: static files delivered without auth (production fix: Lambda@Edge)

CloudWatch:
  Alarm: rag-dlq-depth-alarm (fires when DLQ ≥ 1 → SNS → email)
  Dashboard: rag-pipeline-dashboard ⚠️ widgets lost — needs rebuild

Secrets Manager:  rag/opensearch/credentials
Parameter Store:
  /rag/opensearch/endpoint
  /rag/langsmith/api-key (SecureString)
```

---

## 7. Key Source Files

| File | Role |
|---|---|
| `query_graph.py` | Query Lambda handler + LCEL pipeline |
| `ingestion_graph.py` | Ingestion Lambda handler + LangGraph nodes (⚠️ UnboundLocalError bug at line 361, unfixed) |
| `langsmith_eval.py` | 50-question eval script (4 evaluators) |
| `llm_clients.py` | Cohere Embed v4 + Haiku 4.5 clients (`lru_cached`) |
| `opensearch_client.py` | AWS4Auth OpenSearch client (`lru_cached`) |
| `config.py` | Reads Parameter Store for OpenSearch endpoint |
| `parsers.py` | Paragraph-level PDF parsing (structural block extraction) |
| `detector.py` | Magic-byte file type detection |
| `chunker.py` | Hybrid chunking: structural → semantic merge (0.75 threshold) → recursive split, 448-token cap |
| `create_index.py` | Creates OpenSearch index with knn_vector mapping |
| `setup_hybrid_search.py` | Creates hybrid-search-pipeline (0.5/0.5 weights) |
| `reset_document.py` | Clears DynamoDB hash so PDF can be re-ingested |

---

## 8. Architecture

### Ingestion Pipeline (LangGraph — 7 nodes, 3 conditional branches)
```
PDF Upload → S3 → SQS → rag-document-processor Lambda
  node_download        → S3 to /tmp/ (SHA-256 computed here)
  node_check_duplicate → SHA-256 → DynamoDB GSI lookup (branch: duplicate → END)
  node_detect          → magic bytes (branch: unknown → rejected → END)
  node_parse           → paragraph-level structural block extraction (pypdf)
  node_chunk           → hybrid: structural → semantic merge (cosine 0.75) → recursive split, 448-token cap
  node_hyq             → Haiku 4.5 generates 3 hypothetical questions per chunk
  node_embed           → Cohere Embed v4 → 1536-dim vector
  node_store           → OpenSearch (vector + metadata) + DynamoDB
```

### Query Pipeline (LangChain LCEL — linear chain)
```
React UI → Cognito JWT → API Gateway (API key + JWT) → rag-query Lambda
  Input validation (type check, strip whitespace, length 3–1000 chars)
  run_hyde    → Haiku 4.5 generates hypothetical answer → Cohere Embed v4 → hyde_vector
  run_search  → Hybrid: BM25 on text+hyq_questions^2.0 (raw question) + KNN on embedding (hyde_vector)
              → top_k=15, weights 0.5/0.5
  run_rerank  → Cohere Rerank 3.5 cross-encoder → top 5
  run_dedup   → remove duplicate pages
  run_answer  → token budget (3000 tokens, char-based greedy fill) → Haiku 4.5 → grounded answer + sources
  → JSON response: { answer, sources, context, latency_ms }
```

---

## 9. OpenSearch Recreate Checklist (ORDER IS STRICT)

```
1. Create cluster in AWS console (~15 min wait)
2. Update endpoint in Parameter Store:
   aws ssm put-parameter --name "/rag/opensearch/endpoint" --value "NEW-ENDPOINT" `
     --type String --overwrite --profile rag-dev
3. python create_index.py
4. python setup_hybrid_search.py
5. Map IAM backend role in OpenSearch Dashboards:
   Security → Roles → all_access → Mapped users → Backend roles
   → arn:aws:iam::911414006786:role/service-role/rag-document-processor-role-ucg8h778
   ⚠️ Use IAM role ARN (NOT Lambda function ARN)
6. Upload PDFs to S3 one at a time:
   aws s3 cp "C:\...\johnson_v._superior_court_1.pdf" s3://rag-raw-911414006786/ --profile rag-dev
   aws s3 cp "C:\...\raceway_ford_cases.pdf" s3://rag-raw-911414006786/ --profile rag-dev
7. Verify chunk counts in DynamoDB
8. Set SQS visibility timeout (re-set after every recreation):
   aws sqs set-queue-attributes `
     --queue-url "https://sqs.us-east-1.amazonaws.com/911414006786/rag-ingestion-queue" `
     --attributes VisibilityTimeout=300 --profile rag-dev
9. Run test query, confirm LangSmith trace
```

---

## 10. Lambda Deploy Commands

```powershell
# ⚠️ Always use the package/ folder — pre-built Linux arm64 binaries live there
Copy-Item *.py .\package\
Compress-Archive -Path .\package\* -DestinationPath query_lambda.zip -Force
aws s3 cp query_lambda.zip s3://rag-raw-911414006786/query_lambda.zip --profile rag-dev --region us-east-1
aws lambda update-function-code --function-name rag-query `
  --s3-bucket rag-raw-911414006786 --s3-key query_lambda.zip `
  --profile rag-dev --region us-east-1
```

---

## 11. What's Next

### Immediate (This Weekend)
1. **Write ADRs** — document all architectural decisions with interview-ready reasoning
2. **Update DECISIONS.md** in repo with experiment results (model upgrades, parameter tuning findings)

### Next Project: Case Intelligence Assistant (NJ Judiciary)
Multi-agent system targeting NJ Judiciary workflow:
- Sub-agents: Case Research + Document Analysis + Risk Assessment + Scheduler
- Stack: AWS Lambda + Bedrock, OpenSearch hybrid kNN+BM25
- Integrations: NJ eCourts, CourtListener, stubbed Westlaw/Lexis

### Production Hardening Roadmap

| Area | Current POC | Production Upgrade |
|---|---|---|
| Auth | Implicit grant (deprecated) | Authorization code + PKCE |
| Infra | Console-managed | Terraform / CloudFormation IaC |
| Network | Public endpoints | VPC + private subnets + WAF |
| HyQ matching | BM25 (lexical) | Embed questions → semantic kNN |
| Cold starts | None | Provisioned concurrency |
| Confidence | No threshold | Bedrock Guardrails + fallback |
| Memory | None | DynamoDB conversation history |
| CI/CD | Manual deploy | GitHub Actions (Linux arm64 builds) |
| Caching | None | API Gateway response caching |
| Resilience | DLQ only | Circuit breaker pattern |
| Index mgmt | Direct writes | OpenSearch index aliases |

---

## 12. Why Each Design Decision Exists (Interview Core)

**Constraint-driven framing: every decision traces back to a concrete system constraint.**

| Decision | Constraint / Reason |
|---|---|
| HyDE | Question vector ≠ answer vector in high-dim space; hypothetical answer bridges the gap |
| HyQ | Question-to-question match stronger than question-to-paragraph; pre-built at ingestion = zero query cost |
| Hybrid BM25+KNN (0.5/0.5) | BM25 catches exact legal terms; KNN catches semantic meaning; equal weights validated by experiment (0.7/0.3 semantic-heavy hurt performance on legal text) |
| `hyq_questions^2.0` boost | Question-to-question match is a stronger signal — deserves 2× weight |
| Cohere cross-encoder rerank | Bi-encoder scores Q and chunk independently; cross-encoder sees them together → more accurate |
| Dedup by page | Prevents same source page from dominating context window |
| Token budget 3000 | Caps LLM context → bounded cost, bounded latency; 3000 > 2000 improved correctness |
| top_k=15 not 10 | Wider initial recall → better reranker input (top_n=5 unchanged — quality over quantity) |
| top_n=5 not 7 | top_n=7 hurt context_precision (0.44→0.34); more context ≠ better answers |
| LangGraph for ingestion | 3 conditional branches map naturally to a directed graph |
| LCEL for query | Linear sequential pipeline — simpler abstraction is the right choice |
| SHA-256 deduplication | Filenames change; content doesn't — renames correctly detected as duplicates |
| `lru_cache` on clients | Lambda warm containers reuse HTTPS connections — pay setup cost once |
| AWS4Auth not username/pw | IAM role = STS temporary credentials, auto-rotate, no secrets to leak |
| SQS between S3 and Lambda | Durability, retry with backoff, DLQ for failed messages, visibility timeout |
| pypdf over pdfplumber/PyMuPDF | pdfplumber: dependency conflict with cryptography; PyMuPDF: C extensions exceed Lambda size |
| Haiku 4.5 over Nova Micro | Correctness 0.50→0.94 improvement justifies cost difference for POC |
| Cohere Embed v4 over Titan | Part of model upgrade suite; enabled dramatic correctness improvement |

---

## 13. Key Concepts for Interviews

### Bi-encoder vs Cross-encoder
```
Bi-encoder (KNN):   Question → Vector A  ┐  compared separately — never see each other
                    Chunk    → Vector B  ┘
Cross-encoder:      [Question + Chunk together] → Relevance score — cross-text attention
```
Cross-encoder can't replace KNN for full-corpus search (too slow). Pattern: OpenSearch narrows to 15 → Cohere deeply judges → return top 5.

### Three-Layer Observability
```
CloudWatch Dashboard  → "Is the system up and healthy?"        (infra metrics, alarms)
X-Ray Trace Map       → "Where does the time go at AWS level?" (cold start, service boundaries)
LangSmith Tracing     → "What did each AI step do?"           (per-step tokens, latency, I/O)
LangSmith Evaluation  → "How good are the answers and why?"   (4 metrics)
```

### Faithfulness 0.96 + Correctness 0.94 (Current)
These together confirm the pipeline is both grounded (not hallucinating) and retrieving the right content. The small faithfulness drop from 1.00 reflects the expanded 50-question dataset covering more edge cases.

### Context Precision Stuck at ~0.44
Resistant to retrieval parameter tuning (top_k, weights, top_n all tried). Root cause is chunking — chunks are too large or not semantically tight enough. Fix is Phase C experiments (C1: similarity_threshold, C2: MAX_TOKENS).

### DLQ ≠ Circuit Breaker
- DLQ stops retries per individual message (message-level)
- Circuit breaker halts all traffic upon detecting systemic failure (system-level)

### Lambda File Edit Warning
Surgical edits to `query_graph.py` in PowerShell consistently cause indentation errors. Always use full file replacements via `Set-Content` and verify with `python -c "import query_graph"`.

---

## 14. Useful Commands

```powershell
# Watch logs
aws logs tail /aws/lambda/rag-document-processor --follow --profile rag-dev

# Test query endpoint
Invoke-WebRequest -Uri "https://k0injz47nk.execute-api.us-east-1.amazonaws.com/prod/query" `
  -Method POST `
  -Headers @{"x-api-key"="gPEWpbTVLq9NPD0YJYD4pCszNr7UcF4ln1etxZd0"; "Content-Type"="application/json"} `
  -Body '{"question":"What was Gregory Johnson convicted of?"}' -UseBasicParsing

# Health check
(Invoke-WebRequest -Uri "https://k0injz47nk.execute-api.us-east-1.amazonaws.com/prod/health" -UseBasicParsing).Content

# DLQ check
aws sqs get-queue-attributes `
  --queue-url "https://sqs.us-east-1.amazonaws.com/911414006786/rag-ingestion-dlq" `
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --profile rag-dev

# Other scripts
python langsmith_eval.py
python reset_document.py --list
$env:AWS_PROFILE = "rag-dev"
```

---

## 15. Cost Breakdown

```
OpenSearch (t3.small): ~$50–100/month  ← DELETE when not using! (currently deleted)
Bedrock Haiku 4.5:     ~$1–3/month (estimate)
Cohere Embed v4:       ~$1/month (estimate)
Cohere Rerank:         ~$0.001/query
Secrets Manager:       ~$0.80/month
Everything else (Lambda/S3/SQS/DynamoDB/API GW/Cognito/CloudFront/CW/X-Ray): ~$0 (free tier)
TOTAL active:          ~$53–107/month
TOTAL idle:            ~$2–4/month
```
