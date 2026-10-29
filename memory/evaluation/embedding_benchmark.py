"""Evaluate an OpenAI-compatible embedding endpoint on a fixed retrieval set."""

import argparse
import json
import math
import os
import statistics
import time
import urllib.request
from pathlib import Path


DEFAULT_INSTRUCTION = (
    "Instruct: Given a user memory search query, retrieve the most relevant stored memory.\n"
    "Query:{query}"
)


def normalize(vector: list[float], truncate_dim: int | None) -> list[float]:
    if truncate_dim is not None:
        vector = vector[:truncate_dim]
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        raise ValueError("embedding has zero norm")
    return [value / norm for value in vector]


def embed(endpoint: str, api_key: str, model: str, texts: list[str]) -> tuple[list[list[float]], float]:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps({"model": model, "input": texts}, ensure_ascii=False).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=900) as response:
        payload = json.load(response)
    elapsed = time.perf_counter() - started
    data = sorted(payload["data"], key=lambda item: item["index"])
    return [item["embedding"] for item in data], elapsed


def embed_repeated(
    endpoint: str,
    api_key: str,
    model: str,
    texts: list[str],
    repeats: int,
) -> tuple[list[list[float]], list[float]]:
    vectors: list[list[float]] | None = None
    timings = []
    for _ in range(repeats):
        current_vectors, elapsed = embed(endpoint, api_key, model, texts)
        if vectors is None:
            vectors = current_vectors
        timings.append(elapsed)
    if vectors is None:
        raise RuntimeError("embedding benchmark produced no vectors")
    return vectors, timings


def dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-env", default="EMBEDDING_BENCHMARK_API_KEY")
    parser.add_argument("--query-instruction", action="store_true")
    parser.add_argument("--truncate-dim", type=int)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--single-query-repeats", type=int, default=0)
    args = parser.parse_args()

    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    if args.single_query_repeats < 0:
        parser.error("--single-query-repeats must not be negative")

    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        parser.error(f"environment variable {args.api_key_env} is required")

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    documents = dataset["documents"]
    queries = dataset["queries"]
    query_texts = [item["text"] for item in queries]
    if args.query_instruction:
        query_texts = [DEFAULT_INSTRUCTION.format(query=text) for text in query_texts]

    query_vectors, query_timings = embed_repeated(
        args.endpoint, api_key, args.model, query_texts, args.repeats
    )
    document_vectors, document_timings = embed_repeated(
        args.endpoint,
        api_key,
        args.model,
        [item["text"] for item in documents],
        args.repeats,
    )
    single_query_timings = []
    if args.single_query_repeats:
        _, single_query_timings = embed_repeated(
            args.endpoint,
            api_key,
            args.model,
            [query_texts[0]],
            args.single_query_repeats,
        )
    source_dimension = len(query_vectors[0])
    query_vectors = [normalize(vector, args.truncate_dim) for vector in query_vectors]
    document_vectors = [normalize(vector, args.truncate_dim) for vector in document_vectors]

    doc_indexes = {item["id"]: index for index, item in enumerate(documents)}
    ranks: list[int] = []
    hard_negative_wins = 0
    failures = []
    for query, query_vector in zip(queries, query_vectors, strict=True):
        scores = [dot(query_vector, vector) for vector in document_vectors]
        ranking = sorted(range(len(documents)), key=lambda index: scores[index], reverse=True)
        expected_index = doc_indexes[query["expected_doc_id"]]
        rank = ranking.index(expected_index) + 1
        ranks.append(rank)
        negative_indexes = [doc_indexes[item] for item in query.get("hard_negative_doc_ids", [])]
        if not negative_indexes or scores[expected_index] > max(scores[index] for index in negative_indexes):
            hard_negative_wins += 1
        if rank != 1:
            failures.append(
                {
                    "query_id": query["id"],
                    "expected": query["expected_doc_id"],
                    "rank": rank,
                    "top1": documents[ranking[0]]["id"],
                }
            )

    result = {
        "dataset": dataset["dataset_id"],
        "model": args.model,
        "query_instruction": args.query_instruction,
        "source_dimension": source_dimension,
        "evaluated_dimension": len(query_vectors[0]),
        "repeats": args.repeats,
        "query_batch_seconds": round(statistics.median(query_timings), 4),
        "document_batch_seconds": round(statistics.median(document_timings), 4),
        "query_batch_seconds_all": [round(value, 4) for value in query_timings],
        "document_batch_seconds_all": [round(value, 4) for value in document_timings],
        "query_texts_per_second": round(len(queries) / statistics.median(query_timings), 2),
        "document_texts_per_second": round(
            len(documents) / statistics.median(document_timings), 2
        ),
        "single_query_seconds": (
            round(statistics.median(single_query_timings), 4)
            if single_query_timings
            else None
        ),
        "single_query_seconds_all": [
            round(value, 4) for value in single_query_timings
        ],
        "recall_at_1": sum(rank == 1 for rank in ranks) / len(ranks),
        "recall_at_3": sum(rank <= 3 for rank in ranks) / len(ranks),
        "mrr": statistics.fmean(1 / rank for rank in ranks),
        "hard_negative_accuracy": hard_negative_wins / len(ranks),
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
