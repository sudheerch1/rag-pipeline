# Architectural Decision Records — RAG Pipeline on AWS
**Author:** Sudheer Cherukuri  
**Project:** Production-Grade RAG Pipeline POC  
**Stack:** Python 3.11 · LangChain/LangGraph · AWS Bedrock · OpenSearch · Lambda  
**Updated:** June 2026

---

## ADR Format

Each record follows:
- **Status** — Current standing (Accepted / Superseded / Experimental)
- **Context** — The constraint or problem that forced a decision
- **Decision** — What was chosen
- **Options Considered** — Alternatives evaluated
- **Rationale** — Why this option over the others
- **Consequences** — What this decision enables and costs
- **Tradeoffs** — The honest engineering tension

---

# SECTION 1 — INFRASTRUCTURE

---

## ADR-001: SQS as Decoupling Layer Between S3 and Lambda

**Status:** Accepted

**Context:**  
PDFs are uploaded to S3 by users. Something must trigger the ingestion Lambda. The simplest option is S3 directly invoking Lambda via event notification. However, ingestion is a long-running, failure-prone process (Bedrock calls, OpenSearch writes, multi-step LangGraph pipeline). If Lambda fails mid-run, a direct S3 invocation loses the event.

**Decision:**  
S3 sends events to SQS; Lambda polls SQS. DLQ receives messages that fail after 3 retries.

**Options Considered:**

| Option | Description |
|---|---|
| S3 → Lambda directly | S3 event notification invokes Lambda synchronously |
| S3 → SQS → Lambda | S3 sends to SQS queue; Lambda polls queue |
| S3 → EventBridge → Lambda | Event routing via EventBridge rules |
| S3 → SNS → Lambda | Fan-out pattern |

**Rationale:**  
SQS provides exactly what's needed: event durability (message persists if Lambda crashes), built-in retry with backoff (maxReceiveCount=3), and a DLQ for forensics. EventBridge adds routing complexity that isn't needed for a single consumer. SNS fan-out is designed for multiple consumers — overkill here. Direct S3→Lambda has no retry, no DLQ, and loses the event on failure.

**Consequences:**  
- ✅ Failed ingestions retry automatically up to 3 times
- ✅ DLQ captures all unrecoverable failures — zero silent data loss
- ✅ Visibility timeout (300s) prevents double-processing during long runs
- ✅ SQS decouples upload rate from processing rate (natural backpressure)
- ❌ Adds ~1–5s latency from upload to ingestion start (acceptable for async pipeline)
- ❌ More infrastructure components to manage

**Tradeoffs:**  
Durability and observability vs simplicity. The extra SQS hop is worth it because ingestion failure without a DLQ is an invisible black hole — you'd never know a document wasn't indexed.

---

## ADR-002: DynamoDB for Metadata and Deduplication

**Status:** Accepted

**Context:**  
The pipeline needs to (a) store metadata about each ingested document (status, chunk count, file type), and (b) detect duplicate uploads without re-processing the same content. A relational database was considered but felt like overengineering for a KV access pattern.

**Decision:**  
DynamoDB table `rag-metadata` with `document_id` as partition key. SHA-256 content hash stored in a Global Secondary Index (`content-hash-index`) for O(1) duplicate detection.

**Options Considered:**

| Option | Description |
|---|---|
| DynamoDB with GSI | Hash-keyed table, content hash on GSI |
| DynamoDB with LSI | Content hash as sort key on LSI |
| RDS (PostgreSQL) | Relational DB, full SQL support |
| S3 manifest file | JSON file tracking processed documents |

**Rationale:**  
See ADR-003 for the full GSI vs LSI reasoning (this is the key architectural nuance).

RDS requires a VPC, subnet groups, connection pooling, and ongoing management — all disproportionate for simple KV operations. An S3 manifest file has no atomic operations (race conditions under concurrent uploads) and no secondary lookup capability.

DynamoDB's pay-per-request billing means near-zero cost at POC scale, with no capacity planning required.

**Consequences:**  
- ✅ O(1) duplicate lookup via GSI — no full table scan
- ✅ Serverless, no connection management
- ✅ Atomic writes prevent race conditions
- ❌ No complex queries (no joins, limited filter expressions)
- ❌ GSI eventually consistent — acceptable since deduplication runs once per upload

**Tradeoffs:**  
NoSQL simplicity vs relational expressiveness. Given the access patterns (lookup by document_id, lookup by content_hash), DynamoDB is the precise fit. No relational queries are needed.

---

## ADR-003: GSI vs LSI for Content Hash Deduplication

**Status:** Accepted

**Context:**  
DynamoDB offers two secondary index types. The duplicate detection query needs to look up documents by `content_hash` — a different attribute from the table's partition key (`document_id`). This requires a secondary index. The question is which type.

**Decision:**  
Global Secondary Index (GSI) on `content_hash` as partition key.

**Options Considered:**

| Option | Partition Key | Sort Key | Scope |
|---|---|---|---|
| GSI on content_hash | `content_hash` | (none) | Entire table — independent hash space |
| LSI on content_hash | `document_id` (same as table) | `content_hash` | One partition only |
| Scan + filter | — | — | No index, brute force |

**Rationale:**  
An LSI uses the **same partition key as the base table** (`document_id`) and adds a sort key. This means an LSI query must start with a known `document_id`. But the deduplication use case is the opposite: we have a `content_hash` and need to find whether *any* document in the table has that hash. That is a cross-partition lookup — impossible with an LSI.

