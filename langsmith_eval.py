# langsmith_eval.py
"""
LangSmith Evaluator for RAG Pipeline
- Creates a dataset of 8 Q&A pairs from johnson_v._superior_court_1.pdf
- Runs answer correctness + retrieval relevance evals
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

API_URL     = "https://k0injz47nk.execute-api.us-east-1.amazonaws.com/prod/query"
API_KEY     = "gPEWpbTVLq9NPD0YJYD4pCszNr7UcF4ln1etxZd0"
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
    # Check if dataset already exists
    datasets = list(client.list_datasets(dataset_name=DATASET_NAME))
    if datasets:
        print(f"Dataset already exists: {DATASET_NAME}")
        return datasets[0].id

    print(f"Creating dataset: {DATASET_NAME}")
    dataset = client.create_dataset(
        dataset_name=DATASET_NAME,
        description="Q&A pairs from johnson_v._superior_court_1.pdf for RAG eval"
    )

    # Add examples
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
    # API Gateway returns body as a JSON string, parse it
    body = response.json()
    if isinstance(body.get("body"), str):
        result = json.loads(body["body"])
    else:
        result = body
    return {
        "answer": result.get("answer", ""),
        "sources": result.get("sources", [])
    }

# ── Step 3: Custom evaluators ─────────────────────────────────────────────────

def answer_correctness_evaluator(run: Run, example: Example) -> dict:
    """
    Uses Nova Micro to judge whether the pipeline answer matches
    the expected answer. Returns a score of 0 or 1.
    """
    pipeline_answer  = run.outputs.get("answer", "")
    expected_answer  = example.outputs.get("answer", "")
    question         = example.inputs.get("question", "")

    bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

    prompt = f"""You are evaluating a RAG pipeline answer.

Question: {question}

Expected answer: {expected_answer}

Pipeline answer: {pipeline_answer}

Does the pipeline answer correctly address the question and align with the expected answer?
Respond with ONLY one of: CORRECT or INCORRECT
Then on a new line explain briefly why."""

    response = bedrock.invoke_model(
        modelId="amazon.nova-micro-v1:0",
        body=json.dumps({
            "messages": [{"role": "user", "content": [{"text": prompt}]}]
        })
    )
    result  = json.loads(response["body"].read())
    verdict = result["output"]["message"]["content"][0]["text"].strip()
    score   = 1 if verdict.upper().startswith("CORRECT") else 0

    return {"key": "answer_correctness", "score": score, "comment": verdict}


def retrieval_relevance_evaluator(run: Run, example: Example) -> dict:
    """
    Checks whether at least one retrieved source is relevant to the question.
    Simple heuristic: if sources list is non-empty, retrieval worked.
    Score = number of sources returned (max 5).
    """
    sources = run.outputs.get("sources", [])
    score   = min(len(sources), 5) / 5  # normalize to 0-1

    return {
        "key": "retrieval_relevance",
        "score": score,
        "comment": f"{len(sources)} sources retrieved"
    }


# ── Step 4: Run evaluation ────────────────────────────────────────────────────

def main():
    # Load LangSmith API key from environment or Parameter Store
    langsmith_api_key = os.environ.get("LANGCHAIN_API_KEY")
    if not langsmith_api_key:
        ssm = boto3.client("ssm", region_name="us-east-1")
        param = ssm.get_parameter(Name="/rag/langsmith/api-key", WithDecryption=True)
        langsmith_api_key = param["Parameter"]["Value"]
        os.environ["LANGCHAIN_API_KEY"] = langsmith_api_key

    client = Client()

    # Create dataset
    dataset_id = create_dataset(client)

    print(f"\nRunning evaluation against live API...")
    print(f"Dataset : {DATASET_NAME}")
    print(f"Project : {PROJECT_NAME}")
    print(f"API     : {API_URL}\n")

    # Run evaluation
    results = evaluate(
        call_rag_pipeline,
        data=DATASET_NAME,
        evaluators=[answer_correctness_evaluator, retrieval_relevance_evaluator],
        experiment_prefix="johnson-eval",
        metadata={"pipeline_version": "langchain-langgraph", "document": "johnson_v._superior_court_1.pdf"},
        max_concurrency=1   # sequential — avoids hammering Lambda cold starts
    )

    # Print summary
    print("\n── Evaluation Results ──────────────────────────────")
    scores = {"answer_correctness": [], "retrieval_relevance": []}

    for result in results:
        q = result["example"].inputs["question"][:60]
        for eval_result in result["evaluation_results"]["results"]:
            key   = eval_result.key
            score = eval_result.score
            scores[key].append(score)
            print(f"  [{key}] {score:.2f} | {q}")

    print("\n── Aggregate Scores ────────────────────────────────")
    for key, vals in scores.items():
        if vals:
            avg = sum(vals) / len(vals)
            print(f"  {key}: {avg:.2f} ({len(vals)} questions)")

    print(f"\nFull results: https://smith.langchain.com/projects/{PROJECT_NAME}")


if __name__ == "__main__":
    main()