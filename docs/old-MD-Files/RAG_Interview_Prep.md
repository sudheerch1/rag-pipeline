# RAG Pipeline — Interview Preparation Guide
**Author:** Sudheer Cherukuri  
**Based on:** Production-Grade RAG Pipeline POC on AWS (~80 hours)  
**Stack:** Python 3.11 · LangChain/LangGraph · AWS Bedrock · OpenSearch · Lambda · React

---

## How to Use This Guide

This guide is structured as questions an interviewer will realistically ask, with the answer you should be able to give **in your own words**. For each concept: understand the *why*, not just the *what*.

---

## Part 1: RAG Fundamentals

### Q: What is RAG and why does it exist?
Large language models have a knowledge cutoff and no access to private documents. RAG (Retrieval-Augmented Generation) solves both problems by retrieving relevant chunks of real documents at query time and grounding the model's answer in that context. The model generates, but it doesn't hallucinate — it synthesizes from what you hand it.

### Q: What are the two main phases of a RAG pipeline?
**Ingestion (offline):** Parse documents → chunk them → generate embeddings → store in a vector database.  
**Query (online):** Embed the user's question → search the vector store → retrieve relevant chunks → generate an answer grounded in those chunks.

### Q: What is chunking and why does chunk size matter?
Chunking splits a long document into smaller pieces so each piece can be embedded individually. Chunk size is a tradeoff:
- **Too small:** Individual chunks lack enough context for the model to generate a useful answer.
- **Too large:** Chunks include irrelevant content, polluting the retrieved context window.
- **This project:** 448-token chunks with 64-token overlap. Overlap prevents information loss at chunk boundaries — a sentence that straddles two chunks isn't lost.

### Q: What is a vector embedding?
A high-dimensional numeric representation of text where semantically similar texts are close together in vector space. This enables search by *meaning* rather than keyword match. This project uses Amazon Titan Embed v1 (1536 dimensions).

### Q: What is cosine similarity vs dot product in vector search?
- **Cosine similarity:** Measures angle between vectors, independent of magnitude. Good for normalized embeddings.
- **Dot product:** Measures both angle and magnitude. Faster, but magnitude-sensitive.
- **This project:** OpenSearch HNSW index configured with cosine similarity — appropriate since Titan embeddings are not normalized by default.

---

## Part 2: Advanced Retrieval Techniques

### Q: What is HyDE and why did you use it?
**Hypothetical Document Embeddings.** A user's question ("What was Johnson convicted of?") and the stored chunk text live in different parts of vector space. Questions are sparse; answers are dense. HyDE bridges this gap:
1. Ask Nova Micro to generate a *hypothetical answer* to the question
2. Embed that hypothetical answer instead of the raw question
3. Search with the richer answer-shaped vector

**Tradeoff:** The hypothetical answer may hallucinate. But the embedding still captures the *semantic neighborhood* of a real answer, producing better retrieval than embedding the sparse question directly.

### Q: What is HyQ (Hypothetical Questions)?
The opposite direction from HyDE. At **ingestion time**, for each chunk, you ask Nova Micro to generate 3 hypothetical questions that the chunk could answer. These questions are stored alongside the chunk in OpenSearch.

At query time, the user's question is matched against these stored questions via BM25 keyword search. A question-to-question match is a stronger signal than a question-to-paragraph match.

**This project's limitation:** HyQ questions are matched via BM25 (lexical). A production upgrade would embed the questions and enable semantic kNN search against them.

### Q: What is hybrid search and why is it better than pure vector search?
- **Pure KNN (vector search):** Finds semantically similar chunks. Misses exact legal/technical terms that don't have close embeddings.
- **Pure BM25 (keyword search):** Finds exact term matches. Misses semantic meaning — "dismissed" vs "denied" return different results even if they mean the same thing in context.
- **Hybrid:** Runs both in parallel, normalizes scores (min-max), and combines them. You get both semantic coverage and lexical precision.

**This project:** OpenSearch hybrid-search-pipeline with `hyq_questions^2.0` — the HyQ questions field is boosted 2× because a question-to-question match is a higher-quality signal than a question-to-body-text match.