A GSI has its **own independent partition key** (`content_hash`). The query goes directly to the GSI partition for that hash and returns instantly, regardless of what `document_id` it belongs to. That is exactly the access pattern needed.

LSIs also have a strict size constraint (10 GB per partition key item) and must be defined at table creation time with no ability to add later. GSIs can be added to existing tables and have no per-partition size limit.

**Consequences:**  
- ✅ O(1) deduplication lookup — hash → immediate result
- ✅ Correct semantic: cross-partition lookup by content
- ✅ GSI can be added/removed without table recreation
- ❌ GSI reads are eventually consistent (strong consistency not available)
- ❌ GSI writes consume additional write capacity (billed separately in provisioned mode)

**Tradeoffs:**  
GSI eventual consistency vs strong consistency. For deduplication, eventual consistency is acceptable: the race condition (two uploads of the same file within milliseconds) is vanishingly rare in practice, and the SQS visibility timeout provides a natural gate anyway.

**Interview-ready summary:**  
> "LSI requires you to know the partition key — it adds a sort key within a partition. GSI is independent — it has its own partition key and can look across the entire table. Our deduplication query needs to find a document by content hash when we don't know its document_id. That's a GSI use case, not an LSI use case."

---

## ADR-004: Lambda Architecture (arm64, 512MB, Shared IAM Role)

**Status:** Accepted

**Context:**  
Lambda functions need to be sized and configured. Decisions include architecture (x86 vs arm64), memory, timeout, and IAM role sharing.

**Decision:**  
arm64 architecture, 512MB memory. Both Lambda functions share one IAM role.

**Options Considered:**

| Option | Tradeoff |
|---|---|
| x86 vs arm64 | arm64 is ~20% cheaper and faster for Python workloads |
| 256MB vs 512MB vs 1024MB | 512MB balances cost vs headroom for LangChain overhead |
| Separate IAM roles per Lambda | More secure; more operational complexity |
| Shared IAM role | Simpler; slight over-permission |

**Rationale:**  
arm64 (Graviton2) runs Python numerics faster and costs less per GB-second — a straightforward win for any Python Lambda. The requirement to build Linux arm64 binaries (`--platform manylinux2014_aarch64`) is the tradeoff; documented in the deploy process.

512MB provides enough headroom for LangChain module loading (~150MB) and numpy arrays (1536-dim float32 = ~6KB per vector, trivial). 256MB caused OOM on LangGraph node initialization during testing.

Shared IAM role is a POC pragmatism. In production, each Lambda would have a minimal-permission role. For this POC, the operational simplicity outweighs the slight over-permission.

**Consequences:**  
- ✅ ~20% cost savings vs x86 for same workload
- ✅ No OOM errors at 512MB
- ❌ arm64 requires Linux arm64 dependency builds — extra deploy step
- ❌ Shared role is not least-privilege

**Tradeoffs:**  
Cost/performance vs operational complexity on the architecture side. Security principle of least privilege vs operational simplicity on the IAM side.

---

## ADR-005: S3 → Lambda Deployment (No Direct Upload)

**Status:** Accepted

**Context:**  
Lambda has a 50MB direct upload limit for deployment packages. The RAG pipeline packages (LangChain, numpy, boto3, opensearch-py) exceed this limit.

**Decision:**  
Upload deployment zip to S3 first, then update Lambda to reference the S3 key.

**Options Considered:**

| Option | Description |
|---|---|
| Direct zip upload | Simple but fails above 50MB |
| S3-based deployment | Upload to S3, reference in `update-function-code` |
| Lambda Layers | Split dependencies into a Layer |
| Container image | Package as Docker image, deploy via ECR |

**Rationale:**  
S3 deployment is the simplest extension of direct upload — same zip, different delivery mechanism, no code changes. Lambda Layers would split the package but add complexity to the build process and layer versioning. Container images would provide the most flexibility but add ECR, Docker build pipeline, and container size management overhead — disproportionate for a POC.

**Consequences:**  
- ✅ No package size limit (S3 allows 250MB unzipped)
- ✅ No code changes required
- ❌ Extra S3 upload step in deploy process
- ❌ Slightly slower deploy cycle

---

## ADR-006: CloudWatch + X-Ray + LangSmith Three-Layer Observability

**Status:** Accepted

**Context:**  
A RAG pipeline has two fundamentally different failure modes: infrastructure failures (Lambda OOM, API timeout, queue backup) and AI quality failures (wrong chunks retrieved, hallucinated answers). No single tool covers both.

**Decision:**  
Three-layer observability stack: CloudWatch for infrastructure, X-Ray for distributed tracing across AWS services, LangSmith for AI pipeline step-level visibility and evaluation.

**Options Considered:**

| Option | Covers |
|---|---|
| CloudWatch only | Infrastructure metrics, logs — no AI-layer visibility |
| X-Ray only | Service call graph and latency — no AI step detail |
| LangSmith only | AI steps in detail — no infrastructure metrics |
| Datadog / Grafana | Unified platform, but external cost and setup |
| CloudWatch + LangSmith | Missing distributed service map |
| All three (chosen) | Full coverage across both layers |

**Rationale:**  
These three tools answer distinct questions that no single tool answers:
- **CloudWatch:** "Is the system up? Are errors spiking? Is the DLQ growing?" — infrastructure health
- **X-Ray:** "Where is the 11-second wall clock time going? How much is cold start?" — service-boundary latency
- **LangSmith:** "What did HyDE generate? How many tokens did each step consume? Was the answer faithful?" — AI application behavior

