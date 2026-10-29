#!/usr/bin/env python3
"""Seed three past days of fictional CLI development through the local demo API.

Only the fixed loopback demo address is accepted. Re-running on the same day
reuses events, work sessions, ToDos, and the blog post; nothing is deleted.
All dates use Asia/Tokyo, matching the existing Web UI.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import date, datetime, time, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener
from zoneinfo import ZoneInfo

BASE = "http://127.0.0.1:18790"
ZONE = ZoneInfo("Asia/Tokyo")
MARKER = "hibi-demo-fiction-v1"
CLIENT = build_opener(ProxyHandler({}))

REPORT = """# 日報 {day}

## 1. 今日の概要

小さな OSS の CLI ツール「Pebble CLI」で、設定ファイルの使い方を README に書きました。
設定の優先順位とエラーメッセージを確認し、サンプルの実行手順も整理しました。

## 2. 作業ごとの時間

- 午前の開発と確認は 09:00〜12:00 の3時間でした。
- 午後の修正と検証は 13:00〜15:00 の2時間でした。
- ドキュメントの整理は 15:15〜17:00 の1時間45分でした。

作業時間は合計6時間45分、昼休みと休憩は合計1時間15分でした。

## 3. 自分がしたこと

ブラウザで仕様を読み、エディタで README とサンプル設定を編集しました。
ターミナルではテストとサンプルコマンドを実行しました。
11:30 からの会議では、設定の優先順位を CLI 引数、設定ファイル、既定値の順にする方針を確認しました。

## 4. AI がしたこと

Claude Code は 09:30〜10:15 に README の構成案を作りました。
Codex は 14:00〜14:45 にサンプルコマンドとテストの対応を確認しました。

## 5. 自分とAIの作業の関係

AI の構成案を読み、実際のコマンド出力と照合して README に反映しました。
テストの結果を確認してから、設定ファイルの読み込みとテストの修正の ToDo を完了にしました。

## 6. 明日に持ち越すこと

README の使用例の見直し、リリースノートの下書き、ヘルプ表示の確認が残っています。
"""

BLOG = """# 小さな CLI に設定ファイルを足した3日間

Pebble CLI は、テキストファイルを整える架空の OSS ツールです。
毎回同じ引数を渡していたので、TOML の設定ファイルを読めるようにしました。

## 設定の優先順位

最初に決めたのは、同じ項目を複数の場所で指定したときの扱いです。
CLI 引数、設定ファイル、既定値の順に値を選びます。
引数を省略した場合だけ設定ファイルの値を使うため、既存の使い方も保てます。

## テストで確かめたこと

2日目は設定ファイルがない場合と、書式に誤りがある場合を確認しました。
ファイルがないときは既定値で動き、書式が誤っているときは該当する項目をエラーに表示します。
Codex の確認結果を読みながら、エラーメッセージとテストの期待値をそろえました。

## README の使用例

3日目は Claude Code の構成案をもとに README を書きました。
短い設定例を置き、その下に CLI 引数で上書きする例を並べています。
文章だけで説明していた部分も、コマンドと出力を一緒に載せると見直しやすくなりました。

