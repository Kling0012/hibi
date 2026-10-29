"""Practical read-only P3/P6 benchmark on the retained 20-record synthetic graph.

The benchmark uses one already-ingested graph for every mode, performs no graph
writes or retries, and retains its JSON result.  It measures sequential user
queries plus fresh-client groups of three concurrent queries.
"""
from __future__ import annotations

import os
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import time
from uuid import NAMESPACE_URL, uuid5

MEMORY_USER = os.environ.get("MEMORY_USER", "default")

from gateway.app.config import Settings
from gateway.app.graph_store import GraphStore
from schemas.v1.metadata import MemorySearchRequest


FIXTURE_VERSION = "graphiti-pipeline-ja-v2"
DATABASE = "am_eval_p1_v2_20260919T092828Z_d8bdee2e19_new"
SEQUENTIAL_PLAN = (
    ("off", "off"),
    ("shadow", "shadow"),
    ("on", "on"),
    ("off_repeat", "off"),
)
CONCURRENT_PLAN = (("off_concurrent_3", "off"), ("on_concurrent_3", "on"))
QUERIES = (
    ("current_port", "cedar-prod の AmberSearch が現在使用するポート番号", "04", False),
    ("other_host", "cedar-test の AmberSearch が使用するポート番号", "05", False),
    ("alias", "CedarServer はどのホストの別名か", "02", False),
    ("production_project", "青葉プロジェクトの本番ホストと稼働する検索サービス", "01", False),
    ("index_target", "LibraryIndex は何の索引で、どのサービスから検索されるか", "06", False),
    ("ssh_destination", "FernLab の実ホスト名、OS、藤棚プロジェクトの配置場所", "07", False),
    ("remote_precheck", "FernLab にSSH接続した直後、設定変更前に確認すること", "08", False),
    ("os_launchers", "葵UI のMac版とWindows版の場所と起動ファイル", "09", False),
    ("dependency_isolation", "葵UI の .venv と node_modules を別OSへ同期してよいか", "10", False),
    ("no_delete", "青葉プロジェクトの検証記録や古いバックアップを削除してよいか", "11", False),
    ("change_procedure", "cedar-test の config.toml を変更する前後の確認手順", "12", False),
    ("incident_symptom", "cedar-test のポート8500で発生した connection refused の症状", "13", False),
    ("incident_cause", "cedar-test の AmberSearch が起動しなかった原因の古い作業ディレクトリ", "14", False),
    ("incident_solution", "cedar-test の AmberSearch を復旧させるために修正した設定", "15", False),
    ("incident_verification", "cedar-test の復旧後に health と検索で確認した結果", "16", False),
    ("decision_boundary", "藤棚でJevとGraphitiをどう役割分担し、曖昧な判定をどう扱うか", "17", False),
    ("embedding_provider", "藤棚の埋込みモデル、提供元、ローカルGPU利用の状態", "18", False),
    ("shared_memory_policy", "藤棚の全端末とSSH先の記憶はどこへ集約し、いつ検索保存するか", "19", False),
    ("manual_entry", "藤棚の接続マニュアルと再開時に読む案内ファイル", "20", False),
    ("historical_port", "2026年9月3日時点で cedar-prod の AmberSearch が使用していたポート", "03", True),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--falkordb-port", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--operation-timeout", type=float, default=120.0)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; evidence is never overwritten")
    if not 1024 <= args.falkordb_port <= 65535:
        parser.error("invalid evaluation FalkorDB port")
    return args


def fixture_id(key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"urn:agent-memory:synthetic:{FIXTURE_VERSION}:{key}"))


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(probability * len(ordered)) - 1)]


def latency(values: list[float]) -> dict:
    return {
        "n": len(values),
        "mean_seconds": statistics.mean(values) if values else None,
        "median_seconds": statistics.median(values) if values else None,
        "p95_seconds": percentile(values, 0.95),
        "sum_seconds": sum(values),
    }


def settings_for(args: argparse.Namespace, mode: str) -> Settings:
    settings = Settings(
        _env_file=args.env_file,
        falkordb_host="127.0.0.1",
        falkordb_port=args.falkordb_port,
        falkordb_database=DATABASE,
        memory_embedding_mode="cloudflare",
        memory_embedding_cloudflare_timeout_seconds=60,
        memory_search_rerank_mode=mode,
        memory_search_rerank_candidates=15,
        memory_search_rerank_batch_size=20,
        memory_search_rerank_timeout_seconds=20,
        memory_decision_backend="typesafe",
        memory_jev_max_concurrency=3,
    )
    if not settings.cloudflare_account_id or not settings.cloudflare_api_token:
        raise RuntimeError("Cloudflare credentials are missing")
    if mode != "off" and not settings.typesafe_api_key:
        raise RuntimeError("TypeSafe credentials are missing")
    return settings


async def search_one(store: GraphStore, case: tuple[str, str, str, bool], timeout: float) -> dict:
    query_id, query, fixture, include_history = case
    request = MemorySearchRequest(
        query=query,
        user=MEMORY_USER,
        project="all",
        agent="codex",
        limit=3,
        include_history=include_history,
    )
    started = time.monotonic()
    try:
        results = await asyncio.wait_for(store.search(request), timeout=timeout)
        elapsed = time.monotonic() - started
        returned = [item["memory"]["memory_id"] for item in results]
        expected = fixture_id(fixture)
        return {
            "query_id": query_id,
            "status": "ok",
            "seconds": elapsed,
            "expected_memory_id": expected,
            "returned_memory_ids": returned,
            "hit_at_1": bool(returned and returned[0] == expected),
            "hit_at_3": expected in returned[:3],
        }
    except Exception as exc:
        return {
            "query_id": query_id,
            "status": "error",
            "seconds": time.monotonic() - started,
            "error_class": type(exc).__name__,
        }