CloudWatch alone cannot tell you why a slow query was slow (was it Bedrock? OpenSearch? cold start?). LangSmith alone cannot tell you if Lambda is throttling or if the DLQ is backing up. The combination is not redundant — each layer answers questions the others can't.

**Consequences:**  
- ✅ Full observability from infrastructure through AI quality
- ✅ Diagnosable failure modes at every layer
- ✅ LangSmith 4-metric eval provides quantitative quality measurement
- ❌ Three separate tools to check during incident response
- ❌ LangSmith has per-trace cost at high volume

**Tradeoffs:**  
Comprehensive coverage vs operational simplicity. For a POC demonstrating production thinking, the three-layer approach is both technically correct and a strong demo narrative.

---

# SECTION 2 — INGESTION PIPELINE

---

## ADR-007: LangGraph for Ingestion Pipeline Orchestration

**Status:** Accepted

**Context:**  
The ingestion pipeline is not a simple linear sequence. It has three conditional branches: duplicate detection (skip if already indexed), file type detection (reject if not PDF), and error handling (route to DLQ on failure). These branches cannot be expressed cleanly as a linear chain.

**Decision:**  
LangGraph `StateGraph` with 7 nodes and 3 conditional edges.

**Options Considered:**

| Option | Description |
|---|---|
| Plain Python (if/else) | Sequential code with conditional blocks |
| LangChain LCEL | Linear pipe operator — no native branching |
| LangGraph StateGraph | Directed graph with conditional edges |
| AWS Step Functions | Managed workflow orchestration |

**Rationale:**  
Plain Python if/else is readable for simple cases but becomes difficult to trace and test as branches multiply. It also produces no automatic observability — no trace per node, no state visibility.

LCEL is explicitly a linear pipe — it cannot express branching. Forcing conditional logic into LCEL requires awkward workarounds that fight the abstraction.

LangGraph models the pipeline as what it actually is: a directed graph with decision nodes. Each node is a function; conditional edges encode business logic (duplicate? → skip to END). This maps directly to the domain. LangSmith traces each node individually with timing, input, and output.

AWS Step Functions would provide managed workflow with visual graph, retry policies, and state persistence. But it adds API call overhead per state transition, significant IAM complexity, and removes the Python-native development experience. For a Lambda-centric POC, in-process LangGraph is the right level of abstraction.

**Consequences:**  
- ✅ Branching logic is explicit and readable in graph form
- ✅ LangSmith traces each node individually (7 nodes visible in trace)
- ✅ Easy to add/remove nodes or change routing
- ❌ LangGraph adds ~50MB to Lambda package size
- ❌ Cold start cost increases with LangGraph module initialization

**Tradeoffs:**  
Observability and clean branching vs package size and cold start.

---

## ADR-008: pypdf for PDF Parsing (vs pdfplumber, PyMuPDF)

**Status:** Accepted

**Context:**  
Legal PDFs must be parsed to extract structured text blocks before chunking. Three Python libraries were evaluated.

**Decision:**  
`pypdf` for text extraction.

**Options Considered:**

| Library | Notes |
|---|---|
| `pypdf` | Pure Python, lightweight, no C extensions |
| `pdfplumber` | Better table extraction, but conflict with `cryptography` package |
| `PyMuPDF` (fitz) | Most accurate rendering, but C extensions exceed Lambda size limit |
| `PDFMiner` | Pure Python, but verbose API and slower |

**Rationale:**  
`pdfplumber` was the first choice — it handles tables and multi-column layouts well. But it has a dependency conflict with the `cryptography` package version required by other pipeline dependencies. Resolving it required downgrading `cryptography` in a way that broke OpenSSL-dependent libraries.

`PyMuPDF` produces the highest-quality text extraction but its compiled C extensions add ~80MB to the Lambda package, pushing it over the 250MB unzipped limit when combined with LangChain.

`pypdf` is pure Python, lightweight, and sufficient for the legal document text extraction needed here. The legal PDFs in this pipeline (court decisions) are text-PDFs (not scanned images), so OCR capability isn't required.

**Consequences:**  
- ✅ No dependency conflicts
- ✅ Package stays within Lambda size limits
- ❌ No OCR support (scanned PDFs would fail)
- ❌ Limited table extraction capability

**Tradeoffs:**  
Compatibility and size vs extraction quality. For text-native legal PDFs, `pypdf` is sufficient. If scanned documents are ever needed, the fix is adding a pre-processing step (Textract) rather than replacing pypdf.

---

## ADR-009: Hybrid Chunking Strategy (Structural + Semantic Merge + Recursive Split)

**Status:** Accepted

**Context:**  
Chunking is the most consequential decision in the ingestion pipeline. Chunk quality directly determines retrieval quality. The naive approach (split every N tokens) produces chunks that cut across sentences and paragraphs, destroying legal document structure.

**Decision:**  
Three-phase hybrid chunking:
1. **Structural splitting** — `parsers.py` extracts paragraph-level blocks using document structure (headings, whitespace, formatting)
2. **Semantic merging** — adjacent blocks with cosine similarity ≥ 0.75 and same section are merged
3. **Recursive splitting** — merged blocks exceeding 448 tokens are split with separator priority: `\n` → sentence → `,` → word → character

**Options Considered:**

| Option | Description |
|---|---|
| Fixed token splitting | Split every N tokens regardless of structure |
| Sentence-boundary splitting | Split at sentence endings |
| Paragraph splitting only | Use paragraph breaks as chunk boundaries |
| Pure semantic chunking | Embed all sentences, cluster by similarity |
| Hybrid (chosen) | Structural → semantic merge → recursive split |

