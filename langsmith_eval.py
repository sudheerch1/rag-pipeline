"""
LangSmith Evaluator for RAG Pipeline
- Creates a dataset of 8 Q&A pairs from johnson_v._superior_court_1.pdf
- Runs answer correctness + faithfulness + answer relevance + context precision evals
- Results appear in LangSmith under project: rag-pipeline
"""

import json
import boto3
import os
import requests
from langsmith import Client
from langsmith.evaluation import evaluate
from langsmith.schemas import Run, Example
from langchain_aws import ChatBedrockConverse

# ── Config ────────────────────────────────────────────────────────────────────

API_URL      = "https://k0injz47nk.execute-api.us-east-1.amazonaws.com/prod/query"
API_KEY      = "gPEWpbTVLq9NPD0YJYD4pCszNr7UcF4ln1etxZd0"
DATASET_NAME = "johnson-v-superior-court-eval"
PROJECT_NAME = "rag-pipeline"

# ── Q&A Dataset ───────────────────────────────────────────────────────────────

QA_PAIRS = [
    {
        "question": "What was Gregory Johnson convicted of?",
        "expected": "Three counts of summary criminal contempt for repeatedly interrupting and directing racial slurs and profanities at the court during his habeas trial on May 4, 2023."
    },
    {
        "question": "What sentence did the trial court impose on Johnson?",
        "expected": "Six months of incarceration on each of the three contempt counts, to be served consecutively."
    },
    {
        "question": "Why did the trial court reduce the contempt findings from four to three?",
        "expected": "The court vacated one count because it did not personally hear what Johnson said — it only knew something offensive was said based on the reaction of the respondent's attorney."
    },
    {
        "question": "What reason did Attorney Barlow give for Johnson's behavior?",
        "expected": "She explained that Johnson had not taken his medications that day, was experiencing hallucinatory issues, and was having difficulty with transfers from his wheelchair."
    },
    {
        "question": "What is Practice Book section 1-17?",
        "expected": "A rule of practice listing grounds for deferring a summary criminal contempt proceeding, including when misconduct does not obstruct justice, when the judge has become personally embroiled, when misconduct did not occur in the court's presence, or when contempt was not instantly imposed."
    },
    {
        "question": "Did the Connecticut Supreme Court find that the trial judge became personally embroiled with Johnson?",
        "expected": "No. The court found that despite Johnson's repeated personal attacks and racial slurs, the trial judge maintained a calm and professional demeanor and did not become personally embroiled."
    },
    {
        "question": "What was the outcome of Johnson's writ of error?",
        "expected": "The writ of error was dismissed. The Supreme Court found the trial court substantially complied with Practice Book section 1-16 and was not required to defer the proceeding."
    },
    {
        "question": "How did the court respond to Johnson's incompetency claim?",
        "expected": "The trial court rejected it based on its own observations, noting Johnson was highly intelligent and had directly responded to the court. Counsel also never formally requested a competency evaluation."
    },
]

# ── Step 1: Create or reuse LangSmith dataset ─────────────────────────────────

def create_dataset(client: Client) -> str:
    datasets = list(client.list_datasets(dataset_name=DATASET_NAME))
    if datasets:
        print(f"Dataset already exists: {DATASET_NAME}")
        return datasets[0].id

    print(f"Creating dataset: {DATASET_NAME}")
    dataset = client.create_dataset(
        dataset_name=DATASET_NAME,
        description="Q&A pairs from johnson_v._superior_court_1.pdf for RAG eval"
    )

    for pair in QA_PAIRS:
        client.create_example(
            inputs={"question": pair["question"]},
            outputs={"answer": pair["expected"]},
            dataset_id=dataset.id
        )

    print(f"Added {len(QA_PAIRS)} examples to dataset")
    return dataset.id


# ── Step 2: Target function — calls live API ──────────────────────────────────

