"""Shared helper: link events to the work session active at their time.

Used by events:batch (activity/screen_summary) and the agent-memory
importer (ai_work). "Active at time T" = not cancelled, started_at <= T <
ended_at (or no end). The latest such session wins (normally at most one
by construction); otherwise the event stays unlinked.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import WorkSession


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def session_links_for_times(
    db: Session, times: list[tuple[uuid.UUID, datetime]]
) -> dict[uuid.UUID, uuid.UUID]:
    """Map event id -> the id of the session active at that event's time."""
    if not times:
        return {}
    lo = min(_as_utc(t) for _, t in times)
    hi = max(_as_utc(t) for _, t in times)
    cands = db.scalars(
        select(WorkSession)
        .where(
            WorkSession.status != "cancelled",
            WorkSession.started_at <= hi,
            or_(WorkSession.ended_at.is_(None), WorkSession.ended_at > lo),
        )
        .order_by(WorkSession.started_at.desc())
    ).fetchall()
    links: dict[uuid.UUID, uuid.UUID] = {}
    for eid, t in times:
        t = _as_utc(t)
        for c in cands:  # desc order: first match = latest start <= t
            if c.started_at <= t and (c.ended_at is None or t < c.ended_at):
                links[eid] = c.id
                break
    return links
