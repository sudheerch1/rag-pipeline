# Production-Grade RAG Pipeline on AWS — Updated Project Summary
## For continuing in a new thread (Updated: May 21, 2026)

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
**Demo Date:** May 21, 2026 (Ingestion pipeline — DONE)
**Demo 2 Date:** June 4, 2026 (Query pipeline)
**Live Demo URL:** https://d2hsci73dlmw9w.cloudfront.net

---

## 2. Current Git Branch Status

```
main                        ← working v1 code, fallback if migration fails
feature/langchain-langgraph ← active branch, LangChain/LangGraph migration in progress
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
  rag-processed-911414006786     ← processed chunks JSON
  rag-frontend-911414006786      ← React frontend static files

SQS:
  rag-ingestion-queue            ← S3 event notifications
  rag-ingestion-dlq              ← dead letter queue for failures
  ⚠️ Visibility Timeout: 300 seconds (MUST set after every recreation)

Lambda:
  rag-document-processor         ← ingestion pipeline (128MB, 300s timeout)
  rag-query                      ← query pipeline (512MB, 30s timeout)
  Handler: handler.lambda_handler
  Handler: query_handler.lambda_handler
  IAM Role: arn:aws:iam::911414006786:role/service-role/rag-document-processor-role-ucg8h778

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

### Ingestion Pipeline
```
PDF Upload
  → S3 (rag-raw-911414006786)
  → S3 Event Notification
  → SQS (rag-ingestion-queue)
  → Lambda (rag-document-processor)
      → Download file
      → SHA-256 hash → DynamoDB GSI duplicate check
      → detector.py: magic bytes → file type
      → Unknown file check → save rejected + return cleanly
      → parsers.py: bytes → List[ParsedBlock]
          PDF: paragraph-level blocks with section tracking (UPDATED — see Section 7)
      → chunker.py: semantic chunking → List[Chunk]
          embed blocks via Titan → cosine similarity → split at topic boundaries
          MAX_TOKENS=448, OVERLAP=64 → final chunk max 512 tokens
      → embed_chunks(): Titan Embeddings → 1536-dim vector per chunk
      → generate_hyq_questions(): Nova Micro → 3 HyQ questions per chunk
      → delete_old_chunks(): remove stale OpenSearch docs
      → store_in_opensearch(): index text + embedding + hyq + metadata
      → save_metadata(): DynamoDB write
      → S3 processed: save chunks JSON backup
```

### Query Pipeline
```
User Question
  → React UI (CloudFront HTTPS)
  → Cognito auth (JWT token)
  → API Gateway (API Key + JWT validation)
  → Lambda (rag-query)
      → Step 1: HyDE: Nova generates hypothetical answer → embed it
      → Step 2: Hybrid Search: BM25 + KNN → top 10
      → Step 3: Format chunks
      → Step 4: Cohere Rerank 3.5 → best 5
      → Step 4b: Deduplicate by page
      → Step 5: Nova Micro → grounded answer
  → JSON response with answer + sources
```

---

## 5. Local Project Paths

```
Backend:   C:\Tech\PROD-Grade-POC\RAG\rag-pipeline\
Frontend:  C:\Tech\PROD-Grade-POC\rag-frontend\
Dataset:   C:\Tech\PROD-Grade-POC\Dataset\
```

---

## 6. Python Files — Current State

| File | Status | Notes |
|---|---|---|
| `handler.py` | v1 — to be replaced | Will become `ingestion_graph.py` (LangGraph) |
| `query_handler.py` | v1 — to be replaced | Will become `query_graph.py` (LangChain LCEL) |
| `parsers.py` | ✅ UPDATED | parse_pdf() rewritten — see Section 7 |
| `chunker.py` | v1 — minor update needed | One function swap for LangChain embeddings |
| `detector.py` | ✅ Keep as-is | Magic byte detection, no changes needed |
| `models.py` | ✅ Keep as-is | ParsedBlock and Chunk dataclasses, no changes needed |
| `config.py` | ✅ UPDATED | Added module-level caching — see Section 8 |
| `test_parser.py` | ✅ NEW | Local validation script for parse_pdf() |

---

## 7. parse_pdf() — What Changed and Why

### Problem with old version
pypdf extracts text line by line. The old parser accumulated ALL lines on a page and flushed once at end of page — so one page = one block regardless of how many paragraphs were on it.

### What the new version does
Parser now flushes a paragraph whenever it encounters a line ending with `.`, `!`, or `?`. This means each paragraph becomes its own block, not each page.

### Result on test PDF
```
Before: 75 blocks (one per page roughly)
After:  116 blocks (one per paragraph)

