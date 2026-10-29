"""REST-only integration canary; retains all synthetic records and measurements."""
import os
import argparse
import json
import time
import urllib.request
from pathlib import Path

MEMORY_USER = os.environ.get("MEMORY_USER", "default")


def call(base, path, payload=None):
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    request = urllib.request.Request(base + path, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def wait_ready(base, record, stream):
    start = time.monotonic()
    while time.monotonic() - start < 600:
        details = call(base, f"/v1/memories/{record['memory_id']}/enrichment")
        if details["status"] in ("ready", "uncertain", "failed"):
            stream.write(json.dumps({"memory_id": record["memory_id"], "enrichment": details})+"\n")
            stream.flush()
            print(json.dumps(details), flush=True)
            if details["status"] != "ready":
                raise RuntimeError("enrichment did not reach ready")
            return
        time.sleep(2)
    raise TimeoutError("enrichment deadline exceeded")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", required=True)
    p.add_argument("--run", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    project = "async-eval-" + args.run
    base = args.base_url
    payload = {"type": "project_configuration", "content": "検証用Cedarプロジェクトの本番データベースはAmberDBを使用する。", "scope": {"user": MEMORY_USER, "project": project}, "source": {"agent": "codex", "session": project}, "valid_from": "2026-08-01T00:00:00Z", "idempotency_key": project + "-old"}
    with Path(args.output).open("x") as stream:
        start = time.perf_counter()
        old = call(base, "/v1/memories", payload)
        accepted = time.perf_counter()-start
        repeated = call(base, "/v1/memories", payload)
        assert old["memory_id"] == repeated["memory_id"]
        stream.write(json.dumps({"old_id": old["memory_id"], "accept_seconds": accepted, "idempotency": True})+"\n")
        stream.flush()
        print("accept_seconds=" + str(accepted), flush=True)
        wait_ready(base, old, stream)
        payload.update(content="検証用Cedarプロジェクトは2026年9月1日に本番データベースをAmberDBからCobaltDBへ変更した。", valid_from="2026-09-01T00:00:00Z", idempotency_key=project + "-new")
        new = call(base, f"/v1/memories/{old['memory_id']}/supersede", payload)
        wait_ready(base, new, stream)
        historical = call(base, f"/v1/memories/{old['memory_id']}")
        assert historical["valid_to"].startswith("2026-09-01")
        query = {"query": "Cedar 本番データベース", "user": MEMORY_USER, "project": project, "agent": "codex", "limit": 10}
        current = call(base, "/v1/memories/search", query)["results"]
        ids = [r["memory"]["memory_id"] for r in current]
        assert new["memory_id"] in ids and old["memory_id"] not in ids, ids
        query["include_history"] = True
        history = call(base, "/v1/memories/search", query)["results"]
        assert old["memory_id"] in [r["memory"]["memory_id"] for r in history]
        query["project"] = project + "-other"
        isolated = call(base, "/v1/memories/search", query)["results"]
        assert not any(r["memory"]["memory_id"] in (str(old["memory_id"]),str(new["memory_id"])) for r in isolated)
        stream.write(json.dumps({"search_current": True, "history": True, "scope_isolation": True, "new_id": new["memory_id"]})+"\n")
        print("PASS: idempotency, current/history search, temporal boundary, scope isolation", flush=True)


if __name__ == "__main__":
    main()
