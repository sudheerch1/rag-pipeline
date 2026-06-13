# RAG Pipeline — Thread Starter v11
**Date:** June 13, 2026  
**Branch:** `feature/langchain-langgraph`  
**Repo:** `github.com/sudheerch1/rag-pipeline`  
**Status:** ✅ POC COMPLETE — Moving to next project

---

## Current State

Production-grade RAG pipeline POC on AWS for legal document Q&A. Built with LangGraph (ingestion) + LangChain LCEL (query). Demo delivered June 4 to ~20 engineers/architects. All documentation committed to GitHub.

---

## Model Stack

| Component | Model |
|---|---|
| Embeddings | Cohere Embed v4 (1536-dim) |
| LLM (HyDE / HyQ / Answer) | Claude Haiku 4.5 |
| Reranker | Cohere Rerank 3.5 |
| Eval judge | Claude Haiku 4.5 |

---

## Best Query Config

| Parameter | Value |
|---|---|
| `top_k` | 15 |
| `top_n` (reranker) | 5 |
| `CONTEXT_TOKEN_BUDGET` | 3000 |
| Hybrid weights (semantic/BM25) | 0.5 / 0.5 |
| HyQ questions per chunk | 3 |

---

## Best Eval Scores (50 questions, 3 legal docs)

| Metric | Baseline (Titan+Nova, 8q) | Current Best (Cohere+Haiku, 50q) |
|---|---|---|
| Answer Correctness | 0.50 | **0.94** |
| Faithfulness | 1.00 | **0.96** |
| Answer Relevance | 0.62 | **0.88** |
| Context Precision | 0.45 | **0.44** |

---

## Infrastructure

- **OpenSearch:** `rag-vectors` — **DELETED** (recreate using `docs/OpenSearch_Reactivation_Runbook.md`)
- **Lambda:** `rag-query`, `rag-document-processor`
- **S3:** `rag-raw-911414006786`, `rag-processed-911414006786`, `rag-frontend-911414006786`
- **API Gateway:** `k0injz47nk`
- **Cognito:** `us-east-1_carOA35Jg`
- **CloudFront:** `d2hsci73dlmw9w.cloudfront.net`
- **AWS account:** `911414006786`, region `us-east-1`, profile `rag-dev`
- **Lambda IAM role:** `arn:aws:iam::911414006786:role/service-role/rag-document-processor-role-ucg8h778`

---

## Documents Ingested

| Document | Chunks |
|---|---|
| `johnson_v._superior_court_1.pdf` | 77 |
| `raceway_ford_cases.pdf` | 60 |
| Amiodarone case | ~unknown |

---

## What's in the Repo (`docs/`)

| File | Contents |
|---|---|
| `DECISIONS_ADR.md` | 23 ADRs — full format, all phases |
| `IaC_Deployment_Guide.md` | CloudFormation recreate playbook |
| `rag-stack.yaml` | Complete CFN template |
| `OpenSearch_Reactivation_Runbook.md` | Standalone OpenSearch recreate checklist |
| `GSI_vs_LSI_Explainer.md` | DynamoDB index concept + interview answer |
| `old-MD-Files/` | All thread summaries v2–v10 |

---

## Dependency Management

```powershell
# Lambda deps (Linux arm64) — recreate package/ folder:
pip install -r requirements-lambda.txt --target .\package\ `
  --platform manylinux2014_aarch64 --implementation cp `
  --python-version 3.11 --only-binary=:all:

# Local dev deps:
pip install -r requirements-dev.txt
```

---

## Lambda Deploy

```powershell
Copy-Item *.py .\package\
Compress-Archive -Path .\package\* -DestinationPath query_lambda.zip -Force
aws s3 cp query_lambda.zip s3://rag-raw-911414006786/query_lambda.zip --profile rag-dev --region us-east-1
aws lambda update-function-code --function-name rag-query `
  --s3-bucket rag-raw-911414006786 --s3-key query_lambda.zip `
  --profile rag-dev --region us-east-1
```

---

## Remaining Experiments (low priority — not blocking next POC)

| # | Experiment | Requires re-ingestion? |
|---|---|---|
| B1 | Per-document scoring in `langsmith_eval.py` | No |
| A4 | HyQ 3→5 questions | Yes |
| C1 | similarity_threshold 0.75→0.70 | Yes |
| C2 | MAX_TOKENS 448→512 | Yes |

---

## Next Project

**Case Intelligence Assistant — NJ Judiciary**  
Multi-agent system: Case Research + Document Analysis + Risk Assessment + Scheduler sub-agents  
Stack: AWS Lambda + Bedrock, OpenSearch hybrid kNN+BM25  
Integrations: NJ eCourts, CourtListener, stubbed Westlaw/Lexis

---

## How to Use This File

Upload at the start of a new thread. Tell Claude:
- What you want to work on
- Current OpenSearch status (deleted/active)

*Version: v11 | June 13, 2026*