**Rationale:**  
Fixed token splitting destroys sentence and paragraph boundaries — a legal holding can be split mid-sentence, with the key term in one chunk and the qualifying clause in another. This is unacceptable for legal Q&A.

Pure semantic chunking (clustering sentences by embedding similarity) was the most technically appealing option. But it fails for legal documents for a counterintuitive reason: legal facts and holdings are semantically similar — that's the point of a legal argument. A holding about "flag desecration" and the facts establishing "flag desecration" would cluster together even though they should be in separate retrievable chunks (one is the legal standard, one is the application).

The hybrid approach preserves structural integrity (paragraphs are not broken mid-thought) while using semantic merging only for genuinely coherent adjacent blocks (same section + cosine ≥ 0.75). The recursive splitter as the final backstop ensures no chunk exceeds the 448-token hard limit regardless of how the structural phase performs.

**Consequences:**  
- ✅ Legal document structure preserved
- ✅ Chunk boundaries respect sentence and paragraph integrity
- ✅ Semantically coherent content grouped without over-merging
- ❌ Three-phase complexity vs simple token splitter
- ❌ Cosine threshold (0.75) requires ablation to fully defend

**Tradeoffs:**  
Document integrity vs simplicity. The complexity is justified: context_precision at 0.44 is partially a chunking problem. The experiments remaining (C1, C2) target this directly.

**Interview note:**  
> "Pure semantic chunking sounds correct but fails for legal documents. Holdings and facts are semantically similar by design — the judge is applying the law to the facts. Merging them into one chunk means you can't retrieve the holding independently from the facts that support it. Structure matters more than semantics for this domain."

---

## ADR-010: HyQ — Hypothetical Questions at Ingestion Time

**Status:** Accepted

**Context:**  
At query time, the user's question needs to match stored document chunks. A direct question-to-chunk embedding match is weak because questions are short and sparse while chunks are long and dense — they occupy different regions of vector space.

**Decision:**  
For each chunk, generate 3 hypothetical questions (using Claude Haiku 4.5) that the chunk could answer. Store these in the `hyq_questions` field in OpenSearch. At query time, BM25 matches the user's question against these stored questions with 2× weight boost.

**Options Considered:**

| Option | Description |
|---|---|
| No augmentation | Raw question → raw chunk BM25/KNN |
| HyQ BM25 (chosen) | Question-to-question BM25 matching |
| HyQ semantic KNN | Embed HyQ questions, search by KNN |
| Document summary | Generate chunk summary, embed that |
| Parent-child chunking | Small chunks for retrieval, large chunks for context |

**Rationale:**  
Question-to-question matching is a fundamentally stronger retrieval signal than question-to-paragraph matching. Questions share vocabulary, structure, and intent. A stored question "What statute was Johnson charged under?" is a near-exact match to a user query "What law was Johnson prosecuted under?" — BM25 captures this.

HyQ semantic KNN (embedding the questions and searching by vector) would be even better but adds index complexity (a second KNN field) and re-ingestion cost. The current BM25-on-questions approach is the pragmatic first step; semantic HyQ is the documented production upgrade.

Document summaries were considered but summaries lose the specific question framing — a summary of a legal paragraph reads differently than a question about it.

**Consequences:**  
- ✅ Stronger retrieval signal at query time (question-to-question match)
- ✅ Zero query-time cost (questions generated once at ingestion)
- ✅ BM25 boost (2×) distinguishes HyQ matches from body text matches
- ❌ Re-ingestion required if HyQ generation quality improves (model upgrade, prompt change)
- ❌ BM25 matching is lexical — misses synonyms and paraphrases

**Tradeoffs:**  
Strong retrieval signal at zero query cost vs ingestion complexity. Acknowledged limitation: BM25 is lexical. Production fix: embed HyQ questions and add a second KNN field.

---

## ADR-011: SHA-256 for Content-Based Deduplication

**Status:** Accepted

**Context:**  
Users upload the same document multiple times — renamed, re-exported, or accidentally re-uploaded. Without deduplication, the same content would be indexed twice, doubling chunks and polluting retrieval with duplicate results.

**Decision:**  
SHA-256 hash of the raw PDF bytes, stored in DynamoDB and indexed via GSI for O(1) lookup.

**Options Considered:**

| Option | Description |
|---|---|
| Filename-based | Reject if same filename already exists |
| SHA-256 content hash (chosen) | Hash the bytes — immune to rename |
| Fuzzy document similarity | Embed document summary, check cosine distance |
| Page count + file size | Heuristic — not collision-resistant |

**Rationale:**  
Filename-based deduplication is trivially defeated: `document.pdf` and `document_v2.pdf` are treated as different files even if identical. This is the most common failure mode in practice.

SHA-256 hashes the content, not the name. If bytes are identical, hashes are identical — the document is a duplicate regardless of filename, export date, or metadata. SHA-256 is computationally cheap (milliseconds for a 10MB PDF) and collision probability is cryptographically negligible.

Fuzzy similarity would catch near-duplicate documents (OCR re-scan, minor edit) but adds significant complexity — you'd need to run an embedding pipeline just to check for duplicates before running the embedding pipeline. It also produces probabilistic results vs deterministic results, which is worse UX.

**Consequences:**  
- ✅ Exact duplicate detection regardless of filename
- ✅ O(1) lookup via DynamoDB GSI
- ✅ Deterministic — no false positives
- ❌ Does not catch near-duplicates (minor edits, re-scans)
- ❌ Hash computed before Lambda can short-circuit — must download the full file first

