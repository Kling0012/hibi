"""Benchmark Laya on the frozen 15-candidate Agent Memory rerank set.

The candidate file is shared with the TypeSafe and ModernBERT comparisons.  No
candidate generation or database access happens here.  Output is append-only by
convention: an existing output path is never overwritten.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time


INSTRUCTIONS = (
    "質問への回答に最も直接役立つ記憶候補を一つ選んでください。"
    "対象、環境、時点が一致することを重視し、単語の類似だけで選ばないでください。"
)
NOUL_PREFIX = (
    "次の記憶候補は質問への回答に直接役立つ証拠を含みますか。"
    "対象、環境、時点が一致することを重視し、単語が似ているだけならfalse。\n"
    "記憶候補:\n"
)
NOUL_CRITERIA = {
    "true": "質問に直接答えるための証拠を含む。",
    "false": "無関係、別対象、別時点、または表面的に似るだけ。",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--laya-repo", type=Path, required=True)
    parser.add_argument("--candidate-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--checkpoint", default="convaiinnovations/laya")
    parser.add_argument("--subfolder", default="multilingual")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; evidence is never overwritten")
    return args


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p * len(ordered)) - 1)]


def candidate_text(result: dict) -> str:
    memory = result["memory"]
    return "\n".join([
        f"種別: {memory.get('type')}",
        f"名前: {memory.get('name') or ''}",
        f"内容: {memory.get('content') or ''}",
        f"一致箇所: {result.get('matched_fact') or ''}",
        f"有効期間: {memory.get('valid_from') or ''} から {memory.get('valid_to') or '現在'}",
    ])


def questions(row: dict, style: str) -> dict:
    if style == "choice":
        return {"best_candidate": {
            "type": "choice",
            "instructions": INSTRUCTIONS,
            "criteria": {
                f"candidate_{index}": candidate_text(candidate)
                for index, candidate in enumerate(row["candidates"])
            },
        }}
    return {
        f"candidate_{index}": {
            "type": "noul",
            "instructions": NOUL_PREFIX + candidate_text(candidate),
            "criteria": NOUL_CRITERIA,
        }
        for index, candidate in enumerate(row["candidates"])
    }


def sequence_diagnostics(agent, rows: list[dict], style: str) -> dict:
    from laya.common import build_sequence, render_options

    lengths = []
    raw_instruction_lengths = []
    raw_option_lengths = []
    instruction_truncated = 0
    option_over_48 = 0
    for row in rows:
        for qdef in questions(row, style).values():
            q = agent._to_internal(qdef)
            seq, _ = build_sequence(
                agent.tok,
                {"query": row["query"]},
                q,
                agent.cfg["max_len"],
                agent.cfg["head_max_len"],
            )
            lengths.append(len(seq))
            raw_head = len(agent.tok(
                f'{q["t"]} question: {q["ins"]}', add_special_tokens=False
            )["input_ids"])
            option_lengths = [
                len(agent.tok(" " + option, add_special_tokens=False)["input_ids"])
                for option in render_options(q)
            ]
            capped_sum = sum(1 + min(48, length) for length in option_lengths)
            available = max(8, agent.cfg["head_max_len"] - capped_sum)
            raw_instruction_lengths.append(raw_head)
            raw_option_lengths.extend(option_lengths)
            instruction_truncated += raw_head > available
            option_over_48 += sum(length > 48 for length in option_lengths)
    return {
        "sequences": len(lengths),
        "sequence_tokens_min": min(lengths),
        "sequence_tokens_median": statistics.median(lengths),
        "sequence_tokens_max": max(lengths),
        "raw_instruction_tokens_max": max(raw_instruction_lengths),
        "raw_option_tokens_max": max(raw_option_lengths),
        "instructions_truncated": instruction_truncated,
        "options_over_hard_48_token_cap": option_over_48,
    }


def rank(agent, row: dict, style: str) -> dict:
    import torch

    payload = questions(row, style)
    if agent.device.type == "cuda":
        torch.cuda.synchronize()
    started = time.perf_counter()
    response = agent.predict({"query": row["query"]}, payload)
    if agent.device.type == "cuda":
        torch.cuda.synchronize()
    elapsed = time.perf_counter() - started
    if style == "choice":
        probabilities = response["answers"]["best_candidate"]["probabilities"]
        scores = [probabilities[f"candidate_{index}"] for index in range(15)]
    else:
        scores = [response["answers"][f"candidate_{index}"]["noul"] for index in range(15)]
    order = sorted(range(15), key=lambda index: (-scores[index], index))
    returned = [row["candidates"][index]["memory"]["memory_id"] for index in order]
    expected = row["expected_memory_id"]
    return {
        "query_id": row["query_id"],
        "seconds": elapsed,
        "expected_memory_id": expected,
        "returned_memory_ids": returned,
        "scores": scores,
        "hit_at_1": returned[0] == expected,
        "hit_at_3": expected in returned[:3],
        "input_tokens": response["usage"]["input_tokens"],
    }


def run_variant(agent, rows: list[dict], variant: dict) -> dict:
    import torch

    agent.cfg["max_len"] = variant["max_len"]
    agent.cfg["head_max_len"] = variant["head_max_len"]
    agent.dtype = getattr(torch, variant["dtype"])
    diagnostics = sequence_diagnostics(agent, rows, variant["style"])
    rank(agent, rows[0], variant["style"])
    if agent.device.type != "cuda" and variant["device_required"] == "cuda":
        raise RuntimeError("Laya fell back from CUDA during warm-up")
    if agent.device.type == "cuda":
        torch.cuda.reset_peak_memory_stats()
    measured = []
    for row in rows:
        result = rank(agent, row, variant["style"])
        measured.append(result)
        print(json.dumps({
            "variant": variant["name"],
            "query_id": row["query_id"],
            "seconds": result["seconds"],
            "hit_at_1": result["hit_at_1"],
        }, ensure_ascii=False), flush=True)
    latencies = [row["seconds"] for row in measured]
    return {
        **variant,
        "device_observed": str(agent.device),
        "sequence_diagnostics": diagnostics,
        "summary": {
            "queries": len(measured),
            "hit_at_1": sum(row["hit_at_1"] for row in measured),
            "hit_at_3": sum(row["hit_at_3"] for row in measured),
            "mean_seconds": statistics.mean(latencies),
            "median_seconds": statistics.median(latencies),
            "p95_seconds": percentile(latencies, 0.95),
            "max_seconds": max(latencies),
            "median_input_tokens": statistics.median(row["input_tokens"] for row in measured),
            "peak_allocated_mib": (
                torch.cuda.max_memory_allocated() / 1024 / 1024
                if agent.device.type == "cuda" else None
            ),
        },
        "rows": measured,
    }


def main() -> None:
    args = parse_args()
    sys.path.insert(0, str(args.laya_repo))
    import laya
    import torch

    source = json.loads(args.candidate_file.read_text())
    rows = source["rows"][:args.limit or None]
    load_started = time.perf_counter()
    agent = laya.load(args.checkpoint, subfolder=args.subfolder, device=args.device)
    load_seconds = time.perf_counter() - load_started
    cuda_variants = [
        {"name": "choice-default-fp16", "style": "choice", "dtype": "float16",
         "max_len": 1024, "head_max_len": 256, "device_required": "cuda"},
        {"name": "choice-extended-fp16", "style": "choice", "dtype": "float16",
         "max_len": 1536, "head_max_len": 1024, "device_required": "cuda"},
        {"name": "noul-extended-fp16", "style": "noul", "dtype": "float16",
         "max_len": 768, "head_max_len": 512, "device_required": "cuda"},
        {"name": "choice-extended-fp32", "style": "choice", "dtype": "float32",
         "max_len": 1536, "head_max_len": 1024, "device_required": "cuda"},
        {"name": "noul-extended-fp32", "style": "noul", "dtype": "float32",
         "max_len": 768, "head_max_len": 512, "device_required": "cuda"},
    ]
    cpu_variants = [
        {"name": "noul-extended-cpu-fp32", "style": "noul", "dtype": "float32",
         "max_len": 768, "head_max_len": 512, "device_required": "cpu"},
    ]
    variants = cpu_variants if args.device == "cpu" else cuda_variants
    results = [run_variant(agent, rows, variant) for variant in variants]
    output = {
        "kind": "laya_frozen_candidate_rerank_benchmark",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "laya_repo": str(args.laya_repo),
        "laya_commit": "42626c348753fbb17572a813127df2278a1ec527",
        "checkpoint": args.checkpoint,
        "subfolder": args.subfolder,
        "candidate_file": str(args.candidate_file),
        "candidate_file_sha256": hashlib.sha256(args.candidate_file.read_bytes()).hexdigest(),
        "candidate_count": 15,
        "query_count": len(rows),
        "load_seconds": load_seconds,
        "torch": torch.__version__,
        "cuda_device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "variants": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "output": str(args.output),
        "summaries": {result["name"]: result["summary"] for result in results},
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
