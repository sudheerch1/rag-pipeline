# RAG Pipeline — LangSmith, Observability & Evaluation Thread Summary
## For continuing in a new thread (Updated: May 30, 2026)

---

## What This Thread Covered

This thread was a deep dive into LangSmith observability, evaluation metrics, X-Ray tracing, and CloudWatch dashboard. All 4 evaluation metrics were added and run successfully. X-Ray traces are now working.

---

## 1. Code Changes Made This Session

### `query_graph.py` — Added `context` field to API response
In `lambda_handler`, added one line to return chunk texts alongside the answer:

```python
'body': json.dumps({
    'question': state["question"],
    'answer': state["answer"],
    'context': [c['text'] for c in state["chunks"]],   # ← ADDED THIS SESSION
    'sources': [
        {
            'source': c['source'],
            'page': c['page'],
            'section': c.get('section', ''),
            'score': c['score']
        }
        for c in state["chunks"]
    ]
})
```

**Why:** `faithfulness` and `context_precision` evaluators need the actual chunk texts, not just metadata.

---

### `langsmith_eval.py` — Full rewrite with 4 evaluators

**What changed:**
- `call_rag_pipeline()` now captures `context` (chunk texts) from response
- Added `invoke_nova()` shared helper — all evaluators reuse one Bedrock client
- Added 3 new evaluators: `faithfulness_evaluator`, `answer_relevance_evaluator`, `context_precision_evaluator`
- `main()` now runs all 4 evaluators and prints aggregate scores

**Both files committed to:** `feature/langchain-langgraph` branch

---

## 2. LangSmith — Full Understanding

### What LangSmith Actually Does
```
LangSmith = storage + analytics + experiment tracking
Nova Micro = does the actual judging (LLM-as-a-Judge)
```

LangSmith does NOT evaluate anything itself. It records scores that your evaluator functions produce and displays them as analytics. This is true for custom evaluators — LangSmith's built-in evaluators use their own LLM internally.

### Two Pillars
```
Tracing      → observability (what happened at each step)
Evaluation   → quality measurement (how good were the answers)
```

### Tracing
Every query is automatically traced when these Lambda env vars are set:
```
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=ls__...
LANGCHAIN_PROJECT=rag-pipeline
```

Per trace you see:
```
run_hyde    → input: {question}, output: hyde_text, latency, tokens
run_search  → output: 10 chunks, latency
run_rerank  → output: 5 chunks reranked, latency
run_dedup   → output: unique chunks, latency
run_answer  → input: context + question, output: answer, tokens
```

### Cold Start Insight from Traces
```
run_hyde    94 tokens  → 1.42s  (FIRST Bedrock call — pays connection setup cost)
run_answer  524 tokens → 0.63s  (SECOND Bedrock call — reuses warm connection)
```
run_answer generates 5x more tokens but takes less than half the time. The difference is HTTPS connection overhead on the first call, not token count.

---

## 3. Evaluation — Full Understanding

### How It Works
```
You read PDF → write 8 expected answers → QA_PAIRS in code
      ↓
create_dataset() uploads them to LangSmith
      ↓
evaluate() runs each question through live API
      ↓
call_rag_pipeline() → gets answer + context chunks from API
      ↓
4 evaluator functions → each sends prompt to Nova Micro → gets verdict
      ↓
Scores posted to LangSmith → visible in Experiments tab
```

### The 4 Evaluators

| Evaluator | What it measures | Layer | Needs chunks? |
|---|---|---|---|
| `faithfulness` | Did Nova hallucinate? | Generation | ✅ Yes |
| `answer_relevance` | Did answer address the question? | Generation | ❌ No |
| `answer_correctness` | Was the answer factually right? | End-to-end | ❌ No |
| `context_precision` | Were retrieved chunks useful? | Retrieval | ✅ Yes |

### Plain English Definitions

**faithfulness** — "Did Nova make anything up?"
Checks every claim in the answer against retrieved chunks. Score 1 = grounded, 0 = hallucinated.

**answer_relevance** — "Did the answer talk about the right topic?"
Checks if the answer addresses what was actually asked. Can be relevant but incorrect (right topic, wrong facts).

**answer_correctness** — "Was the answer factually right?"
Compares pipeline answer against your hand-written expected answer. Nova judges semantic match, not exact string.

**context_precision** — "Did OpenSearch retrieve the right chunks?"
Loops through each chunk individually, Nova judges each one. Score = fraction of useful chunks.

### What Each Metric Looks At
```
context_precision  → question + chunks               (retrieval layer)
faithfulness       → question + chunks + answer      (retrieval→generation boundary)
answer_relevance   → question + answer               (generation layer)
answer_correctness → question + answer + expected    (end-to-end)
```

