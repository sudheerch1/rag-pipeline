# RAG Pipeline — Query Pipeline Deep Dive Thread Summary
## For continuing in a new thread (Updated: May 28, 2026)

---

## What This Thread Covered

This thread was a deep conceptual walkthrough of `query_graph.py` — the query Lambda. Every step of the pipeline was traced with real examples, from LCEL syntax to Cross-Encoder theory.

---

## 1. LCEL — LangChain Expression Language

The `|` pipe operator chains components together. Output of each step becomes input of the next.

```python
hyde_chain = hyde_prompt | get_llm() | StrOutputParser()
```

- `hyde_prompt` — fills in `{question}` placeholder → produces formatted string
- `get_llm()` — sends prompt to Bedrock/Nova → returns `AIMessage` object
- `StrOutputParser()` — strips `AIMessage` wrapper → returns plain Python string

**Key concept:** `invoke({"question": "..."})` triggers the full chain in sequence.

---

## 2. RunnableLambda

Wraps a plain Python function so LCEL's `|` pipe can work with it.

```python
query_pipeline = (
    RunnableLambda(run_hyde)
    | RunnableLambda(run_search)
    | RunnableLambda(run_rerank)
    | RunnableLambda(run_dedup)
    | RunnableLambda(run_answer)
)
```

- Plain Python functions don't support `|` — `RunnableLambda` is the adapter that makes them LCEL-compatible
- Think of it as a **power adapter**: your function = US plug, LCEL pipeline = European socket
- Wrapping gives each function `.invoke()`, `.batch()`, `.stream()` methods

---

## 3. State Dict — How Data Flows Through the Pipeline

The same `state` dict travels through every step, getting new keys added at each stage:

```
{"question": "..."}
        ↓  run_hyde()     → adds state["hyde_text"]
        ↓  run_search()   → adds state["chunks"]  (10 candidates)
        ↓  run_rerank()   → updates state["chunks"] (reranked, top 5)
        ↓  run_dedup()    → updates state["chunks"] (duplicates removed)
        ↓  run_answer()   → adds state["answer"]
        ↓
final state returned to lambda_handler
```

---

## 4. run_hyde() — HyDE Step

**What it does:** Generates a legal-sounding hypothetical answer from the user's question.

```python
hyde_chain.invoke({"question": state["question"]})
```

