# Production-Grade RAG Pipeline on AWS

**Author:** Sudheer Cherukuri  
**Completed:** June 2026 | **Effort:** ~80 hours  
**Live Demo:** https://d2hsci73dlmw9w.cloudfront.net  
**GitHub:** [rag-pipeline (backend)](https://github.com/sudheerch1/rag-pipeline) · [rag-frontend](https://github.com/sudheerch1/rag-frontend)

---

## What This Is

A production-grade Retrieval-Augmented Generation (RAG) pipeline built entirely on AWS, demonstrating end-to-end document ingestion, hybrid semantic search, LLM-grounded answer generation, and multi-layer observability. Built as a portfolio POC targeting AI/cloud engineering roles.

Every design decision is intentional and defensible — not boilerplate from a tutorial.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        INGESTION PIPELINE                           │
│                                                                     │
│  PDF Upload → S3 → SQS → Lambda (LangGraph, 7 nodes)               │
│                                                                     │
│  node_download       → Fetch from S3 to /tmp/                       │
│  node_check_duplicate→ SHA-256 → DynamoDB GSI lookup                │
│  node_detect         → Magic-byte file type validation              │
│  node_parse          → Paragraph-level block extraction (pypdf)     │
│  node_chunk          → 448-token chunks / 64-token overlap          │
│  node_hyq            → 3 hypothetical questions per chunk (Nova)    │
│  node_embed          → Titan embeddings (1536 dim)                  │
│  node_store          → OpenSearch (vector + metadata) + DynamoDB    │
│                                                                     │
│  Branches: duplicate → END  |  unknown_type → rejected → END       │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                         QUERY PIPELINE                              │
│                                                                     │
│  React UI → Cognito JWT → API Gateway → Lambda (LCEL chain)         │
│                                                                     │
│  run_hyde    → Nova generates hypothetical answer → Titan embeds    │
│  run_search  → Hybrid: BM25 (text + hyq_questions^2) + KNN          │
│  run_rerank  → Cohere cross-encoder → top 5 of 10                  │
│  run_dedup   → Remove duplicate pages from context                  │
│  run_answer  → Token budget fill → Nova → grounded answer           │
│                                                                     │
│  Response: { answer, sources, context, latency_ms }                 │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

### AWS Services
| Service | Role |
|---|---|
| **S3** | PDF uploads, processed chunks JSON, frontend static files |
| **SQS** | Decoupled ingestion queue (300s visibility timeout) + DLQ |
| **Lambda** | Ingestion processor + query handler (Python 3.11, arm64, 512MB) |
| **DynamoDB** | Document metadata + SHA-256 content hash deduplication |
| **OpenSearch** | Vector index (FAISS/HNSW/cosine) + hybrid search pipeline |
| **API Gateway** | REST API with API key auth + usage plan |
| **Cognito** | User authentication (hosted UI, JWT) |
| **CloudFront + S3** | Frontend CDN delivery |
| **Bedrock** | Titan embeddings, Nova Micro (generation + evaluation), Cohere Rerank |
| **CloudWatch** | Metrics dashboard, structured log queries, DLQ alarm |
| **X-Ray** | Distributed tracing (cold start analysis, service map) |
| **SNS** | DLQ depth alarm → email notification |
| **Secrets Manager** | OpenSearch credentials |
| **Parameter Store** | OpenSearch endpoint |

### AI/ML Frameworks
| Framework | Role |
|---|---|
| **LangGraph** | Ingestion pipeline (branching workflow, 7 nodes) |
| **LangChain LCEL** | Query pipeline (linear chain) |
| **LangSmith** | Observability traces + LLM-as-a-Judge evaluation |

### Bedrock Models
| Model | Used For |
|---|---|
| `amazon.titan-embed-text-v1` | Text embeddings (1536 dimensions) |
| `amazon.nova-micro-v1:0` | HyQ generation, HyDE, answer generation, LLM-as-a-Judge |
| `cohere.rerank-v3-5:0` | Cross-encoder reranking |

---

## Key Design Decisions

### 1. HyDE — Hypothetical Document Embeddings
A user question and the answer it's looking for exist in different regions of vector space. HyDE generates a *hypothetical answer* at query time, embeds it, and searches with that richer vector. This bridges the vocabulary gap between sparse questions and dense document content.

### 2. HyQ — Hypothetical Questions at Ingestion
For each chunk, Nova generates 3 hypothetical questions the chunk could answer. These are stored in OpenSearch. At query time, the user's question is matched against these stored questions via BM25 — a question-to-question match is a stronger signal than a question-to-paragraph match.

### 3. Hybrid BM25 + KNN Search
Pure vector search misses exact legal/technical terms. Pure keyword search misses semantic meaning. The OpenSearch hybrid pipeline runs both in parallel, normalizes with min-max, and combines scores. HyQ questions are boosted 2× (`hyq_questions^2.0`).

### 4. Cross-Encoder Reranking (Cohere)
KNN scores questions and chunks independently (bi-encoder). Cohere Rerank processes them *together* in a single attention pass — enabling cross-text interaction. Narrows 10 candidates to 5 before context assembly.

### 5. LangGraph for Ingestion, LCEL for Query
LangGraph supports branching (duplicate → skip, unknown type → reject). The ingestion pipeline has 3 conditional branches — a directed graph is the right model. The query pipeline is purely sequential — LCEL's linear pipe is the simpler and correct choice.

### 6. SHA-256 Content Hashing for Deduplication
Filenames change; file content doesn't. SHA-256 hashes the bytes, DynamoDB GSI enables O(1) lookup. Rename the file, same hash, correctly rejected as a duplicate.

### 7. Token Budget (2000 tokens)
Greedily fills context up to a cap, bounding Nova's context window to a predictable size. Prevents runaway costs and latency on large documents.

### 8. Three-Layer Observability
```
CloudWatch Dashboard  → Is the system healthy? (infra metrics)
X-Ray Trace Map       → Where does time go? (service boundaries, cold start)
LangSmith Traces      → What did each AI step do? (tokens, latency, I/O)
LangSmith Evaluation  → How good are the answers and why? (4 metrics)
```

---

## Evaluation Results

Evaluated against `johnson-v-superior-court-eval` dataset (8 Q&A pairs, legal domain).

| Metric | Score | Interpretation |
|---|---|---|
| **Faithfulness** | **1.00** | Nova never hallucinated — all claims grounded in retrieved chunks |
| **Answer Relevance** | 0.62 | Answers generally addressed the question |
| **Answer Correctness** | 0.50 | 4/8 answers correct — retrieval failure, not generation failure |
| **Context Precision** | 0.45 | ~half of retrieved chunks were relevant |

**Key insight:** Faithfulness 1.00 + Correctness 0.50 = retrieval problem. Nova correctly said "I cannot find this" when wrong chunks were retrieved. The model did the right thing — it had nothing to work with.

---

## Observability Stack

### CloudWatch Dashboard
5 widgets: Lambda invocations, error rate, duration p50/p99, throttles, DLQ depth.  
DLQ alarm fires within 1 minute of any ingestion failure → SNS email.

### X-Ray Trace Map
```
Client → rag-query (Lambda Context) → rag-query (Lambda Function)
Lambda Context:  11.63s (includes cold start)
Lambda Function:  6.32s (actual handler execution)
Cold start cost:  5.31s
```

### LangSmith Per-Step Tracing
```
run_hyde    94 tokens  → 1.42s  (first Bedrock call — pays HTTPS setup)
run_search             → 0.21s
run_rerank             → 0.38s
run_dedup              → <0.01s
run_answer  524 tokens → 0.63s  (warm Bedrock connection reused)
```

---

## Production Hardening Roadmap

Items deliberately left out of this POC but documented for the upgrade path:

| Area | Current | Production Upgrade |
|---|---|---|
| Auth | Implicit grant (deprecated) | Authorization code + PKCE |
| Infra | Console-managed | Terraform IaC |
| Network | Public endpoints | VPC + private subnets + WAF |
| HyQ matching | BM25 (lexical) | Embed questions → semantic kNN |
| Cold starts | Cold Lambda | Provisioned concurrency |
| Confidence | No threshold | Bedrock Guardrails + fallback |
| Memory | None | DynamoDB conversation history |
| Monitoring | Manual setup | IaC-managed CloudWatch |

---

## Repository Structure

```
rag-pipeline/
├── ingestion_graph.py      # LangGraph ingestion pipeline (7 nodes)
├── query_graph.py          # LangChain LCEL query pipeline
├── langsmith_eval.py       # 4-metric LLM-as-a-Judge evaluation
├── llm_clients.py          # lru_cached Bedrock clients
├── opensearch_client.py    # AWS4Auth OpenSearch client
├── config.py               # Parameter Store config reader
├── parsers.py              # Paragraph-level PDF parser
├── detector.py             # Magic-byte file type detector
├── chunker.py              # 448-token chunker with overlap
├── create_index.py         # OpenSearch index setup
├── setup_hybrid_search.py  # Hybrid search pipeline setup
└── reset_document.py       # DynamoDB hash reset utility

rag-frontend/
├── src/
│   ├── App.tsx             # Main app, Cognito JWT handling
│   └── index.tsx           # Entry point
└── public/
```

---

## Cost Profile

| Service | Monthly Estimate |
|---|---|
| OpenSearch (t3.small) | ~$50–100 ← delete when not in use |
| Bedrock Titan Embeddings | ~$0.50 |
| Bedrock Nova Micro | ~$1–2 |
| Cohere Rerank | ~$0.001/query |
| Everything else | ~$0 (free tier) |
| **Total active** | **~$52–104/month** |
| **Total idle (OpenSearch deleted)** | **~$2–4/month** |

---

## Why This Project

Most RAG tutorials show you LangChain wrappers. This project starts one layer below:
- Raw boto3 + AWS4Auth before any framework abstraction
- Every pipeline step has a documented reason — not just "it's in the tutorial"
- Real evaluation with interpretable metrics, not vibes-based QA
- Observable failure modes, not just happy paths

The goal: understand the system well enough to debug it at 2am when it breaks in production.

---

*Built by Sudheer Cherukuri as a portfolio project for AI/cloud engineering career transition.*  
*Questions: [GitHub Issues](https://github.com/sudheerch1/rag-pipeline/issues)*