**Tradeoffs:**  
Exactness and simplicity vs near-duplicate coverage. For the legal document domain (exact court filings), exact match is sufficient.

---

# SECTION 3 — QUERY PIPELINE

---

## ADR-012: LangChain LCEL for Query Pipeline Orchestration

**Status:** Accepted

**Context:**  
The query pipeline is a fixed 5-step sequence: HyDE → search → rerank → dedup → answer. No branching, no cycles, no conditional routing.

**Decision:**  
LangChain LCEL (`|` pipe operator) with `RunnableLambda` wrappers.

**Options Considered:**

| Option | Description |
|---|---|
| Plain Python function calls | Sequential calls in `lambda_handler` |
| LangGraph StateGraph | Graph-based, same as ingestion |
| LangChain LCEL (chosen) | Linear pipe — each step output → next step input |
| AWS Step Functions | Managed workflow |

**Rationale:**  
For a linear pipeline with no branching, LCEL's pipe syntax is the most concise and readable expression of the intent. `run_hyde | run_search | run_rerank | run_dedup | run_answer` reads as the pipeline is. LangGraph adds graph overhead (node registration, state management, conditional edge definitions) that is unnecessary when there are no branches.

Plain Python works but loses automatic LangSmith tracing per step — each `RunnableLambda` in LCEL is automatically traced with timing, input/output, and token counts.

AWS Step Functions adds per-state-transition latency and external API calls — the opposite of what a latency-sensitive query endpoint needs.

**Consequences:**  
- ✅ Concise, readable pipeline definition
- ✅ Automatic per-step LangSmith tracing
- ✅ Each step independently testable via `.invoke()`
- ❌ LCEL adds LangChain import overhead to Lambda cold start
- ❌ Linear only — adding a branch requires refactoring to LangGraph

---

## ADR-013: HyDE — Hypothetical Document Embeddings at Query Time

**Status:** Accepted

**Context:**  
User questions ("What was Johnson convicted of?") are short, sparse, and interrogative. Document chunks are long, dense, and declarative. In a 1536-dimensional embedding space, these two text types occupy systematically different regions. Searching with a raw question vector often retrieves chunks that are about the same topic but not the actual answer.

**Decision:**  
Before KNN search, use the LLM (Haiku 4.5) to generate a hypothetical 2–3 sentence answer to the question. Embed the hypothetical answer, not the question. Use this richer vector for KNN search.

**Options Considered:**

| Option | Description |
|---|---|
| Embed raw question | Simple; question and answer vectors misaligned |
| HyDE (chosen) | Generate hypothetical answer, embed that |
| Query expansion | Expand question with synonyms, re-embed |
| Multi-query retrieval | Generate N query variants, take union of results |

**Rationale:**  
The core insight: an embedding of a legal answer ("Johnson was convicted under Texas Penal Code §42.09(a)(3) for flag desecration...") is geometrically closer to real stored chunk embeddings than an embedding of the question ("What was Johnson convicted of?"). Both describe the same content, but the answer-shaped text shares vocabulary and density with the stored document chunks.

HyDE intentionally halluccinates — the hypothetical answer doesn't need to be factually correct. It only needs to be *answer-shaped*. Even a wrong hypothetical answer produces an embedding in the answer-region of vector space, which improves KNN recall significantly over the sparse question embedding.

The hypothetical answer is discarded after retrieval — it never appears in the final response.

Query expansion adds synonyms but doesn't change the text type (question → question). HyDE changes the text type (question → answer). Multi-query retrieval multiplies search latency by N.

**Consequences:**  
- ✅ KNN recall improved by searching with answer-shaped embedding
- ✅ Hypothetical answer never reaches the user
- ✅ ~0.98s LLM call cost — paid once per query
- ❌ Hallucinates by design — slightly uncomfortable to explain
- ❌ Adds one LLM call per query (latency + cost)
- ❌ `context_precision` can be affected if hypothetical answer steers toward wrong semantic neighborhood

**Tradeoffs:**  
Better vector alignment at the cost of an extra LLM call and deliberate hallucination. The hallucination is controlled and contained — it exists solely to steer the embedding, not to generate the answer.

---

## ADR-014: Hybrid BM25 + KNN Search with 0.5/0.5 Weights

**Status:** Accepted (weights validated by experiment)

**Context:**  
Legal documents contain both exact technical vocabulary (statute codes, case names, legal terms of art) and conceptual content. No single search modality captures both.

**Decision:**  
OpenSearch hybrid search pipeline combining BM25 (lexical) and KNN (semantic) with equal 0.5/0.5 weight split, min-max normalization. BM25 operates on raw question; KNN operates on HyDE vector. `hyq_questions` field boosted 2× in BM25.

**Options Considered:**

| Option | Notes |
|---|---|
| KNN only | Misses exact legal terms ("§42.09(a)(3)") |
| BM25 only | Misses semantic meaning; no embedding benefit |
| Hybrid 0.7/0.3 (semantic heavy) | Tested — no improvement on legal text |
| Hybrid 0.3/0.7 (BM25 heavy) | Untested |
| Hybrid 0.5/0.5 (chosen) | Validated by experiment |

**Rationale:**  
Pure KNN on legal documents misses statute codes, case citations, and exact legal terms of art — these are rare vocabulary items that Titan/Cohere embeddings may not distinguish well. BM25's TF-IDF scoring naturally rewards rare terms, making it ideal for legal search.