Sections detected: GEOGRAPHICAL AREA, NUMBER NINETEEN, Syllabus, Procedural History, Opinion
Token distribution: min 10, max 254, avg 63.3
```

### Key logic changes
```python
# OLD — flush once per page
flush_paragraph()  # called at end of page loop

# NEW — flush when sentence ends mid-page
ends_sentence = line[-1] in '.!?'
current_para_lines.append(line)
if ends_sentence:
    flush_paragraph(i + 1, current_section)
```

### SKIP_PATTERNS — made generic
The old version had hardcoded patterns for the Connecticut legal case file. Replaced with generic patterns that work for any PDF:
```python
SKIP_PATTERNS = re.compile(
    r'^('
    r'Page \d+'                 # "Page 1", "Page 23"
    r'|\d+\s*$'                 # lone page numbers
    r'|Copyright\s'             # copyright footers
    r'|All rights reserved'
    r'|Confidential'
    r'|www\.'
    r'|http[s]?://'
    r'|\S+@\S+\.\S+'           # email addresses in footers
    r')',
    re.IGNORECASE
)
```

### Heading detection improvement
Added `has_no_punctuation` check — prevents mid-sentence line wraps from being misclassified as headings:
```python
has_no_punctuation = not any(c in line for c in '.,;:()')
if (is_titlecase or is_uppercase) and has_no_digits and has_no_punctuation:
    # treat as heading
```

---

## 8. config.py — What Changed and Why

Added module-level caching so SSM and Secrets Manager are only called once per Lambda container lifetime, not on every invocation.

```python
# config.py
import boto3
import json

_cache = {}  # lives for lifetime of Lambda container

def get_parameter(name, profile_name=None):
    if name in _cache:
        return _cache[name]
    session = boto3.Session(profile_name=profile_name)
    client = session.client('ssm', region_name='us-east-1')
    response = client.get_parameter(Name=name)
    value = response['Parameter']['Value']
    _cache[name] = value
    return value

def get_opensearch_endpoint(profile_name=None):
    return get_parameter('/rag/opensearch/endpoint', profile_name)

def get_opensearch_credentials(profile_name=None):
    cache_key = 'rag/opensearch/credentials'
    if cache_key in _cache:
        return _cache[cache_key]
    session = boto3.Session(profile_name=profile_name)
    client = session.client('secretsmanager', region_name='us-east-1')
    response = client.get_secret_value(SecretId=cache_key)
    secret = json.loads(response['SecretString'])
    result = secret['OPENSEARCH_USER'], secret['OPENSEARCH_PASSWORD']
    _cache[cache_key] = result
    return result
```

Also fixed call site in handler.py and query_handler.py — moved `get_opensearch_endpoint()` inside `get_opensearch_client()` so it's called lazily, not at module load time.

---

## 9. LangChain + LangGraph Migration Plan

### What LCEL is
LangChain Expression Language — chains components with `|` pipe operator:
```python
chain = prompt | llm | StrOutputParser()
result = chain.invoke({"question": "..."})
# same as: parse(llm(prompt.format(question)))
```
Every `|` step is automatically traced in LangSmith.

### Which tool for which file

| File | Tool | Reason |
|---|---|---|
| `handler.py` → `ingestion_graph.py` | LangGraph | Has branching — duplicate/rejected/failed exits at each step |
| `query_handler.py` → `query_graph.py` | LangChain LCEL | Linear pipeline, no branching needed |
| `chunker.py` | LangChain | Swap one embedding function call |
| `parsers.py` | Nothing | Keep as-is |
| `detector.py` | Nothing | Keep as-is |

### New files to create

```
llm_clients.py          ← shared BedrockEmbeddings + ChatBedrock instances (cached)
opensearch_client.py    ← extracted from handler.py, shared by both graphs
ingestion_graph.py      ← replaces handler.py (LangGraph)
query_graph.py          ← replaces query_handler.py (LangChain LCEL)
```

### Migration sequence (do in this order)

```
Step 1: pip install langchain langchain-aws langchain-core langgraph langsmith
        pip freeze > requirements.txt

Step 2: Create llm_clients.py
        Verify: python -c "from llm_clients import get_embeddings, get_llm; print('ok')"

Step 3: Create opensearch_client.py (extract from handler.py)

Step 4: Update chunker.py — swap get_embedding_bedrock() only

Step 5: Create query_graph.py (LangChain LCEL)
        Test locally with test_query_lambda.py

Step 6: Create ingestion_graph.py (LangGraph)
        Test with test_parser.py first, then re-ingest PDF