### Q: What is a cross-encoder reranker and why does it outperform vector similarity?
- **Bi-encoder (KNN):** Encodes the question and each chunk *separately* into vectors, then compares them. Fast, but the two texts never "see" each other.
- **Cross-encoder (Cohere Rerank 3.5):** Takes the question and chunk *together* in a single attention pass. The model can see cross-text interactions — e.g., a pronoun in the chunk that refers to a term in the question.

**Why not use cross-encoders for initial search?** Too slow. You can't run a cross-encoder over thousands of chunks. So: OpenSearch narrows the field to 10 → Cohere deeply judges those 10 → return top 5.

### Q: What is deduplication in your pipeline and why do you need it?
After reranking, multiple top chunks may come from the same page of the document. If they all enter the context window, one page dominates the answer. The dedup step removes chunks with duplicate page numbers, ensuring source diversity in the final context.

---

## Part 3: Evaluation & Observability

### Q: Walk me through your LangSmith evaluation setup.
I created an evaluation dataset (`johnson-v-superior-court-eval`) with 8 question/expected-answer pairs derived from the test document. I then ran 4 evaluators:

| Evaluator | What it measures | Inputs |
|---|---|---|
| `faithfulness` | Did the model hallucinate? | question + chunks + answer |
| `answer_relevance` | Did the answer address the question? | question + answer |
| `answer_correctness` | Was the answer factually right vs expected? | question + answer + expected |
| `context_precision` | Were retrieved chunks actually useful? | question + chunks |

The pattern is **LLM-as-a-Judge**: Nova Micro does the actual evaluation scoring; LangSmith records and visualizes the scores.

### Q: Your faithfulness is 1.00 but correctness is 0.50 — what does that tell you?
This is the most interesting result in the project. **The problem is retrieval, not generation.**

Nova Micro never hallucinated — every claim in every answer was grounded in retrieved chunks (faithfulness = 1.00). But for 4 questions, the *wrong* chunks were retrieved. Nova correctly responded "I cannot find this in the provided context" — which is the right behavior, but scores 0 on correctness.

The fix is improving retrieval: higher `top_k`, better HyQ question quality, or embedding the HyQ questions for semantic search instead of BM25-only.

### Q: What is your three-layer observability stack?
```
CloudWatch Dashboard  → Infrastructure health (Lambda errors, duration, DLQ depth)
AWS X-Ray             → Latency breakdown across AWS service boundaries
LangSmith             → Per-AI-step visibility (tokens, latency, I/O per pipeline step)
```
These are complementary, not redundant. CloudWatch tells you *if* something is broken. X-Ray tells you *where* time is spent at the infrastructure level. LangSmith tells you *what each AI step did* with that time.

### Q: What did X-Ray reveal about your Lambda performance?
Cold start = 5.31s out of a total 11.63s wall clock time. The first Bedrock call (run_hyde) pays the HTTPS connection setup cost — visible as a latency spike vs subsequent calls. In production you'd eliminate this with provisioned concurrency.

### Q: What is structured logging and why does it matter?
Instead of `print("Processing done")`, you emit JSON: `{"event": "processing_complete", "chunks": 56, "duration_ms": 340}`. Every field is queryable in CloudWatch Logs Insights. You can filter by `event`, by `document_id`, by `error_type` — without parsing free-text strings. Both Lambdas in this project emit JSON structured logs.

---

## Part 4: Architecture & AWS

### Q: Why LangGraph for ingestion and LCEL for query?
**LangGraph** is graph-based — it supports branching (duplicate detected → skip to END, unknown file type → rejected → END). The ingestion pipeline has 3 conditional branches, which map naturally to a directed graph with decision nodes.

**LCEL (LangChain Expression Language)** is a linear pipe — ideal for the query pipeline, which is a sequential chain: HyDE → search → rerank → dedup → answer. No branching needed, so the simpler abstraction is the right choice.

