#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Shared building blocks for the Hibi todo / tip generators.

- Material assembly (pure): SQLite activity / screen_summary rows and the
  sync server's declared work sessions -> plain-text prompt materials.
- Output validation (pure): the LLM's JSON -> storage-shaped todo / tip
  items, dropping entries without real evidence, over the length limits,
  or containing secret-looking strings (passwords / tokens / e-mail).
- ``fetch_today_sessions``: fail-soft read of today's declared work
  sessions from the sync server. Any failure returns [] so generation
  continues on local materials alone.
"""

import datetime
import json
import re
from typing import Any, Dict, List, Optional

from opencontext.utils.logging_utils import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Secret-looking strings (never copied into todos / tips)
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    # e-mail addresses
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    # credential assignments: "password: xxx", "API_KEY=xxx", "token：xxx"
    re.compile(
        r"(?i)(?:password|passwd|pwd|api[_-]?key|apikey|secret|token|access[_-]?key)"
        r"\s*[:=：]\s*\S+"
    ),
    re.compile(r"(?:パスワード|トークン|シークレット|認証キー)\s*(?:は|[:=：])\s*\S+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    # provider key prefixes (sk- / ghp_ / AKIA… / xox…)
    re.compile(r"\b(?:sk|ghp|gho|xox[baprs]|AKIA)[A-Za-z0-9_-]{10,}"),
    # long hex strings (hashes / raw API keys)
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
]


def contains_secret(text: str) -> bool:
    """True when the text contains a password / token / e-mail-like string."""
    if not text:
        return False
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


# ---------------------------------------------------------------------------
# Material assembly (pure)
# ---------------------------------------------------------------------------

def _row_time(row: Dict[str, Any], *keys: str) -> str:
    """First non-empty time field of a sqlite row, trimmed for display."""
    for key in keys:
        value = row.get(key)
        if value:
            return str(value)[:16]
    return "?"


def build_tip_materials(
    activities: List[Dict[str, Any]],
    screen_summaries: List[Dict[str, Any]],
    max_items: int = 100,
) -> List[str]:
    """Build LLM material strings from SQLite activity / screen_summary rows.

    Pure function (no storage access) so it can be tested directly.
    Rows are the dicts returned by ``get_activities`` / ``get_screen_summaries``;
    missing fields never raise.
    """
    materials: List[str] = []

    for activity in activities or []:
        if not activity:
            continue
        title = (activity.get("title") or "").strip()
        content = (activity.get("content") or "").strip()
        if not title and not content:
            continue
        span = "-".join(
            part
            for part in (
                _row_time(activity, "start_time"),
                "" if not activity.get("end_time") else _row_time(activity, "end_time"),
            )
            if part and part != "?"
        )
        text = f"[activity] {span or '?'} {title}".strip()
        if content and content != title:
            text += f": {content}"
        materials.append(text)

    for summary in screen_summaries or []:
        if not summary:
            continue
        title = (summary.get("title") or "").strip()
        content = (summary.get("content") or "").strip()
        if not title and not content:
            continue
        where = (summary.get("app") or "").strip()
        if summary.get("window_title"):
            where = f"{where} {summary['window_title']}".strip()
        text = f"[screen] {_row_time(summary, 'created_at')} {title}".strip()
        if where:
            text += f" ({where})"
        if content and content != title:
            text += f": {content}"
        materials.append(text)

    return materials[:max_items]


_SESSION_END_REASON_JA = {
    "idle": "放置で終了",
    "manual": "手動で終了",
    "ai_detected": "作業完了と判定されて終了",
    "superseded": "次の宣言に置き換え",
}


def _parse_iso_local(value: Any) -> Optional[datetime.datetime]:
    """Parse an ISO timestamp (timeline payloads use UTC 'Z') to local time."""
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone()


def build_session_lines(sessions: List[Dict[str, Any]]) -> List[str]:
    """Format declared work sessions (sync-server timeline) as material lines.

    Pure function. Session dicts are the ``session`` objects of the
    ``GET /v1/timeline`` items; unknown / missing fields never raise.
    """
    lines: List[str] = []
    for session in sessions or []:
        if not isinstance(session, dict):
            continue
        title = str(session.get("title") or "").strip()
        start = _parse_iso_local(session.get("started_at"))
        if not title or start is None:
            continue
        end = _parse_iso_local(session.get("ended_at"))
        span = start.strftime("%H:%M") + ("〜" + end.strftime("%H:%M") if end else "〜")
        if session.get("status") == "active":
            state = "進行中"
        elif session.get("status") == "cancelled":
            state = "キャンセル"
        else:
            state = _SESSION_END_REASON_JA.get(str(session.get("end_reason") or ""), "終了")
        minutes = session.get("duration_minutes")
        suffix = (
            f"・{round(minutes)}分"
            if isinstance(minutes, (int, float)) and minutes > 0
            else ""
        )
        lines.append(f"[session] {span} 宣言作業「{title}」（{state}{suffix}）")
    return lines


def build_generation_materials(
    activities: List[Dict[str, Any]],
    screen_summaries: List[Dict[str, Any]],
    sessions: List[Dict[str, Any]] = None,
    max_items: int = 100,
) -> List[str]:
    """Materials for todo / tip prompts: sessions first, then window rows.

    Pure function. Session lines come first (few per day, high signal) so
    they are never pushed out by the activity / screen cap.
    """
    session_lines = build_session_lines(sessions)
    rest = build_tip_materials(
        activities, screen_summaries, max_items=max(0, max_items - len(session_lines))
    )
    return (session_lines + rest)[:max_items]


# ---------------------------------------------------------------------------
# Output validation (pure)
# ---------------------------------------------------------------------------

TODO_MAX_ITEMS = 3
TODO_CONTENT_MAX_CHARS = 40
TIP_MAX_ITEMS = 2
TIP_TITLE_MAX_CHARS = 30
TIP_BODY_MAX_CHARS = 120

# LLM urgency labels -> todo table urgency (frontend: 2=高, 1=中, 0=低)
_URGENCY_MAP = {"高": 2, "中": 1, "低": 0, "high": 2, "medium": 1, "low": 0, "urgent": 2}

_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_hhmm(value: Any) -> Optional[str]:
    """Normalize a time to zero-padded HH:MM, None when not a valid clock time."""
    if value is None:
        return None
    match = _HHMM_RE.match(str(value).strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def _valid_date(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not _DATE_RE.match(text):
        return None
    try:
        datetime.datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return text


def _unwrap_item_list(raw: Any, *keys: str) -> Optional[List[Any]]:
    """Accept a bare JSON array or an object wrapping it under one of ``keys``."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in keys:
            value = raw.get(key)
            if isinstance(value, list):
                return value
    return None