Pure BM25 misses semantic matches — "dismissed" vs "denied," "counsel" vs "attorney" — where meaning is the same but vocabulary differs.

The 0.7/0.3 semantic-heavy configuration was explicitly tested and reverted: legal text needs more BM25 weight because exact terminology is high-value in this domain. 0.5/0.5 was validated empirically as the best performing configuration.

Min-max normalization ensures BM25 and KNN scores, which operate on different scales, are comparable before combination.

**Consequences:**  
- ✅ Both exact term and semantic meaning retrieval
- ✅ Weights empirically validated
- ❌ Two scoring systems to debug when retrieval quality degrades
- ❌ Weight tuning is dataset-specific — may not generalize

**Tradeoffs:**  
Retrieval quality vs simplicity. Equal weights validated empirically — not a default choice.

---

## ADR-015: Cohere Rerank 3.5 as Cross-Encoder Second-Pass

**Status:** Accepted

**Context:**  
OpenSearch hybrid search returns top 15 candidates (top_k=15) scored by BM25+KNN. These scores are produced by a bi-encoder: question and chunk are encoded separately, then compared. This is fast but imprecise — the model never sees the question and chunk together.

**Decision:**  
Pass all 15 candidates to Cohere Rerank 3.5 (cross-encoder) on Bedrock. Return top 5. Replace all downstream processing with Cohere's scores.

**Options Considered:**

| Option | Description |
|---|---|
| No reranking | Trust OpenSearch hybrid scores, take top 5 |
| Cohere Rerank (chosen) | Cross-encoder sees Q+chunk together |
| BM25-only reranking | Second BM25 pass with expanded query |
| Local cross-encoder | Deploy cross-encoder model in Lambda |

**Rationale:**  
A bi-encoder (KNN) encodes question and chunk independently. The representations never interact — attention is limited within each text. A cross-encoder concatenates question and chunk and passes them through the model together. This enables cross-text attention: every word in the question can directly attend to every word in the chunk.

Example: the question contains "prosecution" and the chunk contains "State v. Johnson" — a cross-encoder connects these through attention; a bi-encoder cannot.

Local cross-encoder deployment would be slower (model inference in Lambda), heavier (model weights in package), and harder to maintain. Cohere on Bedrock is an API call — ~$0.001 per query, 150ms latency, no model management.

Reranking with top_n=7 was tested and hurt context_precision (0.44→0.34). More context into the answer generation is not always better — 5 high-quality chunks beat 7 mixed-quality chunks.

**Consequences:**  
- ✅ Significantly more accurate relevance scoring
- ✅ Reduces 15 candidates to 5 high-quality chunks
- ✅ ~$0.001 per query cost
- ❌ Additional latency (~0.38s)
- ❌ top_n=5 caps recall — some relevant chunk from position 6–15 is always discarded

**Tradeoffs:**  
Precision vs recall. top_n=5 maximizes precision; higher top_n hurts context_precision. Empirically, 5 was the right number.

---

## ADR-016: Deduplication by Page Number

**Status:** Accepted

**Context:**  
After reranking, multiple top-5 chunks may originate from the same page of the source document. Adjacent paragraphs on the same page often cover related content and may both pass reranking. If two chunks from page 3 both enter the context, the LLM sees the same source twice — wasting token budget and creating repetitive context.

**Decision:**  
After reranking, iterate through chunks in order and keep only the first chunk per unique page number.

**Options Considered:**

| Option | Description |
|---|---|
| No deduplication | Allow multiple chunks from same page |
| Dedup by page (chosen) | One chunk per page number |
| Dedup by chunk_id | Strict unique chunk deduplication |
| Dedup by semantic similarity | Embed chunks, remove near-duplicates |

**Rationale:**  
Dedup by page is a simple, reliable proxy for source uniqueness. Two chunks from the same page are very likely to contain overlapping or redundant information — they come from adjacent paragraphs in the same section. Removing the duplicate preserves the budget for a chunk from a different part of the document.

Dedup by semantic similarity would be more accurate but requires an extra embedding call per chunk — adding latency and cost with marginal benefit at this scale.

**Consequences:**  
- ✅ Prevents any single source page from dominating context
- ✅ Encourages cross-page (cross-section) context diversity
- ✅ Zero additional latency (pure Python, no I/O)
- ❌ May discard a genuinely distinct chunk that happened to be on the same page as a higher-ranked chunk

---

## ADR-017: Token Budget for Context Assembly (3000 tokens)

**Status:** Accepted (upgraded from 2000 — validated by experiment)

**Context:**  
After deduplication, remaining chunks are assembled into the context string passed to the LLM. Without a cap, large documents could produce context strings of 10,000+ tokens, increasing latency and cost nonlinearly.

**Decision:**  
Greedy character-based fill: concatenate chunks in order, stop when cumulative character count exceeds `CONTEXT_TOKEN_BUDGET × CHARS_PER_TOKEN` (3000 × 4 = 12,000 chars). Budget was 2000 initially; increased to 3000 after empirical improvement in answer correctness.

**Options Considered:**

| Option | Description |
|---|---|
| No cap | Pass all chunks — variable cost/latency |
| Fixed 2000 tokens | Original baseline |
| Fixed 3000 tokens (chosen) | Validated improvement in correctness |
| Dynamic budget | Adjust per-query based on chunk count |
| Tiktoken exact counting | Precise token count vs character approximation |

**Rationale:**  
The character approximation (4 chars ≈ 1 token) is intentionally conservative — it slightly undercounts tokens, ensuring the actual token count stays under the model's window. Tiktoken exact counting adds a tokenizer dependency and latency for marginal accuracy gain.

