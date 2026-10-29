"""Background judging of active work sessions (P2-B).

For each active work session, gather the latest activity/screen_summary
events (all devices, up to SESSION_RECENT_EVENTS) since its start and decide
whether the declared work has ended:

1. Rule first: continuous effective away time -> away end at its start.
2. No events from any device for IDLE_MINUTES -> idle end
   (ended_at = the last event's time).
3. Otherwise ask the LLM (LLM_PROVIDER):
   - "fake" (dev/E2E): deterministic - if the title's words are absent from
     the most recent events, the work has ended.
   - "openai_compat": OpenAI-compatible /chat/completions (LLM_BASE_URL,
     LLM_MODEL, key from LLM_API_KEY_FILE). Must answer with JSON
     {ongoing, ended_at_event_id, confidence, reason}.
   End only when ongoing=false and confidence >= SESSION_END_THRESHOLD;
   ended_at is the time of the last work-related event, not the judge time.

Any failure (parse, network, config) only skips judging for that session in
that cycle (fail-soft: judging alone stops, the server keeps running).
Judged ends keep confirmed=false so the user can correct them later.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from . import config
from .away import effective_away_intervals
from .db import SessionLocal
from .models import Event, WorkSession

log = logging.getLogger("sync.session_judge")

JUDGE_KINDS = ("activity", "screen_summary")
SESSION_RECENT_EVENTS = 30
# fake provider: minimum events since session start before it may say "ended",
# and how many recent events must miss the title words.
FAKE_MIN_EVENTS = 3
FAKE_RECENT_WINDOW = 3


class JudgeError(Exception):
    pass


def _title_words(title: str) -> list[str]:
    """Lowercased title tokens (whitespace/full-width space split, stripped)."""
    words = []
    for w in title.lower().replace("　", " ").split():
        w = w.strip("。、.,、:;!?()（）[]【】「」")
        if w:
            words.append(w)
    return words


def _related(ev: Event, words: list[str]) -> bool:
    hay = f"{ev.text or ''} {(ev.title or '')}".lower()
    return any(w in hay for w in words)


def fake_judge(title: str, note: str | None, events: list[Event]) -> dict:
    """Deterministic judge: ended when title words vanished from recent events."""
    words = _title_words(title)
    if not words:
        return {
            "ongoing": True,
            "ended_at_event_id": None,
            "confidence": 0.5,
            "reason": "fake: title has no usable words",
        }
    if len(events) < FAKE_MIN_EVENTS:
        return {
            "ongoing": True,
            "ended_at_event_id": None,
            "confidence": 0.5,
            "reason": f"fake: only {len(events)} event(s) since start",
        }
    recent = events[-FAKE_RECENT_WINDOW:]
    if any(_related(e, words) for e in recent):
        return {
            "ongoing": True,
            "ended_at_event_id": None,
            "confidence": 0.9,
            "reason": "fake: title words still in recent events",
        }
    related_all = [e for e in events if _related(e, words)]
    last = related_all[-1] if related_all else None
    return {
        "ongoing": False,
        "ended_at_event_id": str(last.id) if last else None,
        "confidence": 0.9,
        "reason": (
            f"fake: title words absent from the last {len(recent)} events"
            + (f"; last related event {last.id}" if last else "; no related event")
        ),
    }


def _judge_messages(title: str, note: str | None, events: list[Event],
                    away: list[tuple[datetime, datetime]] | None = None) -> list[dict]:
    lines = []
    for i, e in enumerate(events, 1):
        ts = e.started_at.astimezone(timezone.utc).strftime("%H:%M:%S")
        head = " ".join(x for x in (e.kind, e.device_id, e.app, e.title) if x)
        lines.append(f"e{i} [{ts}] {head}: {(e.text or '')[:200]}")
    for a, b in away or []:
        lines.append(f"離席 {a.astimezone(timezone.utc):%H:%M}–{b.astimezone(timezone.utc):%H:%M}")
    task = f"{title}（{note}）" if note else title
    user = (
        f"利用者が宣言した作業: {task}\n\n"
        "宣言以降の全端末の画面・活動の記録（古い順）:\n"
        + "\n".join(lines)
        + "\n\n質問: 利用者はまだこの作業を続けているか。"
        "すでに終わったと判断する場合は、この作業に関係した最後の記録の id（e1〜e"
        f"{len(events)}）を選ぶこと（判定した時刻ではなく、その記録の時刻が終了時刻になる）。\n"
        "次の JSON だけを返すこと（他の文章は書かない）:\n"
        '{"ongoing": true または false, "ended_at_event_id": "e3" または null, '
        '"confidence": 0.0〜1.0, "reason": "短い日本語の理由"}'
    )
    return [
        {
            "role": "system",
            "content": "あなたはPC作業ログの分析助手です。必ず指定された形式の JSON のみを出力します。",
        },
        {"role": "user", "content": user},
    ]


def _parse_verdict(content: str, labels: dict[str, Event]) -> dict:
    try:
        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            raise JudgeError("no JSON object in LLM reply")
        raw = json.loads(m.group(0))
        confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))
        eid = raw.get("ended_at_event_id")
        ev = labels.get(str(eid)) if eid else None
        return {
            "ongoing": bool(raw["ongoing"]),
            "ended_at_event_id": str(ev.id) if ev is not None else None,
            "confidence": confidence,
            "reason": str(raw.get("reason", ""))[:500],
        }
    except JudgeError:
        raise
    except Exception as e:  # noqa: BLE001
        raise JudgeError(f"bad LLM verdict: {e}") from e


def llm_judge(title: str, note: str | None, events: list[Event],
              away: list[tuple[datetime, datetime]] | None = None) -> dict:
    if not config.LLM_BASE_URL:
        raise JudgeError("LLM_BASE_URL is not set")
    headers = {"Content-Type": "application/json"}
    if config.LLM_API_KEY_FILE:
        try:
            with open(config.LLM_API_KEY_FILE, "r", encoding="utf-8") as f:
                key = f.read().strip()
            if key:
                headers["Authorization"] = f"Bearer {key}"
        except OSError as e:
            raise JudgeError(f"cannot read api key file: {e}") from e
    payload = {
        "model": config.LLM_MODEL,
        "messages": _judge_messages(title, note, events, away),
        "temperature": 0,
    }
    with httpx.Client(timeout=config.LLM_TIMEOUT_SEC) as client:
        resp = client.post(
            f"{config.LLM_BASE_URL}/chat/completions", json=payload, headers=headers
        )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    labels = {f"e{i}": e for i, e in enumerate(events, 1)}
    return _parse_verdict(content, labels)


def _end_session(
    db, s: WorkSession, *, ended_at: datetime, reason: str,
    confidence: float | None, evidence: list[dict],
) -> None:
    s.status = "ended"
    s.ended_at = ended_at
    s.end_reason = reason
    s.end_confidence = confidence
    s.end_evidence = evidence
    # confirmed stays False: the user can correct/confirm via PATCH.


def judge_session(db, s: WorkSession, now: datetime) -> bool:
    """Judge one active session; True if it was ended. Raises JudgeError on
    LLM/parse failures (caller skips this cycle)."""
    away = effective_away_intervals(db, s.started_at, now)
    for a, b in away:
        if (b - a).total_seconds() >= config.SESSION_AWAY_END_MINUTES * 60:
            _end_session(
                db, s, ended_at=a, reason="away", confidence=None,
                evidence=[{"reason": "continuous effective away time",
                           "started_at": a.isoformat(), "ended_at": b.isoformat()}],
            )
            s.confirmed = False
            return True
    events = db.scalars(
        select(Event)
        .where(Event.kind.in_(JUDGE_KINDS), Event.started_at >= s.started_at,
               Event.started_at <= now)
        .order_by(Event.started_at.desc(), Event.id.desc())
        .limit(SESSION_RECENT_EVENTS)
    ).fetchall()
    events.reverse()  # chronological
    last = events[-1] if events else None
    idle_delta = timedelta(minutes=config.IDLE_MINUTES)

    if last is None:
        if now - s.started_at >= idle_delta:
            _end_session(
                db, s,
                ended_at=s.started_at,
                reason="idle",
                confidence=None,
                evidence=[{
                    "event_id": None,
                    "reason": (
                        "no events from any device for "
                        f"{int((now - s.started_at).total_seconds() // 60)} min"
                        " after session start"
                    ),
                }],
            )
            return True
        return False

    if now - last.started_at >= idle_delta:
        gap_min = (now - last.started_at).total_seconds() // 60
        _end_session(
            db, s,
            ended_at=last.started_at,
            reason="idle",
            confidence=None,
            evidence=[{
                "event_id": str(last.id),
                "reason": f"no events from any device for {int(gap_min)} min",
            }],
        )
        return True

    if config.LLM_PROVIDER == "fake":
        verdict = fake_judge(s.title, s.note, events)
    elif config.LLM_PROVIDER == "openai_compat":
        verdict = llm_judge(s.title, s.note, events, away)
    else:
        raise JudgeError(f"unknown LLM_PROVIDER: {config.LLM_PROVIDER}")

    if verdict["ongoing"] or verdict["confidence"] < config.SESSION_END_THRESHOLD:
        return False

    by_id = {str(e.id): e for e in events}
    ev = by_id.get(verdict["ended_at_event_id"] or "")
    if ev is not None:
        ended_at = ev.started_at
    elif events:
        ended_at = events[-1].started_at
    else:
        ended_at = s.started_at
    ended_at = min(max(ended_at, s.started_at), now)
    _end_session(
        db, s,
        ended_at=ended_at,
        reason="ai_detected",
        confidence=verdict["confidence"],
        evidence=[{
            "event_id": verdict["ended_at_event_id"],
            "reason": verdict["reason"],
        }],
    )
    return True


def judge_once() -> int:
    """One judging cycle over all active sessions; returns ended count."""
    ended = 0
    try:
        with SessionLocal() as db:
            ids = db.scalars(
                select(WorkSession.id).where(WorkSession.status == "active")
            ).fetchall()
    except Exception as e:  # noqa: BLE001 - never crash the worker loop
        log.warning("session judge could not list active sessions: %s", e)
        return 0
    for sid in ids:
        try:
            with SessionLocal() as db:
                s = db.get(WorkSession, sid)
                if s is None or s.status != "active":
                    continue
                if judge_session(db, s, datetime.now(timezone.utc)):
                    ended += 1
                db.commit()
        except Exception as e:  # noqa: BLE001 - skip only this session/cycle
            log.warning("session judge failed for %s (skipped this cycle): %s", sid, e)
    return ended


async def session_judge_worker():
    """Background loop; one cycle per SESSION_JUDGE_INTERVAL."""
    while True:
        try:
            n = await asyncio.to_thread(judge_once)
            if n:
                log.info("session judge ended %d session(s)", n)
        except Exception as e:  # noqa: BLE001 - never let the loop die
            log.warning("session judge worker error: %s", e)
        await asyncio.sleep(config.SESSION_JUDGE_INTERVAL)
