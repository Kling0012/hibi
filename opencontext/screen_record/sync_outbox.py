#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Sync outbox (P1-B): local queue that uploads recorded events to the central
sync server.

Events (activity / screen_summary / report) are appended to the
``sync_outbox`` sqlite table by producers (fail-soft: an enqueue failure never
breaks the recorder). A single background worker uploads them in batches:

- ``PUT  {server}/v1/devices/{device_id}`` once per run (and whenever the
  device identity / server url changes),
- ``POST {server}/v1/events:batch`` with up to 100 events; event ids are
  per-record uuids so replays are idempotent (server upserts on id).

Upload failures back off (5s → 60s) and retry; local recording is never
blocked. ``sync.enabled`` = false disables uploads but keeps enqueueing (so
nothing is lost while the user pauses syncing).
"""

import datetime
import json
import platform
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from opencontext.utils.logging_utils import get_logger

logger = get_logger(__name__)

_BATCH_SIZE = 100
_BACKOFF_MIN = 5.0
_BACKOFF_MAX = 60.0
_IDLE_POLL = 15.0
_REQUEST_TIMEOUT = 15.0

# Desktop todos are no longer generated or uploaded.
_ALLOWED_KINDS = {"activity", "screen_summary", "note", "ai_work", "report", "away"}


def _db_path() -> Optional[str]:
    try:
        from opencontext.storage.global_storage import get_storage

        return get_storage().get_sqlite_db_path() if get_storage() else None
    except Exception:
        return None


def _sync_config() -> Dict[str, Any]:
    try:
        from opencontext.config.global_config import get_config

        return get_config("sync") or {}
    except Exception:
        return {}


def _iso(value) -> Optional[str]:
    """ISO 8601 with an explicit offset; naive datetimes are local time.

    The sync server reads offset-less timestamps as UTC, so a naive local
    time would land hours off on the timeline.
    """
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return (value if value.tzinfo else value.astimezone()).isoformat()
    text = str(value)
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return text
    return (parsed if parsed.tzinfo else parsed.astimezone()).isoformat()


class SyncOutbox:
    """Singleton outbox worker (separate short-lived sqlite connections)."""

    _instance: Optional["SyncOutbox"] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "SyncOutbox":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started = False
        # last identity successfully registered this run
        self._registered: Optional[tuple] = None
        self._device_id: Optional[str] = None
        self.last_sync_at: Optional[str] = None
        self.last_error: str = ""
        self.sent_count: int = 0

    # -- lifecycle ---------------------------------------------------------------
    def start(self):
        if self._started:
            return
        self._started = True
        self._thread = threading.Thread(
            target=self._worker, daemon=True, name="sync-outbox")
        self._thread.start()
        logger.info("[SyncOutbox] worker started")

    def wake(self):
        self._wake.set()

    # -- enqueue (producers) -------------------------------------------------------
    def enqueue(self, kind: str, started_at, ended_at, app: str, title: str,
                text: str, meta: Dict[str, Any], source: str,
                event_id: Optional[str] = None) -> Optional[str]:
        if kind not in _ALLOWED_KINDS:
            logger.warning(f"[SyncOutbox] dropping event with unsupported kind '{kind}'")
            return None
        db_path = _db_path()
        if not db_path:
            logger.warning("[SyncOutbox] no sqlite path; event not queued")
            return None
        # a caller-supplied id re-sends the same event (server upserts on id,
        # e.g. when a record is extended); otherwise generate a fresh uuid
        event_id = event_id or str(uuid.uuid4())
        try:
            connection = sqlite3.connect(db_path, timeout=30)
            try:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO sync_outbox (id, kind, started_at, ended_at, app, title, text, meta, source, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        kind,
                        _iso(started_at),
                        _iso(ended_at),
                        app or "",
                        title or "",
                        text or "",
                        json.dumps(meta, ensure_ascii=False) if meta else None,
                        source or "",
                        datetime.datetime.now().isoformat(),
                    ),
                )
                connection.commit()
            finally:
                connection.close()
        except Exception as e:
            self.last_error = str(e)
            logger.warning(f"[SyncOutbox] enqueue failed (non-fatal): {e}")
            return None
        self.wake()
        return event_id

    # -- worker ----------------------------------------------------------------------
    def _worker(self):
        backoff = _BACKOFF_MIN
        while not self._stop.is_set():
            config = _sync_config()
            if not config.get("enabled"):
                self._registered = None
                self._wake.wait(_IDLE_POLL)
                self._wake.clear()
                continue
            sent = 0
            try:
                sent = self._cycle(config)
                backoff = _BACKOFF_MIN
            except Exception as e:
                self.last_error = str(e)
                backoff = min(backoff * 2, _BACKOFF_MAX)
                logger.warning(f"[SyncOutbox] upload cycle failed: {e}")
            # a full batch likely means more pending -> loop immediately
            if sent < _BATCH_SIZE:
                self._wake.wait(backoff if self.last_error else _IDLE_POLL)
                self._wake.clear()

    def _cycle(self, config: Dict[str, Any]) -> int:
        import httpx

        self.last_error = ""
        server_url = str(config.get("server_url") or "").rstrip("/")
        if not server_url:
            self.last_error = "sync.server_url not configured"
            return 0
        device_id = self._ensure_device_id(config)
        device_name = str(config.get("device_name") or platform.node() or "unknown")
        identity = (server_url, device_id, device_name)
        if identity != self._registered:
            response = httpx.put(
                f"{server_url}/v1/devices/{device_id}",
                json={"name": device_name, "os": f"{platform.system()} {platform.release()}"},
                timeout=_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            self._registered = identity
            logger.info(f"[SyncOutbox] device registered: {device_id} ({device_name})")

        db_path = _db_path()
        if not db_path:
            self.last_error = "no sqlite path"
            return 0
        connection = sqlite3.connect(db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            # Leave legacy queued todos in place, but never upload them.
            rows = connection.execute(
                """
                SELECT id, kind, started_at, ended_at, app, title, text, meta, source, created_at
                FROM sync_outbox WHERE kind != 'todo' ORDER BY created_at LIMIT ?
                """,
                (_BATCH_SIZE,),
            ).fetchall()
            if not rows:
                return 0
            events = []
            for row in rows:
                meta = row["meta"]
                if meta:
                    try:
                        meta = json.loads(meta)
                    except Exception:
                        meta = {"raw": meta}
                events.append(
                    {
                        "id": row["id"],
                        "kind": row["kind"],
                        # rows queued before the offset fix are naive local times
                        "started_at": _iso(row["started_at"]),
                        "ended_at": _iso(row["ended_at"]),
                        "app": row["app"],
                        "title": row["title"],
                        "text": row["text"],
                        "meta": meta,
                        "source": row["source"],
                    }
                )
            response = httpx.post(
                f"{server_url}/v1/events:batch",
                json={"device_id": device_id, "events": events},
                timeout=_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            # A producer may replace the same id during HTTP I/O. Acknowledge
            # only the snapshot we sent, leaving newer updates queued for retry.
            connection.executemany(
                """
                DELETE FROM sync_outbox
                WHERE id = ? AND kind IS ? AND started_at IS ? AND ended_at IS ?
                  AND app IS ? AND title IS ? AND text IS ? AND meta IS ?
                  AND source IS ? AND created_at IS ?
                """,
                [tuple(row) for row in rows],
            )
            connection.commit()
            self.sent_count += len(events)
            self.last_sync_at = datetime.datetime.now().isoformat()
            logger.info(f"[SyncOutbox] uploaded {len(events)} events")
            return len(events)
        finally:
            connection.close()

    def _ensure_device_id(self, config: Dict[str, Any]) -> str:
        device_id = str(config.get("device_id") or "").strip()
        if device_id:
            self._device_id = device_id
            return device_id
        if self._device_id:
            return self._device_id
        # generate once and persist into user settings (fail-soft: if saving
        # fails we keep using the generated id for this run)
        device_id = str(uuid.uuid4())
        self._device_id = device_id
        try:
            from opencontext.config.global_config import get_global_config

            manager = get_global_config().get_config_manager()
            if manager:
                merged = dict(_sync_config())
                merged["device_id"] = device_id
                if manager.save_user_settings({"sync": merged}):
                    logger.info(f"[SyncOutbox] generated sync device_id {device_id}")
        except Exception as e:
            logger.warning(f"[SyncOutbox] could not persist device_id: {e}")
        return device_id

    # -- status (settings UI) ---------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        config = _sync_config()
        pending = None
        db_path = _db_path()
        if db_path:
            try:
                connection = sqlite3.connect(db_path, timeout=5)
                try:
                    pending = connection.execute(
                        "SELECT COUNT(*) FROM sync_outbox WHERE kind != 'todo'").fetchone()[0]
                finally:
                    connection.close()
            except Exception:
                pending = None
        return {
            "enabled": bool(config.get("enabled")),
            "server_url": config.get("server_url", ""),
            "device_id": config.get("device_id", ""),
            "device_name": config.get("device_name") or platform.node() or "",
            "pending_count": pending,
            "last_sync_at": self.last_sync_at,
            "last_error": self.last_error,
            "sent_count": self.sent_count,
        }


def enqueue_outbox_event(kind: str, started_at, ended_at, app: str, title: str,
                         text: str, meta: Dict[str, Any], source: str,
                         event_id: Optional[str] = None) -> Optional[str]:
    """Module-level entry point for producers (starts the worker lazily)."""
    outbox = SyncOutbox.get_instance()
    outbox.start()
    return outbox.enqueue(
        kind=kind, started_at=started_at, ended_at=ended_at, app=app,
        title=title, text=text, meta=meta, source=source, event_id=event_id,
    )


def get_sync_outbox() -> SyncOutbox:
    return SyncOutbox.get_instance()