def parse_todo_items(raw: Any, max_items: int = TODO_MAX_ITEMS) -> List[Dict[str, Any]]:
    """Validate the LLM's todo JSON into storage-shaped items.

    Pure function. Drops entries whose content is empty / multiline / over
    ``TODO_CONTENT_MAX_CHARS``, whose urgency is unknown, whose evidence
    (HH:MM + screen title) is missing or malformed, or that contain
    secret-looking strings. Keeps at most ``max_items``.
    """
    entries = _unwrap_item_list(raw, "todos", "tasks", "items")
    if entries is None:
        return []
    items: List[Dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        content = str(entry.get("content") or entry.get("description") or "").strip()
        urgency = _URGENCY_MAP.get(str(entry.get("urgency") or "").strip().lower())
        evidence_time = normalize_hhmm(entry.get("evidence_time"))
        evidence_title = str(entry.get("evidence_title") or "").strip()
        if not content or "\n" in content:
            continue
        if len(content) > TODO_CONTENT_MAX_CHARS:
            continue
        if urgency is None:
            continue
        if not evidence_time or not evidence_title:
            continue
        item = {
            "content": content,
            "urgency": urgency,
            "evidence_time": evidence_time,
            "evidence_title": evidence_title,
            "reason": f"根拠: {evidence_time} 「{evidence_title}」",
        }
        due_date = _valid_date(entry.get("due_date"))
        if due_date:
            item["due_date"] = due_date
            due_time = normalize_hhmm(entry.get("due_time"))
            if due_time:
                item["due_time"] = due_time
        if contains_secret(json.dumps(item, ensure_ascii=False)):
            continue
        items.append(item)
        if len(items) >= max_items:
            break
    return items


def parse_tip_items(raw: Any, max_items: int = TIP_MAX_ITEMS) -> List[Dict[str, Any]]:
    """Validate the LLM's tip JSON into storage-shaped items.

    Pure function. Same rules as ``parse_todo_items`` with the tip length
    limits (title <= 30 chars, body <= 120 chars); evidence is required.
    """
    entries = _unwrap_item_list(raw, "tips", "items")
    if entries is None:
        return []
    items: List[Dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        body = str(entry.get("body") or entry.get("content") or "").strip()
        evidence_time = normalize_hhmm(entry.get("evidence_time"))
        evidence_title = str(entry.get("evidence_title") or "").strip()
        if not title or not body or "\n" in title or "\n" in body:
            continue
        if len(title) > TIP_TITLE_MAX_CHARS or len(body) > TIP_BODY_MAX_CHARS:
            continue
        if not evidence_time or not evidence_title:
            continue
        item = {
            "title": title,
            "body": body,
            "evidence_time": evidence_time,
            "evidence_title": evidence_title,
        }
        if contains_secret(json.dumps(item, ensure_ascii=False)):
            continue
        items.append(item)
        if len(items) >= max_items:
            break
    return items


def format_tip_text(item: Dict[str, Any]) -> str:
    """One storage line per tip: title, body, then the evidence in parens."""
    return (
        f"{item['title']}：{item['body']}"
        f"（{item['evidence_time']} {item['evidence_title']}）"
    )


def format_user_prompt(
    template: str,
    current_time: str,
    start_time_str: str,
    end_time_str: str,
    materials: List[str],
) -> str:
    """Fill the shared user-prompt template (fail-soft on placeholder mismatch).

    A customized template with unexpected placeholders falls back to a
    minimal header plus the raw material lines instead of raising.
    """
    fields = {
        "current_time": current_time,
        "start_time_str": start_time_str,
        "end_time_str": end_time_str,
        "materials": "\n".join(materials) if materials else "（材料なし）",
    }
    try:
        return template.format(**fields)
    except (KeyError, IndexError, ValueError) as e:
        logger.warning(f"User prompt template mismatch, using fallback format: {e}")
        return (
            f"現在時刻: {current_time}\n"
            f"対象時間帯: {start_time_str}〜{end_time_str}\n\n"
            f"材料:\n{fields['materials']}"
        )


# ---------------------------------------------------------------------------
# Sync-server session fetch (impure, fail-soft)
# ---------------------------------------------------------------------------

def fetch_today_sessions(date: Optional[str] = None) -> List[Dict[str, Any]]:
    """Today's declared work sessions from the sync server (fail-soft).

    Any problem (unset server URL, network error, malformed payload) logs a
    warning and returns []; todo / tip generation continues on the local
    materials alone. Never raises, never stops the caller.
    """
    try:
        import httpx

        from opencontext.tools.retrieval_tools.sync_tools import (
            _get_server_url,
            _local_tz_name,
        )

        server_url = _get_server_url()
        if not server_url:
            return []
        if not date:
            date = datetime.date.today().isoformat()
        response = httpx.get(
            f"{server_url}/v1/timeline",
            params={"date": date, "tz": _local_tz_name()},
            timeout=10.0,
        )
        response.raise_for_status()
        items = response.json().get("items", [])
        sessions = [
            item.get("session")
            for item in items
            if isinstance(item, dict) and item.get("type") == "session"
        ]
        return [session for session in sessions if isinstance(session, dict)]
    except Exception as e:
        logger.warning(f"Sync timeline fetch failed (continuing without sessions): {e}")
        return []