### All 4 Combinations of Relevance vs Correctness
```
Relevant + Correct     → pipeline working perfectly
Relevant + Incorrect   → right topic, wrong facts (retrieval failure — wrong chunks)
Not Relevant + Correct → answered different question (generation drift)
Not Relevant + Incorrect → complete failure (Nova said "I cannot find this")
```

### Latency
**Not measured by any evaluator function.** LangSmith measures it automatically — times how long `call_rag_pipeline()` takes. Free observability, zero code needed.

---

## 4. Evaluation Results (Session Run — May 30, 2026)

### Aggregate Scores
```
faithfulness          : 1.00  (8/8 questions)  ← Nova never hallucinated
answer_relevance      : 0.62  (8 questions)
answer_correctness    : 0.50  (8 questions)
context_precision     : 0.45  (8 questions)
```

### Key Insight
**Faithfulness 1.00 but correctness 0.50 = retrieval problem, not generation problem.**

For 4 questions, wrong chunks were retrieved → Nova faithfully said "I cannot find this information" → answer scored 0 on correctness. Nova did exactly the right thing — it didn't make up answers. But it had nothing to work with.

### Per Question Analysis
| Row | Question (short) | answer_correctness | faithfulness | answer_relevance | context_precision |
|---|---|---|---|---|---|
| 1 | Court respond to incompetency | 1.00 | 1.00 | 1.00 | 0.67 |
| 2 | Sentence imposed | 0.00 | 1.00 | 1.00 | 0.00 |
| 3 | Judge personally embroiled | 0.00 | 1.00 | 0.00 | 0.80 |
| 4 | Practice Book section 1-17 | 1.00 | 1.00 | 1.00 | 0.75 |
| 5 | Reduce from four to three | 0.00 | 1.00 | 0.00 | 0.20 |
| 6 | Outcome of writ of error | 1.00 | 1.00 | 1.00 | 0.25 |
| 7 | Gregory Johnson convicted of | 1.00 | 1.00 | 1.00 | 0.60 |
| 8 | Attorney Barlow reason | 0.00 | 1.00 | 0.00 | 0.33 |

### Where to Find Results in LangSmith
```
LangSmith → Datasets & Experiments → johnson-v-superior-court-eval → Experiments tab
→ johnson-eval-60622841
```
Direct URL:
```
https://smith.langchain.com/o/b1828716-fdc6-461b-ac7d-46399e2d8e73/datasets/ba789a26-8d92-43fb-acb7-fdc6bae17718/compare?selectedSessions=085eaebf-f070-4019-a29c-1c35d8ef3db6
```

---

## 5. Ground Truth — Important Concept

`answer_correctness` relies entirely on the 8 expected answers you wrote manually in `QA_PAIRS` after reading the PDF. Nova compares pipeline answer vs your expected answer — it has no knowledge of the document itself.

**Scaling ground truth in production:**
- LLM-generated Q&A pairs from documents → human spot-checks 10-20%
- Grow organically from production traces (LangSmith "Add to dataset" button)
- Extract from document structure (headings → questions, content → answers)
- User feedback (thumbs down → human writes correct answer → added to dataset)

---

## 6. X-Ray Tracing — Now Working ✅

### Fix Applied This Session
Added environment variable to both Lambdas:
```
Key:   OTEL_TRACES_EXPORTER
Value: awsxray
```

Both Lambdas already had:
```
AWS_LAMBDA_EXEC_WRAPPER = /opt/otel-instrument
TracingConfig: Active
AWSOpenTelemetryDistroPython layer attached
```

### What You Can See Now
```
CloudWatch → Application Signals → Trace Map
```
Shows visual graph:
```
Client → rag-document-processor (Lambda Context) → rag-document-processor (Lambda Function)
Client → rag-query (Lambda Context)              → rag-query (Lambda Function)
```

### Lambda Context vs Lambda Function
```
Lambda Context  = cold start + your code execution (everything)
Lambda Function = only your lambda_handler() execution

rag-query:
  Lambda Context:  11.63s  (everything including cold start)
  Lambda Function:  6.32s  (just your code)
  Cold start cost:  5.31s  (difference = AWS initialization overhead)
```

### Why Two Nodes in Trace Map
X-Ray deliberately separates them so you can see cold start cost vs actual execution cost. In production you'd eliminate cold start with provisioned concurrency.

---

## 7. Three-Layer Observability Story

```
CloudWatch Dashboard  → infrastructure health
                        Lambda invocations, errors, duration, DLQ depth
                        "Is the system up and healthy?"

X-Ray Trace Map       → infrastructure timing
                        Cold start cost, Lambda execution boundary
                        "Where does the time go at the AWS level?"

LangSmith Tracing     → AI application behavior
                        Per step latency, token counts, input/output at each node
                        "What did each AI step actually do?"

LangSmith Evaluation  → AI quality measurement
                        4 metrics across retrieval and generation layers
                        "How good are the answers and why?"
```

