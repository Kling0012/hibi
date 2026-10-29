"""Synthetic-only direct TypeSafe benchmark; never sends env or memory contents."""
import argparse
import concurrent.futures
import datetime
import json
import pathlib
import statistics
import time
import urllib.error
import urllib.request


# Expected outcomes are withheld from the remote state.
CASES = [
    ("en_alias", "en", "PostgreSQL database technology", "Postgres database technology", "same", False),
    ("en_different", "en", "Orion production API service", "Orion Archive historical dataset", "different", False),
    ("en_uncertain", "en", "Alex, an engineer", "Alex, an engineer; no other identifying details are known", "review", False),
    ("en_update", "en", "Project Cedar production database: replaced AmberDB with CobaltDB on 2026-09-01", "Project Cedar production database: AmberDB, recorded 2026-08-01", "same", True),
    ("ja_alias", "ja", "PostgreSQLというデータベース技術", "Postgresというデータベース技術", "same", False),
    ("ja_different", "ja", "オリオンの本番APIサービス", "オリオン・アーカイブの過去資料集", "different", False),
    ("ja_uncertain", "ja", "エンジニアの田中さん", "エンジニアの田中さん。他に識別情報はない", "review", False),
    ("ja_update", "ja", "Cedarプロジェクトの本番DBは2026年9月1日にAmberDBからCobaltDBへ変更された", "Cedarプロジェクトの本番DBはAmberDB。2026年8月1日の記録", "same", True),
    ("mixed_alias", "mixed", "PostgreSQLというデータベース技術", "Postgres database technology", "same", False),
    ("mixed_different", "mixed", "Orionの本番APIサービス", "Orion Archive historical dataset", "different", False),
    ("mixed_uncertain", "mixed", "エンジニアのAlexさん", "Alex, an engineer; no other identifying details are known", "review", False),
    ("mixed_update", "mixed", "Cedarの本番DBは2026年9月1日にAmberDBからCobaltDBへ変更", "Project Cedar production database: AmberDB, recorded 2026-08-01", "same", True),
]


def payload(cases):
    state = {c[0]: {"new": c[2], "candidate": c[3]} for c in cases}
    questions = {}
    for name, *_ in cases:
        path = f"`{name}.new` and `{name}.candidate`"
        questions[name + "_identity"] = {
            "type": "score",
            "instructions": f"Compare the referent of {path}. For configuration facts, identify the project/environment/setting, not its changing value. Shared personal names and occupation alone do not establish identity. Use only the supplied evidence.",
            "criteria": ["Different entities or different configuration settings.", "Possibly related, but insufficient evidence to determine identity; needs review.", "The same entity or the same configuration setting, even if its value changed."],
        }
        questions[name + "_supersedes"] = {
            "type": "noul",
            "instructions": f"Does the new fact explicitly replace the older candidate value for the same setting in {path}? Aliases and uncertain identity do not imply replacement.",
        }
    return {"model": "jev-latest", "state": state, "questions": questions}


def key_from_env(path):
    for line in pathlib.Path(path).read_text().splitlines():
        name, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep and name.strip() == "TYPESAFE_API_KEY":
            return value.strip().strip("\"'")
    raise SystemExit("TYPESAFE_API_KEY is missing (no secret values printed)")


def request(key, cases, mode, run):
    body = payload(cases)
    req = urllib.request.Request("https://api.typesafe.ai/v1/systemone", data=json.dumps(body, ensure_ascii=False).encode(), headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        # Do not log HTTP headers, credentials, or arbitrary error bodies.
        return {"mode": mode, "run": run, "status": exc.code, "elapsed_s": time.perf_counter() - start}
    except (OSError, TimeoutError) as exc:
        return {"mode": mode, "run": run, "error": type(exc).__name__, "elapsed_s": time.perf_counter() - start}
    rows = []
    for name, language, _, _, identity, supersedes in cases:
        score = result["answers"][name + "_identity"]
        noul = result["answers"][name + "_supersedes"]
        predicted = ["different", "review", "same"][min(2, max(0, int(score["score"] + 0.5)))]
        rows.append({"id": name, "language": language, "expected_identity": identity, "predicted_identity": predicted, "identity_correct": predicted == identity, "expected_supersedes": supersedes, "supersedes_correct": (noul["noul"] >= 0.5) == supersedes, "score_answer": score, "noul_answer": noul})
    return {"mode": mode, "run": run, "status": 200, "elapsed_s": time.perf_counter() - start, "model": result.get("model"), "usage": result.get("usage"), "rows": rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    key = key_from_env(args.env_file)
    if not key:
        raise SystemExit("TYPESAFE_API_KEY is empty")
    results = []
    output = pathlib.Path(args.output)
    # Exclusive creation prevents overwriting an earlier experiment.
    with output.open("x", encoding="utf-8") as stream:
        def save(result):
            results.append(result)
            stream.write(json.dumps(result, ensure_ascii=False) + "\n")
            stream.flush()
            print(json.dumps({k: v for k, v in result.items() if k != "rows"}), flush=True)
        first = request(key, CASES, "batch24", 0)
        save(first)
        if first.get("status") != 200:
            return
        for run in range(1, 3):
            save(request(key, CASES, "batch24", run))
        started = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            for result in pool.map(lambda case: request(key, [case], "pair_concurrency3", 0), CASES):
                save(result)
        stream.write(json.dumps({"pair_concurrency3_wall_s": time.perf_counter() - started}) + "\n")
    summary = {"timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(), "synthetic_only": True, "cases": len(CASES), "requests": len(results), "errors": sum(r.get("status") != 200 for r in results), "groups": {}}
    for mode in ("batch24", "pair_concurrency3"):
        group = [r for r in results if r["mode"] == mode and r.get("status") == 200]
        times = sorted(r["elapsed_s"] for r in group)
        if not times:
            summary["groups"][mode] = {"requests": 0}
            continue
        summary["groups"][mode] = {"requests": len(group), "median_s": statistics.median(times), "max_s": max(times), "input_tokens": sum((r.get("usage") or {}).get("input_tokens", 0) for r in group), "languages": {}}
        for language in ("en", "ja", "mixed"):
            rows = [row for r in group for row in r["rows"] if row["language"] == language]
            summary["groups"][mode]["languages"][language] = {"judgments": len(rows)*2, "correct": sum(row["identity_correct"] + row["supersedes_correct"] for row in rows), "false_same": [row["id"] for row in rows if row["predicted_identity"] == "same" and row["expected_identity"] != "same"]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