The 2000→3000 increase was validated: more context gave the LLM more material to work with, improving answer correctness. The risk of prompt explosion (context dominating the response window) did not materialize at 3000 tokens.

**Consequences:**  
- ✅ Bounded and predictable LLM cost per query
- ✅ 3000 tokens validated to improve correctness over 2000
- ❌ Character approximation may slightly undercount — last chunk may be truncated mid-sentence
- ❌ `sources` field in API response includes all post-rerank chunks, not just those within budget — a known inconsistency

**Tradeoffs:**  
Cost predictability vs context completeness. The budget is a deliberate constraint, not a limitation — it forces the retrieval stack to surface the most relevant content rather than relying on LLM context length as a crutch.

---

## ADR-018: Embedding Model — Cohere Embed v4 (upgraded from Titan)

**Status:** Accepted (Supersedes Titan Embed Text v1)

**Context:**  
The embedding model determines the quality of both ingestion-time chunk vectors and query-time HyDE vectors. Titan Embed Text v1 was the initial choice; Cohere Embed v4 was evaluated as part of the model upgrade experiment.

**Decision:**  
Cohere Embed v4 (1536-dim) via Bedrock.

**Options Considered:**

| Model | Notes |
|---|---|
| Amazon Titan Embed Text v1 (1536-dim) | Original choice — baseline scores |
| Cohere Embed v4 (1536-dim, chosen) | Part of upgrade suite that drove correctness 0.50→0.94 |
| Amazon Titan Embed Text v2 | Newer Titan — not tested |
| OpenAI text-embedding-3-small | Not on Bedrock — requires external API |

**Rationale:**  
The model upgrade experiment (Titan+Nova → Cohere Embed v4 + Haiku 4.5) produced the largest single improvement in the project: answer correctness 0.50→0.94. While the improvement cannot be attributed solely to the embedding model change (the LLM also changed), Cohere Embed v4 is well-regarded for retrieval tasks (BEIR benchmark performance) and the combination produced dramatically better results.

Re-ingestion was required to switch embedding models — all stored vectors must be regenerated with the new model for KNN search to be coherent.

**Consequences:**  
- ✅ Massive correctness improvement as part of upgrade suite
- ✅ Same 1536-dim output — no index schema change required
- ❌ Re-ingestion required to switch models
- ❌ Combined improvement makes it hard to isolate embedding model's individual contribution

---

## ADR-019: LLM — Claude Haiku 4.5 (upgraded from Nova Micro)

**Status:** Accepted (Supersedes Nova Micro)

**Context:**  
The LLM is used for three distinct tasks: HyDE (generate hypothetical answer), HyQ (generate 3 questions per chunk at ingestion), and answer generation. The initial model was Nova Micro — fast and cheap. Claude Haiku 4.5 was evaluated as a quality upgrade.

**Decision:**  
Claude Haiku 4.5 (`claude-haiku-4-5`) for all three LLM tasks.

**Options Considered:**

| Model | Notes |
|---|---|
| Amazon Nova Micro | Baseline — fast, cheap, lower quality |
| Claude Haiku 4.5 (chosen) | Better instruction following; improved HyQ and HyDE quality |
| Claude Sonnet | Higher quality; higher cost |
| Amazon Nova Pro | Mid-tier Nova — not tested |

**Rationale:**  
The quality of HyDE-generated hypothetical answers directly affects KNN retrieval quality. The quality of HyQ-generated questions directly affects BM25 retrieval quality. Nova Micro produced HyQ questions that were sometimes too generic or off-target, degrading both retrieval paths. Haiku 4.5 produces more specific, better-phrased questions that match the vocabulary of real user queries more closely.

The combined model upgrade (Cohere Embed v4 + Haiku 4.5) drove correctness from 0.50 to 0.94 — empirical validation that LLM quality compounds through the pipeline.

**Consequences:**  
- ✅ Better HyDE and HyQ quality → better retrieval
- ✅ Better answer generation quality
- ❌ Higher cost than Nova Micro (estimated ~2–3× per query)
- ❌ HyQ re-ingestion required when switching models

---

## ADR-020: Input Validation at Lambda Boundary

**Status:** Accepted

**Context:**  
The query Lambda invokes Bedrock (HyDE), OpenSearch (search), Bedrock again (rerank, answer) — all expensive or rate-limited operations. Malformed inputs (empty strings, non-string types, extremely long inputs) should be rejected before any of these calls are made.

**Decision:**  
Validate at the entry point of `lambda_handler`: type check, strip whitespace, enforce 3–1000 character length bounds. Return 400 immediately on failure.

**Options Considered:**

| Option | Description |
|---|---|
| No validation | Let downstream services error and return 500 |
| Validate in run_hyde | Validate at first LLM call |
| Validate at Lambda entry (chosen) | Reject before any expensive operation |
| API Gateway request validation | JSON schema validation at API Gateway |

**Rationale:**  
Fail fast, fail cheap. Rejecting at the Lambda boundary means no Bedrock calls, no OpenSearch calls, no LangSmith trace — zero cost for invalid inputs. API Gateway request validation can enforce basic JSON schema but cannot check string length or type within a JSON value without a custom authorizer.

**Consequences:**  
- ✅ Invalid inputs cost nothing beyond Lambda invocation
- ✅ Explicit 400 error vs ambiguous 500 from downstream failure
- ❌ Validation logic is duplicated if multiple routes are added

---

## ADR-021: lru_cache on Bedrock and OpenSearch Clients

