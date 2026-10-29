"""Todo helpers shared by the REST API, MCP tools and the auto time judge.

Pure data access: create-with-dedup, list ordering, and the tracked-time
summary (tracked_minutes / last_tracked_at / tracking) the API and MCP both
return. The matching job itself lives in todo_judge.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from .models import Todo, TodoTimeEntry

# Character 3-gram Jaccard at/above this dedups a source='auto' todo against
# an existing open one instead of creating a near-duplicate row.
DEDUP_JACCARD = 0.6
# A todo counts as "tracking" when an entry ended within this window.
TRACKING_WINDOW_MIN = 10

TODO_LIST_STATUSES = ("open", "done", "all")

# open-list ordering: urgency high -> low, then newest first.
_URGENCY_RANK = case(
    (Todo.urgency == "high", 0),
    (Todo.urgency == "medium", 1),
    else_=2,
)


def char_ngrams(s: str, n: int = 3) -> set[str]:
    """Character n-grams; a shorter non-empty string is its own single gram."""
    s = s.strip()
    if len(s) < n:
        return {s} if s else set()
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def title_similarity(a: str, b: str) -> float:
    """3-gram Jaccard of two titles (1.0 for identical short titles)."""
    ga, gb = char_ngrams(a), char_ngrams(b)
    if not ga or not gb:
        return 1.0 if ga == gb and bool(ga) else 0.0
    return len(ga & gb) / len(ga | gb)


def find_duplicate(db: Session, title: str) -> Todo | None:
    """The open todo most similar to title, when near-identical (>= 0.6)."""
    best: Todo | None = None
    best_sim = DEDUP_JACCARD
    for t in db.scalars(select(Todo).where(Todo.status == "open")).fetchall():
        sim = title_similarity(title, t.title)
        if sim >= best_sim:
            best, best_sim = t, sim
    return best


def create_todo(
    db: Session,
    *,
    title: str,
    note: str | None = None,
    urgency: str = "medium",
    due_date=None,
    source: str = "me",
    evidence: str | None = None,
    device_id: str | None = None,
) -> tuple[Todo, bool]:
    """Create a todo, or (source='auto' + near-identical open title) return
    the existing one with deduped=True. Commits."""
    title = (title or "").strip()
    if source == "auto":
        dup = find_duplicate(db, title)
        if dup is not None:
            return dup, True
    t = Todo(
        title=title,
        note=note,
        urgency=urgency,
        due_date=due_date,
        source=source,
        evidence=evidence,
        device_id=device_id,
    )
    db.add(t)
    db.commit()
    return t, False


def query_todos(db: Session, status: str = "open", limit: int = 100) -> list[Todo]:
    """Ordered todo list: open = urgency high->low then newest; done = most
    recently done; all = newest first."""
    q = select(Todo)
    if status == "open":
        q = (
            q.where(Todo.status == "open")
            .order_by(_URGENCY_RANK.asc(), Todo.created_at.desc(), Todo.id.desc())
        )
    elif status == "done":
        q = q.where(Todo.status == "done").order_by(
            Todo.done_at.desc().nullslast(), Todo.id.desc()
        )
    else:  # all
        q = q.order_by(Todo.created_at.desc(), Todo.id.desc())
    return db.scalars(q.limit(limit)).fetchall()


def tracked_bulk(
    db: Session, todo_ids: list[uuid.UUID], now: datetime | None = None
) -> dict[uuid.UUID, dict]:
    """Tracked-time summary per todo: {tracked_minutes (1 decimal),
    last_tracked_at, tracking}. One grouped query."""
    if not todo_ids:
        return {}
    now = now or datetime.now(timezone.utc)
    rows = db.execute(
        select(
            TodoTimeEntry.todo_id,
            func.sum(
                func.extract(
                    "epoch", TodoTimeEntry.ended_at - TodoTimeEntry.started_at
                )
                / 60.0
            ),
            func.max(TodoTimeEntry.ended_at),
        )
        .where(TodoTimeEntry.todo_id.in_(todo_ids))
        .group_by(TodoTimeEntry.todo_id)
    ).fetchall()
    out: dict[uuid.UUID, dict] = {}
    track_after = now - timedelta(minutes=TRACKING_WINDOW_MIN)
    for todo_id, minutes, last_end in rows:
        out[todo_id] = {
            "tracked_minutes": round(float(minutes or 0.0), 1),
            "last_tracked_at": last_end,
            "tracking": last_end is not None and last_end >= track_after,
        }
    return out


def todo_dict(t: Todo, tracked: dict | None = None) -> dict:
    """MCP-facing shape: the same fields as the REST TodoOut (plain JSON)."""
    tracked = tracked or {}
    return {
        "id": str(t.id),
        "title": t.title,
        "note": t.note,
        "status": t.status,
        "source": t.source,
        "urgency": t.urgency,
        "due_date": t.due_date.isoformat() if t.due_date else None,
        "evidence": t.evidence,
        "device_id": t.device_id,
        "created_at": t.created_at.isoformat(),
        "done_at": t.done_at.isoformat() if t.done_at else None,
        "tracked_minutes": tracked.get("tracked_minutes", 0.0),
        "last_tracked_at": (
            tracked["last_tracked_at"].isoformat()
            if tracked.get("last_tracked_at")
            else None
        ),
        "tracking": tracked.get("tracking", False),
    }
