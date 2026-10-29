"""Import durable records from the old all-memory SQLite database through REST."""

import argparse
import json
import os
import re
import sqlite3
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


TYPE_MAP = {
    "decision": "decision",
    "fact": "stable_fact",
    "state": "project_configuration",
}

SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(token|api[_ -]?key|password|secret)\s*[:=]\s*([^\s,、。)]+)"
)


def infer_project(topic: str, default: str) -> str:
    first = topic.strip("/").split("/", 1)[0]
    return first or default


def post(url: str, payload: dict, timeout_seconds: float = 3600) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.load(response)


def prepare_content(content: str, refs: list[str]) -> str:
    """Apply the current memory policy without mutating the legacy database."""
    if content.startswith("[") and "の写し" in content[:120]:
        target = refs[0] if refs else "正本ファイル"
        return (
            f"旧共有記憶に保存されていた {target} のスナップショット本文は、"
            "ファイル本文を記憶へ複製しない現行方針に従い移行時に省略。正本はrefsを参照。"
        )
    return SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", content)


def load_manifest(path: Path) -> dict[int, dict]:
    """Return the latest durable state for every attempted legacy row."""
    states: dict[int, dict] = {}
    if not path.exists():
        return states
    with path.open(encoding="utf-8") as manifest:
        for line_number, line in enumerate(manifest, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                states[int(event["legacy_id"])] = event
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(f"invalid manifest line {line_number}: {path}") from exc
    return states


def append_manifest(path: Path, event: dict) -> None:
    """Append and fsync so an interrupted import never silently retries a POST."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as manifest:
        manifest.write(json.dumps(event, ensure_ascii=False) + "\n")
        manifest.flush()
        os.fsync(manifest.fileno())


def select_rows(connection: sqlite3.Connection, *, only_id: int | None, start_after_id: int | None):
    where = "kind IN ('fact','decision','state')"
    params: tuple[int, ...] = ()
    if only_id is not None:
        where += " AND id = ?"
        params = (only_id,)
    elif start_after_id is not None:
        where += " AND id > ?"
        params = (start_after_id,)
    return connection.execute(f"SELECT * FROM entry WHERE {where} ORDER BY id", params).fetchall()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database")
    parser.add_argument(
        "--base-url", required=True,
        help="target Gateway base URL (required to avoid accidentally using legacy port 8377)",
    )
    parser.add_argument("--user", default="default")
    parser.add_argument("--project", default="all")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=3600,
        help="seconds to wait for one Graphiti write (default: 3600)",
    )
    parser.add_argument(
        "--manifest",
        help="JSONL checkpoint file (required unless --dry-run); keep it until migration is verified",
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--only-id", type=int, help="import exactly one legacy entry ID")
    selection.add_argument("--start-after-id", type=int, help="resume with IDs greater than this value")
    parser.add_argument(
        "--retry-pending-id",
        type=int,
        help=(
            "retry one manifest entry left pending only after verifying that no canonical "
            "MemoryRecord was created"
        ),
    )
    args = parser.parse_args()

    if not args.dry_run and not args.manifest:
        parser.error("--manifest is required unless --dry-run")

    database_uri = Path(args.database).resolve().as_uri() + "?mode=ro&immutable=1"
    connection = sqlite3.connect(database_uri, uri=True)
    connection.row_factory = sqlite3.Row
    rows = select_rows(
        connection, only_id=args.only_id, start_after_id=args.start_after_id,
    )
    if args.only_id is not None and not rows:
        parser.error(f"legacy entry {args.only_id} is absent or not a durable kind")
    manifest_path = Path(args.manifest) if args.manifest else None
    states = load_manifest(manifest_path) if manifest_path and not args.dry_run else {}
    for row in rows:
        created = datetime.fromisoformat(row["first_ts"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        refs = json.loads(row["refs"])
        payload = {
            "idempotency_key": f"legacy-sqlite:entry/{row['id']}",
            "type": TYPE_MAP[row["kind"]],
            "content": prepare_content(row["body"], refs),
            "scope": {"user": args.user, "project": args.project, "agent": None},
            "source": {
                "agent": row["author"].split("@", 1)[0],
                "session": "legacy-sqlite-import",
                "uri": f"legacy-sqlite:entry/{row['id']}",
            },
            "visibility": "project",
            "valid_from": created.isoformat(),
            "refs": refs,
            "tags": list(dict.fromkeys(json.loads(row["tags"]) + [f"legacy-topic:{row['topic']}"])),
            "name": row["name"],
        }
        if args.dry_run:
            print(f"legacy {row['id']}: {json.dumps(payload, ensure_ascii=False)}")
        else:
            previous = states.get(row["id"])
            if previous and previous["status"] == "imported":
                print(f"legacy {row['id']} already imported -> {previous['memory_id']}")
                continue
            retry_verified_pending = (
                previous
                and previous["status"] == "pending"
                and row["id"] == args.retry_pending_id
            )
            if previous and not retry_verified_pending:
                raise RuntimeError(
                    f"legacy {row['id']} has an unresolved {previous['status']} attempt; "
                    "verify the Gateway/FalkorDB result before changing the manifest or retrying"
                )
            if retry_verified_pending:
                append_manifest(manifest_path, {
                    "legacy_id": row["id"],
                    "status": "retrying_verified_absent",
                    "attempted_at": datetime.now(timezone.utc).isoformat(),
                })
            attempted_at = datetime.now(timezone.utc).isoformat()
            append_manifest(manifest_path, {
                "legacy_id": row["id"], "status": "pending", "attempted_at": attempted_at,
            })
            result = post(
                f"{args.base_url}/v1/memories", payload, timeout_seconds=args.request_timeout
            )
            append_manifest(manifest_path, {
                "legacy_id": row["id"], "status": "imported",
                "attempted_at": attempted_at, "memory_id": result["memory_id"],
            })
            print(f"legacy {row['id']} -> {result['memory_id']}")


if __name__ == "__main__":
    main()