def summarize_rows(rows: list[dict]) -> dict:
    ok = [row for row in rows if row["status"] == "ok"]
    return {
        "attempted": len(rows),
        "successful": len(ok),
        "errors": len(rows) - len(ok),
        "hit_at_1": sum(row.get("hit_at_1") is True for row in ok),
        "hit_at_3": sum(row.get("hit_at_3") is True for row in ok),
        "latency": latency([row["seconds"] for row in ok]),
    }


async def sequential_pass(args: argparse.Namespace, label: str, mode: str) -> dict:
    store = GraphStore(settings_for(args, mode))
    rows = []
    started = time.monotonic()
    try:
        for case in QUERIES:
            rows.append(await search_one(store, case, args.operation_timeout))
    finally:
        rerank_metrics = dict(store.search_reranker.metrics)
        embedding_metrics = store.graphiti.embedder.snapshot()
        await store.close()
    return {
        "label": label,
        "mode": mode,
        "wall_seconds": time.monotonic() - started,
        "summary": summarize_rows(rows),
        "rerank_metrics": rerank_metrics,
        "embedding_metrics": embedding_metrics,
        "rows": rows,
    }


async def concurrent_pass(args: argparse.Namespace, label: str, mode: str) -> dict:
    store = GraphStore(settings_for(args, mode))
    rows = []
    batch_walls = []
    started = time.monotonic()
    try:
        for offset in range(0, 9, 3):
            batch_started = time.monotonic()
            batch = await asyncio.gather(*(
                search_one(store, case, args.operation_timeout)
                for case in QUERIES[offset:offset + 3]
            ))
            batch_walls.append(time.monotonic() - batch_started)
            for row in batch:
                row["batch"] = offset // 3
            rows.extend(batch)
    finally:
        rerank_metrics = dict(store.search_reranker.metrics)
        embedding_metrics = store.graphiti.embedder.snapshot()
        await store.close()
    return {
        "label": label,
        "mode": mode,
        "concurrency": 3,
        "wall_seconds": time.monotonic() - started,
        "batch_wall_latency": latency(batch_walls),
        "summary": summarize_rows(rows),
        "rerank_metrics": rerank_metrics,
        "embedding_metrics": embedding_metrics,
        "rows": rows,
    }


async def main() -> None:
    args = parse_args()
    started = datetime.now(timezone.utc)
    sequential = []
    for label, mode in SEQUENTIAL_PLAN:
        print(f"starting sequential {label}", flush=True)
        sequential.append(await sequential_pass(args, label, mode))
        print(json.dumps({"finished": label, **sequential[-1]["summary"]}, ensure_ascii=False), flush=True)
    concurrent = []
    for label, mode in CONCURRENT_PLAN:
        print(f"starting concurrent {label}", flush=True)
        concurrent.append(await concurrent_pass(args, label, mode))
        print(json.dumps({"finished": label, **concurrent[-1]["summary"]}, ensure_ascii=False), flush=True)

    by_label = {run["label"]: run for run in sequential}
    off = {row["query_id"]: row for row in by_label["off"]["rows"] if row["status"] == "ok"}
    on = {row["query_id"]: row for row in by_label["on"]["rows"] if row["status"] == "ok"}
    comparable = sorted(set(off) & set(on))
    comparison = {
        "comparable_queries": len(comparable),
        "top1_changed": sum(off[key]["returned_memory_ids"][:1] != on[key]["returned_memory_ids"][:1]
                            for key in comparable),
        "hit_at_1_gained": [key for key in comparable if not off[key]["hit_at_1"] and on[key]["hit_at_1"]],
        "hit_at_1_lost": [key for key in comparable if off[key]["hit_at_1"] and not on[key]["hit_at_1"]],
        "hit_at_3_gained": [key for key in comparable if not off[key]["hit_at_3"] and on[key]["hit_at_3"]],
        "hit_at_3_lost": [key for key in comparable if off[key]["hit_at_3"] and not on[key]["hit_at_3"]],
    }
    source = Path(__file__)
    output = {
        "kind": "p2_p6_practical_read_only_benchmark",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": started.isoformat(),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "database": DATABASE,
        "records": 20,
        "sequential": sequential,
        "concurrent": concurrent,
        "off_vs_on": comparison,
        "conditions": {
            "same_retained_graph_all_modes": True,
            "writes": 0,
            "harness_retries": 0,
            "sequential_queries_per_mode": len(QUERIES),
            "concurrent_queries_per_mode": 9,
            "concurrency": 3,
            "result_limit": 3,
            "rerank_candidates": 15,
            "rerank_backend": "official TypeSafe",
            "embedding_backend": "Cloudflare",
        },
        "limitations": [
            "Fixed representative synthetic records, not private production-memory sampling.",
            "Primary-record Hit@1/3 is evidence retrieval accuracy, not generated-answer accuracy.",
            "Each pass is one live observation; provider and network latency vary.",
            "off_repeat brackets time drift but does not create a randomized controlled trial.",
            "The concurrent phase uses fresh clients and only the first nine declared queries.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "comparison": comparison}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
