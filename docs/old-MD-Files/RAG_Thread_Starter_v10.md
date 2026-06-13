# RAG Pipeline — Thread Starter Summary
**Date:** June 12, 2026  
**Branch:** `feature/langchain-langgraph`  
**Repo:** `github.com/sudheerch1/rag-pipeline`

---

## Current State

Production-grade RAG pipeline POC on AWS for legal document Q&A. Built with LangGraph (ingestion) + LangChain LCEL (query). Demo delivered June 4 to ~20 engineers/architects.

### Model Stack
| Component | Model |
|---|---|
| Embeddings | Cohere Embed v4 (1536-dim) |
| LLM (HyDE/HyQ/Answer) | Claude Haiku 4.5 |
| Reranker | Cohere Rerank 3.5 |
| Eval judge | Claude Haiku 4.5 |

### Best Query Config (current)
| Parameter | Value |
|---|---|
| `top_k` | 15 |
| `top_n` (reranker) | 5 |
| `CONTEXT_TOKEN_BUDGET` | 3000 |
| Hybrid weights (semantic/BM25) | 0.5 / 0.5 |
| HyQ questions | 3 |

---

## Best Eval Scores (50 questions, 3 legal docs)

| Metric | Baseline (Titan+Nova, 8q) | Current Best (Cohere+Haiku, 50q) |
|---|---|---|
| Answer Correctness | 0.50 | **0.94** |
| Faithfulness | 1.00 | **0.96** |
| Answer Relevance | 0.62 | **0.88** |
| Context Precision | 0.45 | **0.44** |

---

## Experiment History

| Experiment | Result | Kept? |
|---|---|---|
| top_k 10→15 | Improved recall | ✅ Yes |
| top_n 5→7 | Hurt context_precision (0.44→0.34) | ❌ Reverted |
| budget 2000→3000 | Improved correctness | ✅ Yes |
| hybrid weights 0.7/0.3 | No improvement (legal text needs BM25) | ❌ Reverted |

**Key finding:** Context precision is resistant to retrieval tuning. Real fix is chunking (Phase C).

---

## Documents Ingested

| Document | Chunks |
|---|---|
| `johnson_v._superior_court_1.pdf` | 77 |
| `raceway_ford_cases.pdf` | 60 |
| Amiodarone case | ~X |

---

## Infrastructure

- **OpenSearch:** `rag-vectors` domain — **currently DELETED** (recreate before next session)
- **Lambda functions:** `rag-query`, `rag-document-processor`
- **S3 buckets:** `rag-raw-911414006786`, `rag-processed-911414006786`, `rag-frontend-911414006786`
- **API Gateway:** `k0injz47nk`
- **Cognito pool:** `us-east-1_carOA35Jg`
- **CloudFront:** `d2hsci73dlmw9w.cloudfront.net`
- **AWS account:** `911414006786`, region `us-east-1`, profile `rag-dev`
- **Lambda IAM role:** `arn:aws:iam::911414006786:role/service-role/rag-document-processor-role-ucg8h778`

---

## Lambda Deploy Process

⚠️ Always use the `package/` folder — pre-built Linux arm64 binaries live there.

```powershell
Copy-Item *.py .\package\
Compress-Archive -Path .\package\* -DestinationPath query_lambda.zip -Force
aws s3 cp query_lambda.zip s3://rag-raw-911414006786/query_lambda.zip --profile rag-dev --region us-east-1
aws lambda update-function-code --function-name rag-query `
  --s3-bucket rag-raw-911414006786 --s3-key query_lambda.zip `
  --profile rag-dev --region us-east-1
```

---

## OpenSearch Recreate Checklist

```
1. Create cluster (15 min)
2. Update endpoint in Parameter Store
3. python create_index.py
4. python setup_hybrid_search.py
5. Map IAM backend role in OpenSearch Dashboards
   → arn:aws:iam::911414006786:role/service-role/rag-document-processor-role-ucg8h778
6. Upload PDFs to S3 one at a time
7. Verify chunk counts in DynamoDB
8. Run test query, confirm LangSmith trace
```

---

## What We're Doing This Weekend

1. **Write ADRs** — document all architectural decisions with interview-ready reasoning
2. **Update DECISIONS.md** in repo with experiment results
3. **Move on** to next project: Case Intelligence Assistant (NJ Judiciary multi-agent POC)

---

## Remaining Experiments (for reference, not priority)

| # | Experiment | Requires re-ingestion? |
|---|---|---|
| B1 | Per-document scoring in `langsmith_eval.py` | No |
| A4 | HyQ 3→5 | Yes |
| C1 | similarity_threshold 0.75→0.70 | Yes |
| C2 | MAX_TOKENS 448→512 | Yes |

---

## Key Files

| File | Role |
|---|---|
| `query_graph.py` | Query Lambda — LCEL pipeline |
| `ingestion_graph.py` | Ingestion Lambda — LangGraph nodes |
| `langsmith_eval.py` | 50-question eval script |
| `chunker.py` | Semantic chunking |
| `llm_clients.py` | Cohere Embed v4 + Haiku 4.5 clients |
| `setup_hybrid_search.py` | Hybrid search pipeline (0.5/0.5 weights) |
| `create_index.py` | OpenSearch index creation |
| `reset_document.py` | Clear DynamoDB hash for re-ingestion |

---

## How to Use This File

Upload this file at the start of a new thread. Tell Claude:
- What you want to work on (ADRs, next experiments, new project)
- Current OpenSearch status (deleted/active)

*Version: v10 | June 12, 2026*
