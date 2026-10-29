"""Effective away time shared by session and ToDo accounting."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import or_, select

from .models import Event

log = logging.getLogger("sync.away")
Interval = tuple[datetime, datetime]
ACTIVE_KINDS = ("activity", "screen_summary", "note")


def _union(intervals: list[Interval]) -> list[Interval]:
    merged: list[Interval] = []
    for a, b in sorted(intervals):
        if b <= a:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def subtract_intervals(intervals: list[Interval], cuts: list[Interval]) -> list[Interval]:
    """Split intervals around a sorted, disjoint list of excluded intervals."""
    out = []
    for a, b in intervals:
        cursor = a
        for c, d in cuts:
            if d <= cursor:
                continue
            if c >= b:
                break
            if c > cursor:
                out.append((cursor, min(c, b)))
            cursor = max(cursor, d)
            if cursor >= b:
                break
        if cursor < b:
            out.append((cursor, b))
    return out


def effective_away_intervals(db, from_at: datetime, to_at: datetime) -> list[Interval]:
    """Union away intervals after removing activity on other devices.

    Query failures roll back only this calculation's savepoint. Callers keep
    working with zero away time, including within a judging transaction.
    """
    try:
        if to_at <= from_at:
            return []
        margin = timedelta(minutes=1)
        with db.begin_nested():
            away = db.execute(
                select(Event.device_id, Event.started_at, Event.ended_at).where(
                    Event.kind == "away",
                    Event.started_at < to_at,
                    Event.ended_at > from_at,
                )
            ).all()
            if not away:
                return []
            activity = db.execute(
                select(Event.device_id, Event.started_at, Event.ended_at).where(
                    Event.kind.in_(ACTIVE_KINDS),
                    Event.started_at < to_at + margin,
                    or_(Event.started_at > from_at - margin, Event.ended_at > from_at),
                )
            ).all()
        active_by_device: dict[str, list[Interval]] = {}
        for device, started, ended in activity:
            active_by_device.setdefault(device, []).append(
                (started - margin, max(started + margin, ended or started))
            )
        cuts_by_device: dict[str, list[Interval]] = {}
        effective = []
        for device, started, ended in away:
            if device not in cuts_by_device:
                cuts_by_device[device] = _union([
                    interval
                    for other, intervals in active_by_device.items() if other != device
                    for interval in intervals
                ])
            effective.extend(subtract_intervals(
                [(max(started, from_at), min(ended, to_at))], cuts_by_device[device]
            ))
        return _union(effective)
    except Exception as e:  # noqa: BLE001 - only away accounting falls back
        log.warning("away calculation failed (using zero minutes): %s", e)
        return []


def overlap_minutes(intervals: list[Interval], a: datetime, b: datetime) -> float:
    """Minutes of a union of intervals overlapping [a, b)."""
    try:
        return sum(
            max(0.0, (min(end, b) - max(start, a)).total_seconds()) / 60.0
            for start, end in _union(intervals)
        ) if b > a else 0.0
    except Exception as e:  # noqa: BLE001 - only away accounting falls back
        log.warning("away overlap failed (using zero minutes): %s", e)
        return 0.0


def session_minutes(db, session, now: datetime, *, from_at=None, to_at=None,
                    intervals: list[Interval] | None = None) -> tuple[float, float]:
    """Return (working, away) minutes, optionally clipped to a day window."""
    a = max(session.started_at, from_at) if from_at else session.started_at
    b = session.ended_at or now
    if to_at:
        b = min(b, to_at)
    elapsed = max(0.0, (b - a).total_seconds() / 60.0)
    if intervals is None:
        intervals = effective_away_intervals(db, a, b)
    away = overlap_minutes(intervals, a, b)
    return round(max(0.0, elapsed - away), 2), round(away, 2)