### Q: Why SQS between S3 and Lambda for ingestion?
Direct S3-to-Lambda invocation is synchronous — if Lambda fails, the event is lost. SQS provides:
- **Durability:** Events survive Lambda crashes
- **Retry logic:** Failed messages retry with backoff
- **DLQ:** After max retries, failed messages land in a Dead Letter Queue for investigation
- **Visibility timeout (300s):** Prevents another Lambda instance from picking up a message being processed

### Q: Why SHA-256 for duplicate detection instead of filename?
Filenames change — the same document renamed is still a duplicate. SHA-256 hashes the file *content*, so `document_v2_final_FINAL.pdf` is correctly identified as a duplicate if the bytes are the same. The hash is stored in DynamoDB with a GSI (`content-hash-index`) enabling O(1) lookups — no full table scan.

### Q: What is your token budget and why does it matter?
After reranking and dedup, chunks are filled into the context window greedily until they exceed 2000 tokens (measured as characters / 4 chars-per-token). This caps Nova's context to a predictable size — bounded cost, bounded latency, no prompt explosions on large documents.

**Known nuance:** The API response `sources` field reflects all post-rerank chunks, not just chunks that made it through the token budget. Worth knowing if asked.

### Q: Why `lru_cache` on the Bedrock client?
Lambda containers can be reused across invocations ("warm" containers). By caching the Bedrock client with `lru_cache(maxsize=1)`, the HTTPS connection is reused — avoiding the setup cost on every invocation. The first call to Bedrock pays the connection setup; subsequent warm calls reuse it. Visible in LangSmith traces as a latency spike on `run_hyde` for cold starts.

### Q: Why Cognito implicit grant flow and what are its limitations?
The implicit grant flow is the simplest Cognito integration: browser redirects to the hosted UI, authenticates, and receives a JWT directly in the URL hash — no backend needed. React reads the token from `window.location.hash` and passes it with each API Gateway request.

**Limitation:** The implicit grant is deprecated in OAuth 2.1 because the token is exposed in the browser URL, referrer headers, and browser history. Production migration path: authorization code flow with PKCE, which exchanges a short-lived code for a token server-side.

### Q: Why did you start with raw boto3 instead of LangChain from the beginning?
Two reasons: (1) It demonstrates deeper understanding of what LangChain abstracts. LangChain still uses boto3 and AWS4Auth underneath — knowing that makes you a better debugger. (2) Building the working pipeline first, then migrating, shows architectural confidence rather than framework dependency.

---

## Part 5: Design Tradeoffs & "What Would You Do Differently"

### Q: What would you improve in production?

| Area | Current POC | Production Upgrade |
|---|---|---|
| Auth | Implicit grant (deprecated) | Authorization code flow + PKCE |
| Infra | Console-deployed | Terraform IaC |
| Network | Public endpoints | VPC + private subnets + WAF |
| HyQ matching | BM25 (lexical) | Embed questions → semantic kNN |
| Cold start | Provisioned concurrency: no | Provisioned concurrency for latency SLA |
| Confidence | No scoring | Threshold-based fallback ("I don't know") |
| Guardrails | None | Bedrock Guardrails for PII + toxicity |
| Memory | None | DynamoDB conversation history for multi-turn |

### Q: Why is context_precision 0.45 and how would you fix it?
Context precision measures whether the retrieved chunks were actually relevant. At 0.45, roughly half the retrieved chunks aren't contributing to correct answers. Root causes:
1. **top_k too low:** Retrieves 10 chunks → 5 after rerank → limited recall for some question types
2. **HyQ quality:** Nova-generated questions may not match the exact vocabulary of test questions
3. **BM25-only HyQ matching:** No semantic fallback if exact terms don't overlap

Fix priority: increase top_k → embed HyQ questions for semantic search → evaluate again.

### Q: What does your DLQ alarm demonstrate about production thinking?
The alarm fires within 1 minute of any message landing in the DLQ — operators get an SNS email before users notice degradation. **Silent failure is worse than noisy failure.** The 2 intentional DLQ messages in the demo show the observability working, not broken infrastructure.

---

## Part 6: LangChain/LangGraph Concepts