Step 7: Deploy both Lambdas, verify LangSmith traces appear
```

### LangSmith setup
Add these to Lambda environment variables:
```
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=ls__your_key_here
LANGCHAIN_PROJECT=rag-pipeline
```
Once set, every invoke() call automatically appears in LangSmith dashboard — no extra code needed.

---

## 10. Lambda Package Size Considerations

```
Current (v1):   ~15-20 MB uncompressed
After LangChain: ~80-120 MB uncompressed
Lambda limit:    250 MB uncompressed (via S3 upload)
```

Check actual size after installing:
```powershell
(Get-ChildItem -Recurse package\ | Measure-Object -Property Length -Sum).Sum / 1MB
```

If over 250 MB, use Lambda Layers:
```
Layer 1: LangChain stack (langchain, langchain-aws, langgraph, langsmith)
Layer 2: Existing deps (pypdf, opensearch-py, etc.)
Function: just your code files
```

---

## 11. test_parser.py — Local Validation Script

Located at: `C:\Tech\PROD-Grade-POC\RAG\rag-pipeline\test_parser.py`

```powershell
# Run against default PDF
python test_parser.py

# Run against any PDF
python test_parser.py --file "C:\path\to\any.pdf"
```

Output sections:
- Block summary (total, headings, text blocks)
- Sections detected with page numbers
- Token distribution histogram
- Blocks per section
- First 5 and last 3 text blocks (spot check content)
- Potential issues (blocks over 448 tokens, blocks with no section)

---

## 12. Immediate Next Steps

```
1. ✅ Parser updated and validated (116 blocks, correct sections)
2. ✅ Git branch created: feature/langchain-langgraph
3. ⏳ Activate OpenSearch (needed for re-ingestion and June 4 demo)
4. ⏳ Re-ingest johnson_v._superior_court_1.pdf with updated parser
5. ⏳ Run test_query_lambda.py — verify retrieval quality improved
6. ⏳ Start LangChain migration — begin with llm_clients.py
```

---

## 13. Key Commands

### Deploy ingestion Lambda
```powershell
cd C:\Tech\PROD-Grade-POC\RAG\rag-pipeline
Remove-Item lambda_package.zip
Compress-Archive -Path handler.py, detector.py, parsers.py, chunker.py, models.py, config.py -DestinationPath lambda_package.zip
Compress-Archive -Path .\package\* -Update -DestinationPath lambda_package.zip
aws lambda update-function-code --function-name rag-document-processor --zip-file fileb://lambda_package.zip --profile rag-dev
```

### Deploy query Lambda
```powershell
Remove-Item query_package.zip
Compress-Archive -Path query_handler.py, models.py, config.py -DestinationPath query_package.zip
Compress-Archive -Path .\package\* -Update -DestinationPath query_package.zip
aws lambda update-function-code --function-name rag-query --zip-file fileb://query_package.zip --profile rag-dev
```

### Recreate OpenSearch (full checklist)
```powershell
# 1. Map Lambda role in Dashboards:
#    Security → Roles → all_access → Mapped users → add:
#    arn:aws:iam::911414006786:role/service-role/rag-document-processor-role-ucg8h778

# 2. Create index
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
aws logs tail /aws/lambda/rag-query --follow --profile rag-dev
aws dynamodb scan --table-name rag-metadata --profile rag-dev --max-items 5
```

---

## 14. Cost Breakdown

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

## 15. Key Technical Decisions

| Decision | Why |
|---|---|
| LangGraph for ingestion | Branching logic — duplicate/rejected/failed exits at each step |
| LangChain LCEL for query | Linear pipeline, no branching, cleaner code |
| Keep parsers.py as-is | Custom paragraph-level PDF parsing is better than LangChain loaders |
| Keep detector.py as-is | Magic byte detection has no LangChain equivalent |
| Paragraph-level PDF parsing | One paragraph = one block = better chunk boundaries |
| Generic SKIP_PATTERNS | Old patterns were hardcoded to one specific legal PDF |
| config.py caching | SSM/Secrets Manager called once per container, not per invocation |
| MAX_TOKENS=448 | 448 + 64 overlap = 512 correct max |
| Cosine similarity over dot product | Ignores magnitude, correct for high-dim vectors |
| HyDE at query time | Answer embeddings closer to real answers in vector space |
| HyQ at ingestion | Question-to-question matching stronger than question-to-text |
| Cohere Rerank | Cross-encoder sees Q+chunk together, more accurate than bi-encoder |
| IAM role not access keys | Temporary STS credentials, auto-rotate |
| SQS visibility timeout 300s | Default 30s < Lambda ~35s processing → caused duplicates |
