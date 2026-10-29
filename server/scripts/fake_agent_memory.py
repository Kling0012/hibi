#!/usr/bin/env python3
"""Minimal fake agent-memory for sync-server E2E (P3-A).

Serves GET /v1/memories with the production gateway's response shape:
{"items": [...], "next_cursor": base64url({"v": iso, "i": uuid}) | None}.

Records have fixed uuids (a001..a005) and created_at relative to server
start (40/30/20/10 minutes ago; a004 supersedes a001). --extra adds one
"now" record (a005) for the recovery test. --page-size caps items per
page regardless of the requested limit, to exercise cursor paging.

Read-only; nothing is persisted.
"""
from __future__ import annotations

import argparse
import base64
import json
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

T0 = datetime.now(timezone.utc)

A1 = "00000000-0000-4000-8000-00000000a001"
A2 = "00000000-0000-4000-8000-00000000a002"
A3 = "00000000-0000-4000-8000-00000000a003"
A4 = "00000000-0000-4000-8000-00000000a004"
A5 = "00000000-0000-4000-8000-00000000a005"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _mk(mid, minutes_ago, mtype, tags, content, agent="claude", supersedes=None):
    dt = T0 - timedelta(minutes=minutes_ago)
    return {
        "_dt": dt,
        "item": {
            "type": mtype,
            "content": content,
            "scope": {"user": "user", "project": "all", "agent": None},
            "source": {
                "agent": agent,
                "session": f"e2e-session-{mid[-4:]}",
                "uri": "e2e:/fake/project",
            },
            "visibility": "project",
            "valid_from": _iso(dt),
            "refs": [],
            "tags": tags,
            "name": None,
            "idempotency_key": None,
            "memory_id": mid,
            "created_at": _iso(dt),
            "valid_to": None,
            "schema_version": 1,
            "episode_uuid": None,
            "supersedes": supersedes,
            "superseded_by": None,
            "enrichment_status": "ready",
            "enrichment_error": "",
        },
    }


def build_items(extra: bool) -> list[dict]:
    items = [
        _mk(A1, 40, "decision", ["minecontext", "p3a"], "E2E用の決定事項その1（a001、後にa004が置換）"),
        _mk(A2, 30, "solution", ["e2e"], "E2E用の解決策その2（a002、別エージェント）", agent="codex"),
        _mk(A3, 20, "requirement", [], "E2E用の要件その3（a003）"),
        _mk(A4, 10, "decision", ["minecontext"], "E2E用の決定事項その1の置換（a004）", supersedes=A1),
    ]
    if extra:
        items.append(_mk(A5, 0, "note", ["recovery"], "復帰テスト用の新規記録（a005）"))
    return sorted(items, key=lambda r: (r["_dt"], r["item"]["memory_id"]))


def _parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _encode_cursor(dt: datetime, mid: str) -> str:
    raw = json.dumps({"v": dt.isoformat(), "i": mid})
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cur: str):
    padded = cur + "=" * (-len(cur) % 4)
    data = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    return _parse_iso(data["v"]), data["i"]


def make_handler(items, page_size):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib naming
            parsed = urlparse(self.path)
            if parsed.path != "/v1/memories":
                self.send_error(404)
                return
            q = parse_qs(parsed.query)
            try:
                since = _parse_iso(q["since"][0])
            except (KeyError, ValueError):
                self.send_error(422, "since required")
                return
            remaining = [r for r in items if r["_dt"] >= since]
            cur = q.get("cursor", [None])[0]
            if cur:
                try:
                    cv, ci = _decode_cursor(cur)
                except Exception:
                    self.send_error(400, "invalid cursor")
                    return
                remaining = [
                    r
                    for r in remaining
                    if (r["_dt"], r["item"]["memory_id"]) > (cv, ci)
                ]
            limit = int(q.get("limit", ["100"])[0])
            size = min(limit, page_size) if page_size else limit
            page = remaining[:size]
            next_cursor = (
                _encode_cursor(page[-1]["_dt"], page[-1]["item"]["memory_id"])
                if page and len(remaining) > size
                else None
            )
            body = json.dumps(
                {"items": [r["item"] for r in page], "next_cursor": next_cursor}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):  # keep E2E output clean
            pass

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18999)
    ap.add_argument("--page-size", type=int, default=0,
                    help="cap items per page (0 = honor requested limit)")
    ap.add_argument("--extra", action="store_true",
                    help="serve the a005 recovery record too")
    args = ap.parse_args()
    items = build_items(args.extra)
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(items, args.page_size))
    print(f"fake agent-memory on :{args.port} "
          f"({len(items)} record(s), page_size={args.page_size or 'requested'})",
          flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
