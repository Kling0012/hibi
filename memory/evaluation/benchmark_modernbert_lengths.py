"""Measure the cost of raising ModernBERT's 512-token inference cap."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import time

import torch

from modernbert.engine import CrossEncoder, HUB_CHECKPOINT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--checkpoint", default=HUB_CHECKPOINT)
    parser.add_argument("--lengths", type=int, nargs="+", default=(512, 1024, 2048))
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; evidence is never overwritten")

    encoder = CrossEncoder(args.checkpoint, device="cuda", dtype="float16")
    # Long enough to distinguish all tested caps; the evidence marker is at the tail.
    state = "質問対象とは無関係な背景説明です。" * 500 + " 最後の証拠: 対象サービスのポートは8123。"
    question = "この記憶は対象サービスのポート番号を示していますか。"
    context = f"質問: {question}\n状況: {state}"
    candidates = ["true — 直接の証拠を含む。", "false — 証拠を含まない。"]
    raw_lengths = [len(encoder.tokenizer(context, candidate, truncation=False)["input_ids"])
                   for candidate in candidates]

    encoder.max_length = 512
    encoder.logits([context, context], candidates)
    rows = []
    for max_length in args.lengths:
        encoder.max_length = max_length
        elapsed = []
        peaks = []
        outputs = []
        processed = []
        for _ in range(args.repeats):
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()
            started = time.perf_counter()
            logits, tokens, truncated = encoder.logits([context, context], candidates)
            torch.cuda.synchronize()
            elapsed.append(time.perf_counter() - started)
            peaks.append(torch.cuda.max_memory_allocated() / 1024 / 1024)
            outputs.append(logits)
            processed.append(tokens)
        rows.append({
            "max_length": max_length,
            "median_seconds": statistics.median(elapsed),
            "runs_seconds": elapsed,
            "peak_allocated_mib": max(peaks),
            "processed_tokens": processed,
            "truncated": truncated,
            "last_logits": outputs[-1],
        })
    result = {
        "kind": "modernbert_max_length_microbenchmark",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "device": torch.cuda.get_device_name(0),
        "dtype": "float16",
        "model": encoder.model_id,
        "raw_pair_token_lengths": raw_lengths,
        "model_max_position_embeddings": encoder.model.config.max_position_embeddings,
        "rows": rows,
        "note": "Two-pair synthetic runtime/VRAM probe; not an accuracy benchmark.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