### Q: What is LCEL and what problem does it solve?
LangChain Expression Language is a declarative pipe syntax: `chain = prompt | llm | output_parser`. It provides automatic streaming, async support, and tracing without boilerplate. Each `|` operator composes runnables — the output of one step is the input to the next.

### Q: What is a Runnable in LangChain?
The base abstraction in LCEL. Anything that can be called with `.invoke()`, `.stream()`, or `.batch()`. Prompts, LLMs, output parsers, and custom functions wrapped in `RunnableLambda` are all Runnables — composable via `|`.

### Q: What is LangGraph and when would you choose it over LCEL?
LangGraph models workflows as directed graphs with nodes (functions) and edges (transitions), including conditional edges. Use LangGraph when:
- You need branching/routing logic
- You need cycles (retry loops, agent tool-use loops)
- You need checkpointing/persistence (long-running agents)

Use LCEL when: linear pipeline, no branching needed.

### Q: What is LangSmith vs LangGraph?
- **LangSmith:** Observability platform. Records traces, evaluations, token usage, latency. Does not execute AI logic.
- **LangGraph:** Workflow orchestration library. Executes AI pipelines as graphs. Sends traces *to* LangSmith.

---

## Part 7: Behavioral / Project Questions

### Q: What was the hardest technical problem you solved?
The X-Ray tracing fix. Traces were appearing in CloudWatch but not correlating with Lambda functions. Root cause: missing `OTEL_TRACES_EXPORTER=awsxray` environment variable on both Lambdas. AWS X-Ray requires OTEL to be explicitly directed to its exporter — the default OTEL exporter doesn't route to X-Ray automatically.

### Q: How did you validate the pipeline was working correctly?
Three layers: (1) Happy path — upload PDF, verify 75 blocks / 56 chunks in DynamoDB, query returns grounded answer. (2) Edge cases — duplicate PDF upload → DLQ, unknown file type → rejected branch, corrupted file → magic-byte detection catches it. (3) Automated evaluation — LangSmith 4-metric eval against 8 ground truth Q&A pairs.

### Q: What would you add next?
1. Embed HyQ questions (semantic kNN vs current BM25) — directly addresses the retrieval quality gap  
2. Conversation memory via DynamoDB — enables follow-up questions  
3. Confidence scoring — threshold-based "I don't know" instead of hallucinated answers  
4. Terraform IaC — reproducible infrastructure  
5. Agentic extension — multi-document reasoning, tool-using agents

---

## Quick Concept Glossary

| Term | One-Line Definition |
|---|---|
| RAG | Retrieval + Generation: retrieve relevant chunks, ground LLM answer in them |
| HyDE | Embed a hypothetical answer to improve vector search recall |
| HyQ | Pre-generate questions at ingestion; match question-to-question at query time |
| BM25 | TF-IDF-based keyword ranking; rewards rare-term exact matches |
| KNN | K-nearest neighbors in vector space; semantic similarity search |
| HNSW | Approximate nearest neighbor graph index (fast KNN at scale) |
| Cross-encoder | Model that sees Q+chunk together; more accurate than bi-encoder comparison |
| Bi-encoder | Model that encodes Q and chunk separately; fast but less accurate |
| LCEL | LangChain pipe syntax for composing LLM chains |
| LangGraph | Graph-based workflow orchestration for branching/cyclic AI pipelines |
| LangSmith | Observability and evaluation platform for LLM applications |
| LLM-as-a-Judge | Using an LLM to evaluate another LLM's output |
| Faithfulness | Does the answer contain only claims grounded in retrieved chunks? |
| Context Precision | Were the retrieved chunks actually relevant to the question? |
| DLQ | Dead Letter Queue — catches messages that failed all retries |
| OTEL | OpenTelemetry — vendor-neutral observability instrumentation standard |
| Provisioned Concurrency | Pre-warmed Lambda containers; eliminates cold starts |
| lru_cache | Python memoization; in Lambda, caches Bedrock client across warm invocations |
| SHA-256 | Content hash for deduplication; immune to filename changes |
| GSI | DynamoDB Global Secondary Index; enables O(1) lookup on non-primary key |
| PKCE | OAuth 2.1 pattern for secure authorization code flow without client secret |