- `hyde_prompt` fills in `{question}` in the template
- LLM generates a passage *as if extracted from a legal document*
- `StrOutputParser` strips the `AIMessage` and returns plain string
- Result stored as `state["hyde_text"]`
- **Fallback:** If LLM fails → `state["hyde_text"] = state["question"]` (pipeline doesn't crash)

**Why HyDE:** Answer embeddings live in the same vector space as stored document chunks. A raw question vector is too far from document space for accurate KNN retrieval.

---

## 5. run_search() — Hybrid Search Step

**What it does:** Runs BM25 keyword search + KNN vector search simultaneously in OpenSearch.

### Two legs of hybrid search:

| Leg | Uses | Searches field |
|---|---|---|
| BM25 keyword | Original question (raw text) | `text` (weight 1.0) and `hyq_questions` (weight 2.0) |
| KNN vector | HyDE answer (converted to 1536-dim vector) | `embedding` |

### Why this split:
- BM25 is good at **exact word matching** → use raw question
- KNN is good at **semantic/meaning matching** → use HyDE answer (closer to document space)

### OpenSearch index fields (rag-chunks):
```
text           → Chunk paragraph text (BM25 searchable)
embedding      → 1536-dim Titan vector (KNN searchable)
hyq_questions  → 3 generated questions (BM25, 2× weight)
source_key     → S3 filename
page           → Page number from PDF
section        → Detected section heading
chunk_index    → Position within document
```

### Result cleanup — list comprehension:
```python
state["chunks"] = [
    {
        'text':    h['_source']['text'],
        'source':  h['_source']['source_key'],
        'page':    h['_source'].get('page'),      # .get() = safe, returns None if missing
        'section': h['_source'].get('section',''),# .get() with default = returns '' if missing
        'score':   h['_score']
    }
    for h in hits
]
```
Strips all OpenSearch metadata noise — keeps only the 5 fields needed downstream.

---

## 6. BM25 and TF-IDF — Key Concepts Covered

### TF-IDF (the algorithm inside BM25):
- **TF (Term Frequency):** How many times a word appears in THIS chunk → more = higher score
- **IDF (Inverse Document Frequency):** How rare a word is ACROSS ALL chunks → rarer = higher score
- **TF-IDF score = TF × IDF**
- Common words like "what", "the" → near zero IDF → minimal contribution
- Rare words like "indemnification", "breaches" → high IDF → major contribution

### BM25 improvement over plain TF-IDF:
- Adds **saturation** — word appearing 10x doesn't score 10x (flattens out)
- Adds **document length normalization** — shorter chunks score slightly higher per match

### Weight in `hyq_questions^2.0`:
- A match in `hyq_questions` field counts 2× vs a match in `text`
- `hyq_questions` were purpose-built during ingestion to mirror real user queries
- So a question-to-question match is a very strong relevance signal → deserves double weight

### Algorithm family tree:
```
TF-IDF (1970s) → BM25 (1994) → Hybrid Search (now)
                                  ↑
                         BM25 + Vector Search combined
                         (what your pipeline uses)
```

---

## 7. Embeddings and KNN Search

### What an embedding is:
- Text converted to a list of 1536 floating point numbers by Amazon Titan
- Similar meaning → similar numbers (close vectors)
- Different meaning → very different numbers (far vectors)

### During ingestion:
- Each chunk's text was embedded → stored in `embedding` field in OpenSearch

### During query:
- `hyde_text` is embedded → `question_vector`
- KNN compares `question_vector` against all stored `embedding` vectors
- Returns 10 chunks with smallest distance (highest cosine similarity)

### Why HyDE vector beats raw question vector:
```
Raw question  → QUESTION space    → far from stored chunks
HyDE answer   → LEGAL DOC space  → close to stored chunks
```

### OpenSearch KNN configuration (your index):
```
Engine:     FAISS
Algorithm:  HNSW (Hierarchical Navigable Small World)
Similarity: Cosine Similarity
```

### Cosine similarity:
- Measures the **angle** between two vectors (not distance)
- Range: 0 to 1 (1 = identical meaning)
- Best for text embeddings — magnitude doesn't matter, only direction

### HNSW algorithm:
- Organizes chunks as a multi-layer graph
- Navigates graph to find nearest vectors without comparing against all chunks
- Brute force: compare against 1M chunks → slow; HNSW: navigate graph → fast

### KNN also gives scores:
- Each returned chunk gets a cosine similarity score (0 to 1)
- These flow into `state["chunks"]` as `score` field

---

## 8. Hybrid Score Merging

Both BM25 and KNN produce scores. OpenSearch's `hybrid-search-pipeline` merges them:
- Uses `min_max normalization` on both scores
- Combines into a single hybrid score per chunk
- Top 10 returned to Lambda

---

## 9. run_rerank() — Cohere Reranking Step

**What it does:** Takes 10 OpenSearch chunks, sends them to Cohere on Bedrock, gets back top 5 truly relevant ones with better scores.

### What is sent to Cohere:
```python
{
    "query": "original user question",
    "documents": ["chunk text 1", "chunk text 2", ...],  # 10 chunk texts
    "top_n": 5,
    "api_version": 2
}
```

### What Cohere returns:
```python
results = [
    {"index": 0, "relevance_score": 0.98},  # original position in chunks list
    {"index": 2, "relevance_score": 0.91},
    ...5 total
]
```

### Score tracking in state:
```python
c['rerank_score']   = item['relevance_score']  # Cohere's new score
c['original_score'] = c['score']               # old OpenSearch hybrid score saved
c['score']          = item['relevance_score']  # replaced with Cohere score
```

### Fallback if Cohere fails:
```python
state["chunks"] = chunks[:top_n]  # just take first 5 from OpenSearch order
```

---

## 10. Why Cohere Reranker is Better Than OpenSearch Scoring

### OpenSearch uses Bi-Encoder (separate encoding):
```
Question → Encoder → Vector A
                              → compare separately
Chunk    → Encoder → Vector B
```
- Question and chunk **never see each other** during encoding
- Attention is limited to within one text at a time

### Cohere uses Cross-Encoder (joint encoding):
```
[Question + Chunk together] → Encoder → Relevance Score
```
- Question and chunk are fed **together**
- Every word in the question **directly attends to** every word in the chunk
- Model deeply understands the relationship between question and answer

### Why Cross-Encoder is more accurate:
- Bi-Encoder: "breach" in question vs "breach" in chunk → high score (word match)
- Cross-Encoder: reads that chunk talks about **confidentiality breach** not **contract remedies** → correctly scores lower

### Why Cross-Encoder can't replace OpenSearch:
```
OpenSearch KNN: encode question once → compare 10,000 vectors → milliseconds ✅
Cohere:         run model on 10,000 pairs → minutes ❌

Solution: OpenSearch finds top 10 fast → Cohere deeply judges those 10
```

### Score journey:
```
KNN cosine score  ──┐
                    ├──► hybrid pipeline ──► hybrid score ──► Cohere rerank ──► final score
BM25 TF-IDF score ──┘
```

---

## 11. Concepts — Python Syntax Covered

### dict (dictionary):
```python
{"question": "What happens if a party breaches the contract?"}
#  key           value
```
Key-value pairs. `PromptTemplate` looks for key `"question"` to fill in `{question}` placeholder.

### `.get()` vs `[]` access:
```python
h['_source']['text']          # crashes if key missing (KeyError)
h['_source'].get('page')      # returns None if key missing (safe)
h['_source'].get('section','')# returns '' if key missing (safe with default)
```

### List comprehension:
```python
[{ ... } for h in hits]
# = loop through hits, build a dict for each, collect into a list
```

---

## 12. Full Query Pipeline Flow (end to end)

```
User: "What happens if a party breaches the contract?"
                    ↓
            lambda_handler()
            - input validation (length, type, blank check)
                    ↓
            run_hyde()
            - LLM generates legal-sounding hypothetical answer
            - state["hyde_text"] added
                    ↓
            run_search()
            - embed hyde_text → 1536-dim vector
            - BM25: keyword search on text + hyq_questions (raw question)
            - KNN: vector search on embedding field (hyde vector)
            - hybrid pipeline merges scores
            - top 10 chunks returned
            - state["chunks"] added (10 items)
                    ↓
            run_rerank()
            - send 10 chunks + question to Cohere on Bedrock
            - Cohere cross-encoder reads Q+chunk together
            - returns top 5 with new relevance scores
            - state["chunks"] updated (5 items, Cohere scores)
                    ↓
            run_dedup()
            - removes chunks with same source+page key
            - state["chunks"] updated
                    ↓
            run_answer()
            - token budget enforcement (2000 tokens = ~8000 chars)
            - builds context from chunks
            - LLM generates final answer
            - state["answer"] added
                    ↓
            lambda_handler returns:
            {
                "question": "...",
                "answer": "...",
                "sources": [{ source, page, section, score }, ...]
            }
```

---

## 13. Carry-Forward from Previous Summaries

- **Active branch:** `feature/langchain-langgraph`
- **Demo 2 date:** June 4, 2026 (query pipeline)
- **Live URL:** https://d2hsci73dlmw9w.cloudfront.net
- **OpenSearch:** delete when not in use (~$50-100/month)
- **LangSmith:** answer_correctness 1.00 on 8 Q&A pairs
- **Key files:** `query_graph.py`, `ingestion_graph.py`, `llm_clients.py`, `opensearch_client.py`, `config.py`
- All ingestion decisions, AWS resource details, deploy commands → see `RAG_Pipeline_Project_Summary_v6.md`