def call_rag_pipeline(inputs: dict) -> dict:
    response = requests.post(
        API_URL,
        headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
        json={"question": inputs["question"]},
        timeout=30
    )
    body = response.json()
    if isinstance(body.get("body"), str):
        result = json.loads(body["body"])
    else:
        result = body

    return {
        "answer":  result.get("answer", ""),
        "context": result.get("context", []),   # list of chunk texts — needed for faithfulness + context_precision
        "sources": result.get("sources", [])    # metadata — source/page/section/score
    }


# ── Shared Bedrock helper ─────────────────────────────────────────────────────

def invoke_nova(prompt: str) -> str:
    """Calls Nova Micro and returns the raw text response."""
    bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
    response = bedrock.invoke_model(
        modelId="amazon.nova-micro-v1:0",
        body=json.dumps({
            "messages": [{"role": "user", "content": [{"text": prompt}]}]
        })
    )
    result = json.loads(response["body"].read())
    return result["output"]["message"]["content"][0]["text"].strip()


# ── Step 3: Evaluators ────────────────────────────────────────────────────────

def answer_correctness_evaluator(run: Run, example: Example) -> dict:
    """
    Is the pipeline answer factually correct vs the expected answer?
    Nova judges whether the answer aligns with the reference.
    Score: 1 = correct, 0 = incorrect.
    """
    pipeline_answer = run.outputs.get("answer", "")
    expected_answer = example.outputs.get("answer", "")
    question        = example.inputs.get("question", "")

    prompt = f"""You are evaluating a RAG pipeline answer.

Question: {question}

Expected answer: {expected_answer}

Pipeline answer: {pipeline_answer}

Does the pipeline answer correctly address the question and align with the expected answer?
Respond with ONLY one of: CORRECT or INCORRECT
Then on a new line explain briefly why."""

    verdict = invoke_nova(prompt)
    score   = 1 if verdict.upper().startswith("CORRECT") else 0
    return {"key": "answer_correctness", "score": score, "comment": verdict}


def faithfulness_evaluator(run: Run, example: Example) -> dict:
    """
    Does the answer stay grounded in the retrieved chunks, or does Nova hallucinate?
    Checks every claim in the answer can be traced back to the context.
    Score: 1 = fully grounded, 0 = contains hallucinated claims.

    Why this matters: answer_correctness only checks if the answer is right.
    Faithfulness checks HOW the answer was produced — from retrieved evidence,
    not from the LLM's parametric memory.
    """
    pipeline_answer = run.outputs.get("answer", "")
    context_chunks  = run.outputs.get("context", [])
    question        = example.inputs.get("question", "")

    if not context_chunks:
        return {
            "key": "faithfulness",
            "score": 0,
            "comment": "No context chunks returned by pipeline — cannot assess faithfulness."
        }

    context_text = "\n\n".join(
        f"[Chunk {i+1}]: {chunk}" for i, chunk in enumerate(context_chunks)
    )

    prompt = f"""You are evaluating whether an AI answer is grounded in the provided context.

Question: {question}

Retrieved context chunks:
{context_text}

AI answer: {pipeline_answer}

Is every factual claim in the answer supported by the context chunks above?
The answer should not introduce facts not present in the context.

Respond with ONLY one of: FAITHFUL or HALLUCINATED
Then on a new line explain which specific claims are or are not supported."""

    verdict = invoke_nova(prompt)
    score   = 1 if verdict.upper().startswith("FAITHFUL") else 0
    return {"key": "faithfulness", "score": score, "comment": verdict}


def answer_relevance_evaluator(run: Run, example: Example) -> dict:
    """
    Does the answer actually address what the question asked?
    A correct but off-topic answer should score low here.
    Score: 1 = directly addresses the question, 0 = does not address it.

    Difference from answer_correctness:
    - answer_correctness: is the content factually right?
    - answer_relevance:   is the answer actually about what was asked?
    """
    pipeline_answer = run.outputs.get("answer", "")
    question        = example.inputs.get("question", "")

    prompt = f"""You are evaluating whether an AI answer is relevant to the question asked.

Question: {question}

AI answer: {pipeline_answer}

Does the answer directly and specifically address what the question asked?
An answer that is factually true but talks about a different topic should be marked NOT RELEVANT.

Respond with ONLY one of: RELEVANT or NOT RELEVANT
Then on a new line explain briefly why."""

    verdict = invoke_nova(prompt)
    score   = 1 if verdict.upper().startswith("RELEVANT") else 0
    return {"key": "answer_relevance", "score": score, "comment": verdict}


