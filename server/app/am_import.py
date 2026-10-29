"""Background import of AI work records from agent-memory (P3-A).

Reads the shared memory's time-range listing (GET {AM_BASE_URL}/v1/memories,
created_at ascending, cursor paging; the "+" in ISO times is sent
percent-encoded, httpx does this for query params) and upserts each record
as an event:

  id         = memory_id (uuid)
  kind       = 'ai_work'
  device_id  = 'agent:<source.agent>' (device auto-registered with
               name=<agent>, os='agent')
  started_at = created_at
  text       = content
  title      = short heading from type + tags (e.g. "decision · MineContext")
  source     = source.uri
  meta       = {memory_type, tags, refs, source_session, valid_from,
                supersedes, superseded_by}

When a record supersedes an older one, the older event's meta.superseded_by
is set so the timeline can hide it (include_superseded=false, the default).
Cursor progress (max created_at fetched, inclusive) is committed per page in
importer_state, so a mid-run failure only retries the un-imported remainder.
Any failure (agent-memory down, timeout) is logged and retried next cycle
(fail-soft: only this import stops, the server keeps running).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import config
from .db import SessionLocal
from .links import session_links_for_times
from .models import Device, Event, ImporterState

log = logging.getLogger("sync.am_import")

IMPORTER_NAME = "agent_memory"
PAGE_LIMIT = 200
MAX_PAGES = 1000  # safety cap per pass (200 records per page)


class ImportFailure(Exception):
    pass


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _short_title(item: dict) -> str:
    parts = [str(item.get("type") or "memory")]
    tags = item.get("tags") or []
    if tags:
        parts.append(str(tags[0]))
    return " · ".join(parts)[:80]


def _ensure_agent_device(db, device_id: str, agent: str) -> None:
    db.execute(
        pg_insert(Device)
        .values(id=device_id, name=agent, os="agent")
        .on_conflict_do_nothing(index_elements=["id"])
    )


def _get_state(db) -> ImporterState:
    st = db.get(ImporterState, IMPORTER_NAME)
    if st is None:
        st = ImporterState(name=IMPORTER_NAME)
        db.add(st)
    return st


def upsert_memory(db, item: dict) -> datetime | None:
    """Upsert one memory record as an ai_work event; returns its created_at
    (None when the record is unusable and was skipped)."""
    try:
        eid = uuid.UUID(str(item.get("memory_id")))
    except (ValueError, TypeError, AttributeError):
        log.warning("skip memory without valid uuid: %r", item.get("memory_id"))
        return None
    created = _parse_dt(item.get("created_at"))
    if created is None:
        log.warning("skip memory %s without usable created_at", eid)
        return None
    src = item.get("source") or {}
    agent = str(src.get("agent") or "unknown")
    device_id = f"agent:{agent}"
    meta = {
        "memory_type": item.get("type"),
        "tags": item.get("tags") or [],
        "refs": item.get("refs") or [],
        "source_session": src.get("session"),
        "valid_from": item.get("valid_from"),
        "supersedes": item.get("supersedes"),
        "superseded_by": item.get("superseded_by"),
    }
    _ensure_agent_device(db, device_id, agent)
    values = dict(
        id=eid,
        device_id=device_id,
        kind="ai_work",
        started_at=created,
        ended_at=None,
        app=None,
        title=_short_title(item),
        text=str(item.get("content") or ""),
        meta=meta,
        source=(str(src.get("uri")) if src.get("uri") else None),
        session_id=session_links_for_times(db, [(eid, created)]).get(eid),
    )
    stmt = pg_insert(Event).values(**values)
    update_set = {k: v for k, v in values.items() if k != "id"}
    update_set["updated_at"] = func.now()
    stmt = stmt.on_conflict_do_update(index_elements=["id"], set_=update_set)
    db.execute(stmt)
    # Mark the record this one replaces, so the timeline can hide the old one.
    if item.get("supersedes"):
        try:
            old = db.get(Event, uuid.UUID(str(item.get("supersedes"))))
        except (ValueError, TypeError):
            old = None
        if old is not None and (old.meta or {}).get("superseded_by") != str(eid):
            m = dict(old.meta or {})
            m["superseded_by"] = str(eid)
            old.meta = m
    return created


def import_once() -> dict:
    """One full import pass; returns {fetched, upserted, pages, cursor_ts}.

    Raises on agent-memory read failures (network, HTTP, parse); progress
    already committed (per page) is kept and the next run continues there.
    """
    if not config.AM_BASE_URL:
        raise ImportFailure("AM_BASE_URL is not set")
    with SessionLocal() as db:
        st = _get_state(db)
        db.commit()
        cursor_ts = st.cursor_ts
    if cursor_ts is None:  # first run: start from the lookback window
        cursor_ts = datetime.now(timezone.utc) - timedelta(
            hours=config.AM_IMPORT_LOOKBACK_HOURS
        )
    if cursor_ts.tzinfo is None:
        cursor_ts = cursor_ts.replace(tzinfo=timezone.utc)
    stats = {"fetched": 0, "upserted": 0, "pages": 0}
    cursor: str | None = None
    with httpx.Client(
        base_url=config.AM_BASE_URL, timeout=config.AM_TIMEOUT_SEC
    ) as client:
        while True:
            params = {
                "field": "created_at",
                "limit": PAGE_LIMIT,
                "since": cursor_ts.isoformat(),
            }
            if cursor:
                params["cursor"] = cursor
            resp = client.get("/v1/memories", params=params)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items") or []
            stats["pages"] += 1
            stats["fetched"] += len(items)
            if items:
                max_ts = cursor_ts
                with SessionLocal() as db:
                    for it in items:
                        ts = upsert_memory(db, it)
                        if ts is not None:
                            stats["upserted"] += 1
                            if ts > max_ts:
                                max_ts = ts
                    st = _get_state(db)
                    st.cursor_ts = max_ts
                    db.commit()
                cursor_ts = max_ts
            cursor = data.get("next_cursor")
            if not cursor:
                break
            if stats["pages"] >= MAX_PAGES:
                log.warning(
                    "agent-memory import hit the %d-page cap; continuing next cycle",
                    MAX_PAGES,
                )
                break
    with SessionLocal() as db:
        st = _get_state(db)
        st.last_success_at = datetime.now(timezone.utc)
        st.last_error = None
        st.imported_count += stats["fetched"]
        db.commit()
        final_cursor = st.cursor_ts
    stats["cursor_ts"] = final_cursor
    return stats


def _record_error(e: Exception) -> None:
    try:
        with SessionLocal() as db:
            st = _get_state(db)
            st.last_error = f"{type(e).__name__}: {e}"[:500]
            db.commit()
    except Exception:  # noqa: BLE001 - bookkeeping only
        log.warning("agent-memory import: could not record last_error")


async def am_import_worker():
    """Background loop; one pass per AM_IMPORT_INTERVAL. Fail-soft."""
    if not config.AM_IMPORT_ENABLED:
        log.info("agent-memory import disabled (AM_IMPORT_ENABLED off)")
        return
    if not config.AM_BASE_URL:
        log.info("agent-memory import idle: AM_BASE_URL is not set")
        return
    while True:
        try:
            stats = await asyncio.to_thread(import_once)
            if stats["fetched"]:
                log.info(
                    "agent-memory import: %d record(s) (%d upserted) in %d page(s)",
                    stats["fetched"],
                    stats["upserted"],
                    stats["pages"],
                )
        except Exception as e:  # noqa: BLE001 - retry next cycle
            log.warning("agent-memory import failed (will retry next cycle): %s", e)
            _record_error(e)
        await asyncio.sleep(config.AM_IMPORT_INTERVAL)