リリースノートの下書きとヘルプ表示の確認は、次の作業として残しています。
"""

# Oldest to newest: exactly yesterday and the two days before it.
PHASES = (
    {
        "topic": "設定ファイルの読み込み",
        "browser": "TOML の仕様と CLI 引数の優先順位を確認している。",
        "editor": "設定ファイルを読み込み、CLI 引数で値を上書きする処理を実装している。",
        "terminal": "設定あり・設定なしのサンプルコマンドを実行している。",
        "claude": "設定ファイルの読み込み処理と、既定値を保つ変更案を作成した。",
        "codex": "設定ファイルがない場合と空の場合の動作を確認した。",
    },
    {
        "topic": "テストとエラーメッセージの修正",
        "browser": "テストの失敗ログとエラーメッセージの仕様を読んでいる。",
        "editor": "設定の書式エラーを表示する処理とテストの期待値を修正している。",
        "terminal": "設定の優先順位と書式エラーのテストを実行している。",
        "claude": "CLI 引数・設定ファイル・既定値の優先順位を確認するテスト案を作成した。",
        "codex": "エラーメッセージとテストの期待値のずれを修正し、対象のテストを実行した。",
    },
    {
        "topic": "README とサンプルの作成",
        "browser": "設定の優先順位と README の使用例を照合している。",
        "editor": "README に TOML の設定例と CLI 引数で上書きする例を書いている。",
        "terminal": "README のサンプルコマンドと、設定のテストを実行している。",
        "claude": "README の構成案を作成し、設定ファイルと CLI 引数の使用例を整理した。",
        "codex": "README のサンプルコマンドとテストの対応を確認した。",
    },
)


def api(method: str, path: str, body=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    request = Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with CLIENT.open(request, timeout=30) as response:
        return json.load(response)


def stable_id(key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{MARKER}:{key}"))


def at(day: date, hm: str) -> str:
    return datetime.combine(day, time.fromisoformat(hm), tzinfo=ZONE).isoformat()


def event(day, key, kind, start, end, title, text, app=None, meta=None):
    return {
        "id": stable_id(f"{day}:{key}"),
        "kind": kind,
        "started_at": at(day, start),
        "ended_at": at(day, end) if end else None,
        "title": title,
        "text": text,
        "app": app,
        "source": MARKER,
        "meta": {"demo": True, "fictional": True, **(meta or {})},
    }


def seed_sessions(day, phase):
    query = urlencode({"from": at(day, "00:00"), "to": at(day + timedelta(days=1), "00:00")})
    existing = api("GET", f"/v1/sessions?{query}")["sessions"]
    for key, start, end, title in (
        ("morning", "09:00", "12:00", phase["topic"]),
        ("afternoon", "13:00", "15:00", "修正と動作の確認"),
        ("docs", "15:15", "17:00", "ドキュメントと作業メモの整理"),
    ):
        note = f"{MARKER}:{day}:{key} / Pebble CLI の架空の開発記録"
        session = next((s for s in existing if s.get("note") == note), None)
        if session is None:
            session = api("POST", "/v1/sessions", {
                "title": title, "note": note, "device_id": "demo-laptop",
                "started_at": at(day, start),
            })["session"]
        if session["status"] == "active":
            api("POST", f"/v1/sessions/{session['id']}/end", {"ended_at": at(day, end)})


def seed_day(day, phase):
    seed_sessions(day, phase)
    screen = []
    blocks = (
        ("09:00", "09:25", "Browser", "browser"),
        ("09:25", "10:45", "Code Editor", "editor"),
        ("10:45", "11:30", "Terminal", "terminal"),
        ("11:30", "12:00", "Meeting", "meeting"),
        ("13:00", "14:00", "Code Editor", "editor"),
        ("14:00", "15:00", "Terminal", "terminal"),
        ("15:15", "16:00", "Browser", "browser"),
        ("16:00", "17:00", "Code Editor", "editor"),
    )
    for start, end, app, key in blocks:
        cursor = datetime.fromisoformat(at(day, start))
        stop = datetime.fromisoformat(at(day, end))
        body = ("設定の優先順位と、次のリリースに含める変更を話し合っている。"
                if key == "meeting" else phase[key])
        while cursor < stop:
            hm = cursor.strftime("%H:%M")
            next_hm = min(cursor + timedelta(minutes=5), stop).strftime("%H:%M")
            screen.append(event(
                day, f"screen:{hm}", "activity" if key == "terminal" else "screen_summary",
                hm, next_hm, f"Pebble CLI · {phase['topic']}" if key != "meeting" else "OSS 開発ミーティング",
                body, app,
            ))
            cursor += timedelta(minutes=5)
    for key, start, end, title, reason in (
        ("lunch", "12:00", "13:00", "昼休み", "idle"),
        ("break", "15:00", "15:15", "休憩", "idle"),
    ):
        screen.append(event(day, key, "away", start, end, title, title, meta={"reason": reason}))
    result = api("POST", "/v1/events:batch", {"device_id": "demo-laptop", "events": screen})
    for agent, label, start, end, text_key in (
        ("claude-code", "Claude Code", "09:30", "10:15", "claude"),
        ("codex", "Codex", "14:00", "14:45", "codex"),
    ):
        record = event(
            day, agent, "ai_work", start, end, f"{label} · {phase['topic']}", phase[text_key], label,
            {"source_session": f"demo-{agent}-{day}", "memory_type": "result",
             "tags": ["pebble-cli", "demo"], "refs": []},
        )
        api("POST", "/v1/events:batch", {"device_id": f"agent:{agent}", "events": [record]})
    print(f"{day}: {len(screen) - 2} screen activities, 2 away, 2 AI sessions "
          f"(accepted={result['accepted']}, updated={result['updated']})")


def seed_todos(day):
    existing = api("GET", "/v1/todos?status=all&limit=500")["todos"]
    for key, title, status, urgency in (
        ("config", "設定ファイルの読み込みを実装する", "done", "high"),
        ("tests", "設定の優先順位とエラー表示のテストを直す", "done", "medium"),
        ("readme", "README の使用例を見直す", "open", "high"),
        ("release", "リリースノートの下書きを書く", "open", "medium"),
        ("help", "ヘルプ表示と既定値を確認する", "open", "low"),
    ):
        note = f"{MARKER}:todo:{key} / Pebble CLI の架空の手入力 ToDo"
        todo = next((t for t in existing if t.get("note") == note), None)
        if todo is None:
            todo = api("POST", "/v1/todos", {
                "title": title, "note": note, "source": "me", "urgency": urgency,
                "due_date": (day + timedelta(days=2)).isoformat(), "device_id": "demo-laptop",
            })
            if status == "done":
                api("PATCH", f"/v1/todos/{todo['id']}", {"status": "done"})
    print("ToDos: 5 manually added fixtures (2 done, 3 open); existing edits are preserved")


def seed_writing(day):
    # This is the existing report API's date-based UUID, so the Web UI finds it.
    report = event(day, "report", "report", "00:00", None, f"日報 {day}", REPORT.format(day=day),
                   meta={"report_type": "daily", "date": day.isoformat(), "tz": "Asia/Tokyo",
                         "model": "demo-fiction"})
    report["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"minecontext:sync-server:daily-report:{day}"))
    api("POST", "/v1/events:batch", {"device_id": "demo-laptop", "events": [report]})
    query = urlencode({"status": "all", "tag": MARKER, "from": str(day), "to": str(day)})
    posts = api("GET", f"/v1/blog/posts?{query}")["posts"]
    if not posts:
        # Published only inside the isolated loopback demo; no external publishing.
        post = api("POST", "/v1/blog/posts", {
            "title": "小さな CLI に設定ファイルを足した3日間", "markdown": BLOG,
            "date": day.isoformat(), "type": "post", "author": "me", "status": "published",
            "tags": ["pebble-cli", "OSS", MARKER],
            "excerpt": "設定の読み込み、テストの修正、README の作成を進めた架空の開発記録。",
        })
    else:
        post = posts[0]
    print(f"Daily report: {day}; blog: {BASE}/blog/#/post/{post['slug']}")


def main():
    health = api("GET", "/healthz")
    if health.get("ok") is not True or health.get("db") != "ok":
        raise RuntimeError("Local demo API is not healthy")
    yesterday = datetime.now(ZONE).date() - timedelta(days=1)
    for device_id, name, os_name in (
        ("demo-laptop", "開発ノート", "linux"),
        ("agent:claude-code", "Claude Code", "agent"),
        ("agent:codex", "Codex", "agent"),
    ):
        api("PUT", f"/v1/devices/{device_id}", {"name": name, "os": os_name})
    for offset, phase in zip((2, 1, 0), PHASES):
        seed_day(yesterday - timedelta(days=offset), phase)
    seed_todos(yesterday)
    seed_writing(yesterday)
    for offset in (2, 1, 0):
        day = yesterday - timedelta(days=offset)
        query = urlencode({"date": str(day), "tz": "Asia/Tokyo"})
        timeline = api("GET", f"/v1/timeline?{query}")
        if not timeline["items"]:
            raise RuntimeError(f"Empty timeline for {day}")
        print(f"Verified timeline {day}: {len(timeline['items'])} items")


if __name__ == "__main__":
    try:
        main()
    except (HTTPError, URLError, OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"Demo seed failed: {exc}", file=sys.stderr)
        sys.exit(1)