**Status:** Accepted

**Context:**  
Lambda containers are reused across warm invocations. Each Bedrock client initialization involves establishing HTTPS connections to AWS endpoints, loading boto3 session credentials, and setting up the signing context. If done per-request, this overhead repeats on every invocation.

**Decision:**  
`@lru_cache(maxsize=1)` on all client factory functions (`get_llm()`, `get_embeddings()`, `get_opensearch_client()`).

**Options Considered:**

| Option | Description |
|---|---|
| Module-level globals | Initialize clients at module load (always cached) |
| lru_cache (chosen) | Cache on first call, reuse on subsequent |
| Per-invocation init | Initialize in lambda_handler on every call |
| Connection pooling | Explicit pool management |

**Rationale:**  
Module-level globals would initialize clients even on Lambda cold start before the handler runs — paying the cost regardless of whether the invocation needs that client. `lru_cache` defers initialization to the first actual call, then caches the result for the lifetime of the container.

The performance impact is visible in LangSmith traces: `run_hyde` (first Bedrock call) takes 1.42s; `run_answer` (second Bedrock call, warm connection) takes 0.63s. The 0.79s difference is HTTPS connection setup — eliminated on warm containers.

**Consequences:**  
- ✅ Connection reuse across warm Lambda invocations
- ✅ Measurable latency improvement on warm path
- ❌ Cached clients hold credentials — acceptable since Lambda role credentials auto-rotate

---

## ADR-022: API Gateway Auth — Cognito JWT + API Key

**Status:** Accepted (with known POC limitations)

**Context:**  
The query API needs two forms of protection: user authentication (who is this person?) and rate limiting / usage control (how much can they call?). These are distinct concerns.

**Decision:**  
Dual authentication: Cognito JWT via `Authorization` header (user identity) + API key via `x-api-key` header (usage plan enforcement). GET /health is public with no key required.

**Options Considered:**

| Option | Description |
|---|---|
| No auth | Open API — unacceptable |
| API key only | No user identity, no token expiry |
| Cognito only | Identity, but no fine-grained usage control |
| Cognito + API key (chosen) | Both identity and usage plan |
| Lambda authorizer | Custom auth logic, maximum flexibility |

**Rationale:**  
API Gateway's built-in Cognito authorizer validates the JWT signature and expiry against the User Pool — no custom code needed. The API key provides a separate usage plan layer (10 req/sec, 1000/month) with automatic throttling and quota enforcement. These two mechanisms serve different purposes and are both needed.

**Known POC limitation:** Cognito implicit grant flow is deprecated in OAuth 2.1 — the JWT is exposed in the browser URL and browser history. Production fix: authorization code flow with PKCE.

**Known POC limitation:** The API key is baked into `config.ts` and shipped with the React build. This is mitigated by requiring a valid Cognito JWT — the API key alone is useless without authentication. In production, the API key would be fetched from a backend secrets service.

**Consequences:**  
- ✅ User identity via Cognito — tokens expire, can be revoked
- ✅ Usage plan enforcement with automatic throttling
- ✅ No custom authorizer code to maintain
- ❌ Implicit grant deprecated — JWT in URL
- ❌ API key in frontend bundle

---

# SECTION 4 — EVALUATION

---

## ADR-023: LLM-as-a-Judge with Four Metrics

**Status:** Accepted

**Context:**  
Evaluating RAG pipeline quality requires measuring at two distinct layers: retrieval quality (did we get the right chunks?) and generation quality (did the LLM produce a correct, grounded answer?). Human evaluation doesn't scale; single-metric evaluation misses layer-specific failures.

**Decision:**  
Four evaluators using Claude Haiku 4.5 as judge, run against 50 question/expected-answer pairs:
- `faithfulness` — retrieval→generation boundary (hallucination detection)
- `answer_relevance` — generation layer (did answer address the question?)
- `answer_correctness` — end-to-end (was the answer factually right vs expected?)
- `context_precision` — retrieval layer (were retrieved chunks useful?)

**Options Considered:**

| Option | Description |
|---|---|
| Human evaluation | Ground truth, but does not scale |
| Single metric (correctness) | Misses retrieval vs generation distinction |
| RAGAS framework | Pre-built RAG evaluation library |
| Four custom evaluators (chosen) | Full control, interpretable, uses existing Bedrock client |

**Rationale:**  
The four-metric combination is the minimum sufficient set to diagnose failure at each pipeline layer. Faithfulness 1.00 + correctness 0.50 is a diagnostic pattern: the LLM isn't hallucinating (faithfulness), but the wrong chunks are being retrieved (correctness low). Without faithfulness separately measured, a correctness drop looks like a generation problem — you'd try to fix the LLM when the retrieval is broken.

RAGAS was considered but adds a dependency with its own LLM calls and opinionated prompt templates. Custom evaluators using the same Bedrock client provide transparency — you can read exactly what the judge is asked and why it scores a particular answer.

**Consequences:**  
- ✅ Layer-specific diagnostic capability
- ✅ Quantitative, reproducible scores
- ✅ LangSmith records all scores for trend analysis
- ❌ LLM-as-a-Judge introduces same-model bias (Haiku judging Haiku answers)
- ❌ Ground truth (50 Q&A pairs) must be manually curated

**Tradeoffs:**  
Interpretability and control vs framework convenience. Custom evaluators cost more build time but produce metrics you can fully explain in an interview.

---

*End of ADR Document — 23 records across Infrastructure, Ingestion, Query, and Evaluation phases.*