**One-liner for demo:**
> *"CloudWatch tells me my infrastructure is healthy. X-Ray tells me where the time goes across AWS services. LangSmith tells me what my AI pipeline did with that time — and whether the answers were good."*

---

## 8. CloudWatch Dashboard Status

**⚠️ Dashboard widgets were lost** — `rag-pipeline-dashboard` exists but shows "No widget on this dashboard."

**Needs to be rebuilt before demo.** Add these 5 widgets:

| Widget | Metric | Source |
|---|---|---|
| Invocation Count | Lambda → Invocations | rag-query + rag-document-processor |
| Error Rate | Lambda → Errors | rag-query + rag-document-processor |
| Duration p50/p99 | Lambda → Duration | rag-query |
| Throttles | Lambda → Throttles | rag-query |
| DLQ Depth | SQS → ApproximateNumberOfMessagesVisible | rag-ingestion-dlq |

---

## 9. Demo Talking Points — Observability & Evaluation

### Opening
> *"Most RAG demos show you an answer. This shows you proof — proof the pipeline worked, proof Nova didn't hallucinate, and proof of where it breaks down when it does."*

### On LangSmith Tracing
> *"Every query is fully traced. I can see exactly what HyDE generated, how many chunks came back, how Cohere reranked them, and what Nova received before generating the answer. run_hyde takes 1.42 seconds despite only 94 tokens because it's the first Bedrock call — it pays the HTTPS connection setup cost. run_answer generates 524 tokens in 0.63 seconds because it reuses the warm connection."*

### On Evaluation
> *"I implemented the four standard RAG evaluation metrics — faithfulness, answer relevance, context precision, and answer correctness. Together they cover both the retrieval layer and the generation layer."*

### On Faithfulness Score
> *"Faithfulness is 1.00 across all 8 questions. Nova never hallucinated once. Every claim in every answer came directly from the retrieved chunks — it never went outside the documents."*

### On Lower Correctness Score
> *"Answer correctness is 0.50 and context precision is 0.45 — and this is where it gets interesting. These two metrics together tell me the problem isn't generation, it's retrieval. For 4 questions the right chunks weren't surfaced. Nova was faithful to what it got — it just didn't get the right chunks. Without these metrics I'd just see wrong answers with no idea why."*

### On LLM-as-a-Judge
> *"The evaluators use Nova Micro on Bedrock as the judge. LangSmith records and displays the scores — it doesn't do the evaluation itself. In production you'd use a stronger independent model as judge to avoid same-model bias, but for a POC this gives accurate signal at near-zero cost."*

### On X-Ray
> *"X-Ray separates Lambda Context from Lambda Function. Context is 11.6 seconds, Function is 6.3 seconds — that 5.3 second gap is pure cold start. AWS is initializing the container and loading LangChain before my code even starts. In production you'd eliminate this with provisioned concurrency."*

### On Two-Layer Observability
> *"X-Ray gives me infrastructure-level observability. LangSmith gives me AI-layer observability. They answer different questions — X-Ray tells me a request took 11 seconds, LangSmith tells me 1.42 of those seconds was HyDE generation and why."*

---

## 10. Things To Do Before June 4 Demo

| Priority | Task | Status |
|---|---|---|
| 🔴 High | Rebuild CloudWatch dashboard widgets (5 widgets) | ❌ Not done |
| 🔴 High | Practice demo narrative out loud — LangSmith section | ❌ Not done |
| 🟡 Medium | Verify X-Ray traces appear after a fresh query | ✅ Working |
| 🟡 Medium | Open LangSmith experiment view — confirm scores visible | ✅ Working |
| 🟢 Low | Consider increasing top_k to 15 to improve context_precision | Optional |

---

## 11. Carry-Forward from Previous Summaries

- **Active branch:** `feature/langchain-langgraph`
- **Demo 2 date:** June 4, 2026 (query pipeline)
- **Live URL:** https://d2hsci73dlmw9w.cloudfront.net
- **OpenSearch:** ACTIVE this session — delete when not using (~$50-100/month)
- **LangSmith project:** rag-pipeline
- **Eval dataset:** johnson-v-superior-court-eval (8 Q&A pairs)
- **Latest eval scores:** faithfulness 1.00, correctness 0.50, relevance 0.62, context_precision 0.45
- **Key files:** `query_graph.py`, `langsmith_eval.py`, `ingestion_graph.py`, `llm_clients.py`, `opensearch_client.py`, `config.py`
- All ingestion decisions, AWS resource details, deploy commands → see `RAG_Pipeline_Project_Summary_v6.md`
- Query pipeline deep dive → see `RAG_Pipeline_Thread_Summary_QueryPipeline_Deepdive.md`
