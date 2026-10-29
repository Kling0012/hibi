"""Read-only evaluation of the isolated canonical search-mapping fix.

Uses retained evaluation graphs and performs no initialization, graph writes,
retries, or result relabeling. Output contains only synthetic IDs and timings.
"""

import os
import argparse
import asyncio
import hashlib
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from evaluation.benchmark_pipeline_live import QUERIES, QUERY_OPTIONS, fixture_id
from gateway.app.config import Settings
from gateway.app.graph_store import GraphStore

MEMORY_USER = os.environ.get("MEMORY_USER", "default")
from schemas.v1.metadata import MemorySearchRequest


DATABASES = {
    "baseline_graph": "am_eval_p1_v2_20260919T092828Z_d8bdee2e19_baseline",
    "new_graph": "am_eval_p1_v2_20260919T092828Z_d8bdee2e19_new",
}
PORTS = {"baseline_graph": 17384, "new_graph": 17385}


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; do not overwrite evidence")
    return args


def percentile95(values):
    ordered = sorted(values)
    return ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)]


async def main():
    args = parse_args()
    stores = {}
    for label in DATABASES:
        settings = Settings(
            _env_file=args.env_file,
            falkordb_host="127.0.0.1",
            falkordb_port=PORTS[label],
            falkordb_database=DATABASES[label],
            memory_embedding_mode="cloudflare",
            memory_embedding_cloudflare_timeout_seconds=60,
        )
        if not settings.cloudflare_account_id or not settings.cloudflare_api_token:
            raise RuntimeError("Cloudflare credentials are not configured")
        stores[label] = GraphStore(settings)

    rows = []
    try:
        for query_id, query, expected_fixture in QUERIES:
            request = MemorySearchRequest(
                query=query, user=MEMORY_USER, project="all", agent="codex", limit=3,
                include_history=QUERY_OPTIONS.get(query_id, {}).get("include_history", False),
            )

            async def run_one(label):
                started = time.monotonic()
                results = await asyncio.wait_for(stores[label].search(request), timeout=120)
                ids = [item["memory"]["memory_id"] for item in results]
                expected = str(fixture_id(expected_fixture))
                return {
                    "graph": label,
                    "query_id": query_id,
                    "expected_memory_id": expected,
                    "returned_memory_ids": ids,
                    "hit_at_1": bool(ids and ids[0] == expected),
                    "hit_at_3": expected in ids[:3],
                    "seconds": time.monotonic() - started,
                }

            rows.extend(await asyncio.gather(*(run_one(label) for label in DATABASES)))
    finally:
        await asyncio.gather(*(store.close() for store in stores.values()), return_exceptions=True)

    summary = {}
    for label in DATABASES:
        selected = [row for row in rows if row["graph"] == label]
        times = [row["seconds"] for row in selected]
        summary[label] = {
            "count": len(selected),
            "hit_at_1": sum(row["hit_at_1"] for row in selected),
            "hit_at_3": sum(row["hit_at_3"] for row in selected),
            "median_seconds": statistics.median(times),
            "p95_seconds": percentile95(times),
        }
    source = Path(__file__)
    output = {
        "kind": "read_only_isolated_search_mapping_evaluation",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "mapping_policy": "no premature canonical LIMIT; preserve edge/provenance order; no new fact invalidation filter",
        "graphs": DATABASES,
        "summary": summary,
        "rows": rows,
        "limitations": [
            "Synthetic fixed cases; not production-answer accuracy.",
            "Graphs were produced by different ingestion variants; compare mapping within each graph, not ingestion causality.",
            "One search per query/graph, no retry; provider and network latency are not controlled.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