def context_precision_evaluator(run: Run, example: Example) -> dict:
    """
    Were the right chunks retrieved, or was there noise in the top results?
    Checks whether the retrieved chunks are actually useful for answering the question.
    Score: fraction of retrieved chunks that are relevant (0.0 to 1.0).

    Why this matters: you could have a correct answer despite poor retrieval
    (the LLM got lucky). This metric exposes retrieval quality directly.
    """
    context_chunks  = run.outputs.get("context", [])
    question        = example.inputs.get("question", "")
    expected_answer = example.outputs.get("answer", "")

    if not context_chunks:
        return {
            "key": "context_precision",
            "score": 0,
            "comment": "No context chunks returned by pipeline."
        }

    relevant_count = 0
    verdicts       = []

    for i, chunk in enumerate(context_chunks):
        prompt = f"""You are evaluating whether a retrieved text chunk is useful for answering a question.

Question: {question}

Expected answer: {expected_answer}

Retrieved chunk:
{chunk}

Is this chunk relevant and useful for answering the question?
Respond with ONLY one of: RELEVANT or NOT RELEVANT"""

        verdict = invoke_nova(prompt)
        is_relevant = verdict.upper().startswith("RELEVANT")
        if is_relevant:
            relevant_count += 1
        verdicts.append(f"Chunk {i+1}: {'RELEVANT' if is_relevant else 'NOT RELEVANT'}")

    score   = relevant_count / len(context_chunks)
    comment = f"{relevant_count}/{len(context_chunks)} chunks relevant. " + " | ".join(verdicts)
    return {"key": "context_precision", "score": score, "comment": comment}


# ── Step 4: Run evaluation ────────────────────────────────────────────────────

def main():
    langsmith_api_key = os.environ.get("LANGCHAIN_API_KEY")
    if not langsmith_api_key:
        ssm = boto3.client("ssm", region_name="us-east-1")
        param = ssm.get_parameter(Name="/rag/langsmith/api-key", WithDecryption=True)
        langsmith_api_key = param["Parameter"]["Value"]
        os.environ["LANGCHAIN_API_KEY"] = langsmith_api_key

    client = Client()

    dataset_id = create_dataset(client)

    print(f"\nRunning evaluation against live API...")
    print(f"Dataset : {DATASET_NAME}")
    print(f"Project : {PROJECT_NAME}")
    print(f"API     : {API_URL}\n")

    results = evaluate(
        call_rag_pipeline,
        data=DATASET_NAME,
        evaluators=[
            answer_correctness_evaluator,
            faithfulness_evaluator,
            answer_relevance_evaluator,
            context_precision_evaluator,
        ],
        experiment_prefix="johnson-eval",
        metadata={
            "pipeline_version": "langchain-langgraph",
            "document": "johnson_v._superior_court_1.pdf",
            "evaluators": "answer_correctness, faithfulness, answer_relevance, context_precision"
        },
        max_concurrency=1
    )

    # ── Print summary ─────────────────────────────────────────────────────────
    print("\n── Evaluation Results ──────────────────────────────")
    scores = {
        "answer_correctness": [],
        "faithfulness":       [],
        "answer_relevance":   [],
        "context_precision":  []
    }

    for result in results:
        q = result["example"].inputs["question"][:60]
        for eval_result in result["evaluation_results"]["results"]:
            key   = eval_result.key
            score = eval_result.score
            if key in scores:
                scores[key].append(score)
            print(f"  [{key}] {score:.2f} | {q}")

    print("\n── Aggregate Scores ────────────────────────────────")
    for key, vals in scores.items():
        if vals:
            avg = sum(vals) / len(vals)
            print(f"  {key:<22}: {avg:.2f}  ({len(vals)} questions)")

    print(f"\nFull results: https://smith.langchain.com/projects/{PROJECT_NAME}")


if __name__ == "__main__":
    main()