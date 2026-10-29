"""Background ToDo time tracking (P6): match open todos to screen records.

Each cycle (TODO_JUDGE_INTERVAL seconds): fetch activity/screen_summary changes
by (ingestion/update time, id), then judge their actual work intervals in
TODO_JUDGE_MAX_WINDOW_MIN chunks against open todos (max 30). Re-evaluating
an event replaces entries backed by that event and their related evidence:

- "fake" (dev/E2E): deterministic - a todo matches when any title word
  (2+ chars, whitespace/symbol split) appears in a record's app/title/text;
  matched records become one interval at confidence 0.9.
- "openai_compat": /chat/completions (LLM_BASE_URL, LLM_MODEL, key from
  LLM_API_KEY_FILE) must answer with a JSON array
  [{"todo": n, "start": "HH:MM:SS", "end": "HH:MM:SS", "confidence", "reason"}].
  Only time actually spent working on the todo may be reported; casual
  browsing and guessing must not (empty array when nothing qualifies).

Verdicts with confidence >= TODO_MATCH_THRESHOLD become todo_time_entries
(times intersected with the window's evidence, the matching event ids attached).
The 30s minimum applies once to each original event before splitting into
windows and subtracting away time; evidence fragments may be shorter.
Overlapping or nearby automatic entries are merged within
TODO_MERGE_GAP_MIN, without bridging away time. The change cursor advances
only on success; LLM failures retry the same changes next cycle
(fail-soft: tracking alone stops, the server keeps running).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
from sqlalchemy import func, select, text, tuple_
from sqlalchemy.dialects.postgresql import array

from . import config
from .away import effective_away_intervals, overlap_minutes, subtract_intervals
from .db import SessionLocal
from .models import Event, ImporterState, Todo, TodoTimeEntry

log = logging.getLogger("sync.todo_judge")

JUDGE_KINDS = ("activity", "screen_summary")
# Material caps: open todos and window records handed to the LLM.
MAX_TODOS = 30
MAX_EVENTS = 500
# Normalize original events once; window/away fragments may be shorter.
MIN_ENTRY_SEC = 30
MAX_REASON_CHARS = 60
# Cursor row (importer_state.name) for this job.
STATE_NAME = "todo_judge"
# Keep the UUID in a companion row's name, avoiding a schema migration.
CURSOR_PREFIX = f"{STATE_NAME}:cursor:"
ZERO_ID = uuid.UUID(int=0)

# fake provider: title word split (whitespace + common symbols).
_WORD_SPLIT = re.compile(
    r"[\s　/\|,.;:!?()\[\]{}<>\"'`~@#$%^&*+=_-]+|。、，．・：；？！「」『』（）］［】"
)


class JudgeError(Exception):
    pass


def _words(title: str) -> list[str]:
    """Lowercased title tokens of 2+ chars (whitespace/symbol split)."""
    return [w for w in _WORD_SPLIT.split(title.lower()) if len(w) >= 2]


def _hay(ev: Event) -> str:
    return f"{ev.app or ''} {ev.title or ''} {ev.text or ''}".lower()


def _hms(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%H:%M:%S")


def fake_match(todos: list[Todo], events: list[Event]) -> list[dict]:
    """Deterministic matcher: each todo whose title words appear in records
    gets one interval over its matching records, at confidence 0.9."""
    out: list[dict] = []
    for n, t in enumerate(todos, 1):
        words = _words(t.title)
        if not words:
            continue
        hits = [e for e in events if any(w in _hay(e) for w in words)]
        if not hits:
            continue
        out.append(
            {
                "todo": n,
                "start": _hms(hits[0].started_at),
                "end": _hms(max(e.ended_at or e.started_at for e in hits)),
                "confidence": 0.9,
                "reason": f"fake: 題名の語が記録{len(hits)}件に現れた",
            }
        )
    return out


def _judge_messages(todos: list[Todo], events: list[Event]) -> list[dict]:
    todo_lines = [
        f"{n}. {t.title}" + (f"（{t.note}）" if t.note else "")
        for n, t in enumerate(todos, 1)
    ]
    rec_lines = []
    for e in events:
        head = " ".join(x for x in (e.device_id, e.app, e.title) if x)
        time_s = _hms(e.started_at)
        if e.ended_at:
            time_s += f"–{_hms(e.ended_at)}"
        rec_lines.append(f"[{time_s}] {head}: {(e.text or '')[:300]}")
    user = (
        "ToDoの一覧（番号と題名）:\n"
        + "\n".join(todo_lines)
        + "\n\n画面の記録（古い順。時刻・端末・アプリ・タイトル・本文）:\n"
        + "\n".join(rec_lines)
        + "\n\n質問: 記録の中で、各ToDoを片付けるための作業をしていた時間帯はどこか。\n"
        "そのToDoを片付けるための作業をしていた時間だけを返すこと。"
        "ただ見ているだけの時間や、根拠のない推測は含めないこと。"
        "該当するものがなければ空配列を返すこと。\n"
        "次の JSON 配列だけを返すこと（他の文章は書かない）:\n"
        '[{"todo": 1, "start": "HH:MM:SS", "end": "HH:MM:SS", '
        '"confidence": 0.0〜1.0, "reason": "日本語60字まで"}]'
    )
    return [
        {
            "role": "system",
            "content": "あなたはPC作業ログの分析助手です。必ず指定された形式の JSON のみを出力します。",
        },
        {"role": "user", "content": user},
    ]


def _parse_verdicts(content: str) -> list[dict]:
    try:
        m = re.search(r"\[.*\]", content, re.S)
        if not m:
            raise JudgeError("no JSON array in LLM reply")
        raw = json.loads(m.group(0))
        if not isinstance(raw, list):
            raise JudgeError("LLM reply is not a JSON array")
        out = []
        for item in raw:
            if not isinstance(item, dict) or "todo" not in item:
                continue
            out.append(
                {
                    "todo": item["todo"],
                    "start": str(item.get("start", "")),
                    "end": str(item.get("end", "")),
                    "confidence": max(
                        0.0, min(1.0, float(item.get("confidence", 0.0)))
                    ),
                    "reason": str(item.get("reason", ""))[:MAX_REASON_CHARS],
                }
            )
        return out
    except JudgeError:
        raise
    except Exception as e:  # noqa: BLE001
        raise JudgeError(f"bad LLM verdict: {e}") from e


def llm_match(todos: list[Todo], events: list[Event]) -> list[dict]:
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
        "messages": _judge_messages(todos, events),
        "temperature": 0,
    }
    with httpx.Client(timeout=config.LLM_TIMEOUT_SEC) as client:
        resp = client.post(
            f"{config.LLM_BASE_URL}/chat/completions", json=payload, headers=headers
        )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return _parse_verdicts(content)


def _resolve_hms(
    s: str, window_start: datetime, window_end: datetime
) -> datetime | None:
    """HH:MM:SS -> the occurrence inside the window (UTC), if any."""
    try:
        t = datetime.strptime(s.strip(), "%H:%M:%S").time()
    except ValueError:
        return None
    candidates = []
    for offset in (-1, 0, 1):
        day = window_start.date() + timedelta(days=offset)
        cand = datetime.combine(day, t, tzinfo=timezone.utc)
        if window_start <= cand <= window_end:
            candidates.append(cand)
    return max(candidates) if candidates else None


def _apply_verdicts(
    db,
    todos: list[Todo],
    events: list[Event],
    verdicts: list[dict],
    window_start: datetime,
    window_end: datetime,
    reusable: dict | None = None,
) -> tuple[int, int]:
    """Record verdicts within normalized evidence; returns (created, extended).
    The caller supplies window/away fragments and commits the entries."""
    evidence = []
    for e in sorted(events, key=lambda e: e.started_at):
        a = max(e.started_at, window_start)
        b = min(e.ended_at or e.started_at, window_end)
        if b <= a:
            continue
        if evidence and a <= evidence[-1][1]:
            evidence[-1] = (evidence[-1][0], max(evidence[-1][1], b))
        else:
            evidence.append((a, b))
    by_num = {n: t for n, t in enumerate(todos, 1)}
    by_num_str = {str(n): t for n, t in enumerate(todos, 1)}
    created = extended = 0
    for v in verdicts:
        if v["confidence"] < config.TODO_MATCH_THRESHOLD:
            continue  # below the threshold: not recorded at all
        t = by_num.get(v["todo"]) or by_num_str.get(str(v["todo"]))
        if t is None:
            continue
        start = _resolve_hms(v["start"], window_start, window_end)
        end = _resolve_hms(v["end"], window_start, window_end)
        if start is None or end is None:
            continue
        start = min(max(start, window_start), window_end)
        end = min(max(end, window_start), window_end)
        if end < start:
            start, end = end, start
        # Minimum duration was applied to the original events, before window
        # and away splitting. Never extend or discard their short fragments.
        supported = [(max(start, a), min(end, b)) for a, b in evidence
                     if a < end and b > start]
        away = effective_away_intervals(db, start, end)
        for part_start, part_end in subtract_intervals(supported, away):
            if part_end <= part_start:
                continue
            ev_ids = list(dict.fromkeys(
                str(e.id) for e in events
                if e.started_at < part_end
                and (e.ended_at or e.started_at) > part_start
            ))
            if not ev_ids:
                continue
            gap = timedelta(minutes=max(0, config.TODO_MERGE_GAP_MIN))
            neighbors = db.scalars(
                select(TodoTimeEntry).where(
                    TodoTimeEntry.todo_id == t.id,
                    TodoTimeEntry.source == "auto",
                    TodoTimeEntry.started_at <= part_end + gap,
                    TodoTimeEntry.ended_at >= part_start - gap,
                ).order_by(TodoTimeEntry.started_at, TodoTimeEntry.id)
            ).all()
            prev = None
            for entry in neighbors:
                merge_start = min(part_start, entry.started_at)
                merge_end = max(part_end, entry.ended_at)
                merge_away = effective_away_intervals(db, merge_start, merge_end)
                if overlap_minutes(merge_away, merge_start, merge_end) > 0:
                    continue
                part_start, part_end = merge_start, merge_end
                ev_ids = list(dict.fromkeys([*(entry.event_ids or []), *ev_ids]))
                if prev is None:
                    prev = entry
                else:
                    db.delete(entry)
            if prev is not None:
                prev.started_at = part_start
                prev.ended_at = part_end
                prev.event_ids = ev_ids
                extended += 1
            else:
                entry_ids = reusable.get(t.id, []) if reusable is not None else []
                reused_id = entry_ids.pop(0) if entry_ids else None
                db.add(TodoTimeEntry(
                    **({"id": reused_id} if reused_id is not None else {}),
                    todo_id=t.id,
                    started_at=part_start,
                    ended_at=part_end,
                    source="auto",
                    confidence=v["confidence"],
                    reason=v["reason"],
                    event_ids=ev_ids,
                ))
                if reused_id is None:
                    created += 1
                else:
                    extended += 1
    return created, extended


def _related_entries(db, todos: list[Todo], changes: list[Event]):
    """Reload the complete evidence of merged entries before replacing them."""
    event_ids = {e.id for e in changes}
    entries = {}
    pending = set(event_ids)
    while pending:
        rows = db.scalars(select(TodoTimeEntry).where(
            TodoTimeEntry.source == "auto",
            TodoTimeEntry.todo_id.in_([t.id for t in todos]),
            TodoTimeEntry.event_ids.has_any(array([str(i) for i in pending])),
        )).all()
        pending = set()
        for entry in rows:
            if entry.id in entries:
                continue
            entries[entry.id] = entry
            for value in entry.event_ids or []:
                eid = uuid.UUID(value)
                if eid not in event_ids:
                    event_ids.add(eid)
                    pending.add(eid)
    events = db.scalars(select(Event).where(
        Event.id.in_(event_ids), Event.kind.in_(JUDGE_KINDS),
    ).order_by(Event.started_at, Event.id)).all()
    return events, sorted(entries.values(), key=lambda e: (e.started_at, e.id))


def _judge_material(db, events: list[Event], now: datetime, max_win: timedelta):
    """Separate the ingestion cursor from bounded, dated work-time windows."""
    windows = {}
    for e in events:
        # Apply the existing minimum once to the original event (including
        # zero/short events), before window splitting and away subtraction.
        end = max(e.started_at + timedelta(seconds=MIN_ENTRY_SEC),
                  min(e.ended_at or e.started_at, now))
        start = e.started_at
        while start < end:
            day = start.replace(hour=0, minute=0, second=0, microsecond=0)
            index = (start - day) // max_win
            a = day + index * max_win
            b = min(a + max_win, day + timedelta(days=1))
            part_end = min(end, b)
            windows.setdefault((a, b), []).append(SimpleNamespace(
                id=e.id, device_id=e.device_id, app=e.app, title=e.title,
                text=e.text, started_at=start, ended_at=part_end,
            ))
            start = part_end
    for (a, b), records in sorted(windows.items()):
        away = effective_away_intervals(db, a, b)
        material = []
        for e in records:
            for start, end in subtract_intervals([(e.started_at, e.ended_at)], away):
                material.append(SimpleNamespace(
                    id=e.id, device_id=e.device_id, app=e.app, title=e.title,
                    text=e.text, started_at=start, ended_at=end,
                ))
        material.sort(key=lambda e: (e.started_at, e.id))
        for offset in range(0, len(material), MAX_EVENTS):
            yield a, b, material[offset:offset + MAX_EVENTS]


def todo_judge_once() -> int:
    """Apply one page of changes atomically; failures leave its cursor intact."""
    if config.LLM_PROVIDER != "fake" and not config.LLM_BASE_URL:
        return 0
    now = datetime.now(timezone.utc)
    max_win = timedelta(minutes=max(1, config.TODO_JUDGE_MAX_WINDOW_MIN))
    try:
        with SessionLocal() as db:
            # Multiple server processes must not apply the same page concurrently.
            if not db.scalar(select(func.pg_try_advisory_xact_lock(7466365))):
                return 0
            state = db.get(ImporterState, STATE_NAME)
            if state is None:
                state = ImporterState(name=STATE_NAME)
                db.add(state)
            markers = db.scalars(select(ImporterState).where(
                ImporterState.name.startswith(CURSOR_PREFIX),
            )).all()
            marker = next((m for m in markers if m.cursor_ts == state.cursor_ts), None)
            if marker is not None:
                cursor_ts = marker.cursor_ts
                cursor_id = uuid.UUID(marker.name[len(CURSOR_PREFIX):])
            elif state.cursor_ts is not None:
                # Bound the legacy work-time cursor's replay to one lookback
                # window instead of re-evaluating the entire stored history.
                cursor_ts, cursor_id = min(state.cursor_ts, now) - max_win, ZERO_ID
            else:
                cursor_ts, cursor_id = now - max_win, ZERO_ID

            todos = db.scalars(
                select(Todo).where(Todo.status == "open")
                .order_by(Todo.created_at.asc(), Todo.id.asc()).limit(MAX_TODOS)
            ).all()
            # Ingestion uses transaction timestamps (now()), not commit times.
            # Evaluate the cutoff in the changes statement's snapshot so a
            # transaction starting between statements cannot be skipped later.
            # Keep waiting for idle-in-transaction clients: they may still write.
            safe_before = text(
                "(SELECT LEAST(clock_timestamp(), COALESCE(min(xact_start), clock_timestamp())) "
                "FROM pg_stat_activity WHERE datname = current_database() "
                "AND pid <> pg_backend_pid() AND state <> 'idle' "
                "AND backend_type = 'client backend')"
            )
            changed_at = func.greatest(Event.created_at, Event.updated_at)
            changes = db.scalars(select(Event).where(
                Event.kind.in_(JUDGE_KINDS),
                tuple_(changed_at, Event.id) > tuple_(cursor_ts, cursor_id),
                changed_at < safe_before,
            ).order_by(changed_at, Event.id).limit(MAX_EVENTS)).all()
            created = 0
            if changes and todos:
                events, replaced = _related_entries(db, todos, changes)
                reusable = {}
                for entry in replaced:
                    reusable.setdefault(entry.todo_id, []).append(entry.id)
                    db.delete(entry)
                db.flush()
                for a, b, material in _judge_material(db, events, now, max_win):
                    if config.LLM_PROVIDER == "fake":
                        verdicts = fake_match(todos, material)
                    elif config.LLM_PROVIDER == "openai_compat":
                        verdicts = llm_match(todos, material)
                    else:
                        raise JudgeError(f"unknown LLM_PROVIDER: {config.LLM_PROVIDER}")
                    added, _extended = _apply_verdicts(
                        db, todos, material, verdicts, a, b, reusable
                    )
                    created += added
            if changes:
                # Consume changes while no todos are open, without judging them.
                last = changes[-1]
                cursor_ts = max(last.created_at, last.updated_at)
                cursor_id = last.id
                state.imported_count = (state.imported_count or 0) + created
            # Never jump to wall time: a capped page or an idle cycle must not
            # discard unprocessed records, including timestamp ties.
            state.cursor_ts = cursor_ts
            for old in markers:
                db.delete(old)
            db.flush()
            db.add(ImporterState(name=f"{CURSOR_PREFIX}{cursor_id}", cursor_ts=cursor_ts))
            state.last_success_at = now
            state.last_error = None
            db.commit()
            return created
    except Exception as e:  # noqa: BLE001 - skip this cycle only (fail-soft)
        log.warning("todo judge failed (changes retried next cycle): %s", e)
        return 0


async def todo_judge_worker():
    """Background loop; one cycle per TODO_JUDGE_INTERVAL."""
    while True:
        try:
            n = await asyncio.to_thread(todo_judge_once)
            if n:
                log.info("todo judge added %d time entr%s", n, "y" if n == 1 else "ies")
        except Exception as e:  # noqa: BLE001 - never let the loop die
            log.warning("todo judge worker error: %s", e)
        await asyncio.sleep(config.TODO_JUDGE_INTERVAL)
