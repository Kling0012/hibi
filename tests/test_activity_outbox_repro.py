# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Reproduce issue #6 with real recorder/outbox methods and isolated SQLite.

Only the provider answer, storage lookup, worker start and HTTP transport are
replaced. No application, background worker or external sync server is started.
Run: uv run --frozen python -m pytest -q -s tests/test_activity_outbox_repro.py
"""

import datetime
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from opencontext.llm import provider_router
from opencontext.screen_record import sync_outbox
from opencontext.screen_record.live_monitor import ScreenLiveMonitor, _Trigger
from opencontext.storage import global_storage
from opencontext.storage.backends.sqlite_backend import SQLiteBackend


CONTENT = "Reading a synthetic document in the same editor window."
WINDOWS = [{"app": "Sample Editor", "title": "sample.txt", "url": ""}]
BASE = datetime.datetime(2026, 10, 21, 10, 0, 0)
CONFIG = {
    "server_url": "https://sync.example.invalid",
    "device_id": "00000000-0000-4000-8000-000000000006",
    "device_name": "reproduction",
}


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    backend = SQLiteBackend()
    assert backend.initialize({"config": {"path": str(tmp_path / "repro.db")}})
    storage = SimpleNamespace(
        insert_activity=backend.insert_activity,
        get_sqlite_db_path=backend.get_db_path,
    )
    monkeypatch.setattr(global_storage, "get_storage", lambda: storage)
    monkeypatch.setattr(sync_outbox, "_db_path", backend.get_db_path)
    outbox = sync_outbox.SyncOutbox()
    monkeypatch.setattr(sync_outbox.SyncOutbox, "_instance", outbox)
    monkeypatch.setattr(outbox, "start", lambda: None)
    router = SimpleNamespace(
        has_live_provider=lambda: True,
        live_ask=AsyncMock(return_value=CONTENT),
    )
    monkeypatch.setattr(provider_router, "get_provider_router", lambda: router)
    monitor = ScreenLiveMonitor()
    remote = {}
    batches = []
    enqueue_calls = []
    real_enqueue = sync_outbox.enqueue_outbox_event

    def enqueue(**kwargs):
        result = real_enqueue(**kwargs)
        enqueue_calls.append({"requested": kwargs.get("event_id"), "returned": result})
        return result

    monkeypatch.setattr(sync_outbox, "enqueue_outbox_event", enqueue)

    def upload(url, *, json, timeout):
        assert url == f"{CONFIG['server_url']}/v1/events:batch"
        assert json["device_id"] == CONFIG["device_id"]
        events = deepcopy(json["events"])
        batches.append(events)
        for event in events:
            remote[event["id"]] = event
        return httpx.Response(200, request=httpx.Request("POST", url))

    def register(url, *, json, timeout):
        assert url == f"{CONFIG['server_url']}/v1/devices/{CONFIG['device_id']}"
        return httpx.Response(200, request=httpx.Request("PUT", url))

    monkeypatch.setattr(httpx, "post", upload)
    monkeypatch.setattr(httpx, "put", register)

    def save_interval(end_seconds, windows=WINDOWS):
        monitor._process_interval(_Trigger(
            "interval",
            interval_start=(BASE + datetime.timedelta(seconds=end_seconds - 30)).timestamp(),
            interval_end=(BASE + datetime.timedelta(seconds=end_seconds)).timestamp(),
            interval_meta=deepcopy(windows),
        ))

    def rows(table):
        assert table in {"activity", "sync_outbox"}
        return [dict(row) for row in backend.connection.execute(f"SELECT * FROM {table}")]

    try:
        yield SimpleNamespace(
            monitor=monitor, outbox=outbox, remote=remote, batches=batches,
            enqueue_calls=enqueue_calls, save=save_interval, rows=rows,
            db_path=backend.get_db_path(), upload=upload,
        )
    finally:
        backend.close()


@pytest.mark.parametrize("fail_first_enqueue", [False, True], ids=["normal", "enqueue-retry"])
def test_activity_extensions_keep_one_sync_event_across_uploads(recorder, monkeypatch,
                                                               fail_first_enqueue):
    r = recorder
    retained_ids = []
    if fail_first_enqueue:
        with monkeypatch.context() as patch:
            patch.setattr(sync_outbox, "_db_path", lambda: None)
            r.save(30)
        assert r.rows("sync_outbox") == []
        assert len(r.rows("activity")) == 1
    else:
        r.save(30)
        retained_ids.append(r.monitor._last_activity["event_id"])

    r.save(60)
    retained_ids.append(r.monitor._last_activity["event_id"])
    r.save(90)
    retained_ids.append(r.monitor._last_activity["event_id"])
    queued_before_upload = r.rows("sync_outbox")
    first_id = queued_before_upload[0]["id"]
    assert r.outbox._cycle(CONFIG) > 0
    assert r.rows("sync_outbox") == []
    r.save(120)
    retained_ids.append(r.monitor._last_activity["event_id"])
    queued_after_upload = r.rows("sync_outbox")
    assert r.outbox._cycle(CONFIG) > 0
    assert r.rows("sync_outbox") == []

    activities = r.rows("activity")
    latest_end = BASE + datetime.timedelta(seconds=120)
    assert len(activities) == 1
    assert datetime.datetime.fromisoformat(activities[0]["start_time"]) == BASE
    assert datetime.datetime.fromisoformat(activities[0]["end_time"]) == latest_end
    assert activities[0]["content"] == CONTENT
    sent = [event for batch in r.batches for event in batch]
    assert all(event["started_at"] == sync_outbox._iso(BASE) for event in sent)
    assert all(
        event["text"] == CONTENT
        and event["app"] == WINDOWS[0]["app"]
        and event["title"] == WINDOWS[0]["title"]
        and event["meta"] == {"windows": WINDOWS}
        for event in sent
    )
    observations = {
        "fail_first_enqueue": fail_first_enqueue,
        "retained_ids": retained_ids,
        "enqueue_calls": r.enqueue_calls,
        "local_rows": len(activities),
        "local_latest_end": activities[0]["end_time"],
        "queued_before_upload": len(queued_before_upload),
        "id_after_upload": queued_after_upload[0]["id"],
        "remote_rows": len(r.remote),
        "remote_end_times": [event["ended_at"] for event in r.remote.values()],
    }
    print(json.dumps(observations, indent=2))
    failures = []
    if any(event_id != first_id for event_id in retained_ids):
        failures.append(f"successful enqueue ID not retained: {retained_ids!r}")
    if len(queued_before_upload) != 1:
        failures.append(f"expected one queued event, got {len(queued_before_upload)}")
    if queued_after_upload[0]["id"] != first_id:
        failures.append("event ID changed after successful upload")
    if len(r.remote) != 1:
        failures.append(f"expected one remote event, got {len(r.remote)}")
    if r.remote[first_id]["ended_at"] != sync_outbox._iso(latest_end):
        failures.append("first remote event did not receive the latest end time")
    assert not failures, "; ".join(failures)


def test_old_upload_acknowledgement_preserves_same_id_update(recorder, monkeypatch):
    r = recorder
    event_id = "00000000-0000-4000-8000-000000000060"
    initial_end = BASE + datetime.timedelta(seconds=30)
    latest_end = BASE + datetime.timedelta(seconds=60)

    def enqueue(end):
        return r.outbox.enqueue(
            kind="activity", started_at=BASE, ended_at=end,
            app=WINDOWS[0]["app"], title=WINDOWS[0]["title"], text=CONTENT,
            meta={"windows": WINDOWS}, source="minecontext-desktop", event_id=event_id,
        )

    assert enqueue(initial_end) == event_id

    def upload_with_update(url, *, json, timeout):
        assert json["events"][0]["ended_at"] == sync_outbox._iso(initial_end)
        # Deterministically enqueue after the batch was read, before its success
        # response. The same interleaving occurs with a producer during HTTP I/O.
        assert enqueue(latest_end) == event_id
        pending = r.rows("sync_outbox")
        assert len(pending) == 1
        assert pending[0]["ended_at"] == sync_outbox._iso(latest_end)
        return r.upload(url, json=json, timeout=timeout)

    with monkeypatch.context() as patch:
        patch.setattr(httpx, "post", upload_with_update)
        assert r.outbox._cycle(CONFIG) == 1
    pending_after_ack = r.rows("sync_outbox")
    assert r.outbox._cycle(CONFIG) >= 0
    print(json.dumps({
        "pending_after_old_ack": pending_after_ack,
        "remote_end": r.remote[event_id]["ended_at"],
        "expected_end": sync_outbox._iso(latest_end),
    }, indent=2))
    assert len(pending_after_ack) == 1, "old upload acknowledgement deleted the newer queued update"
    assert pending_after_ack[0]["id"] == event_id
    assert pending_after_ack[0]["ended_at"] == sync_outbox._iso(latest_end)
    assert len(r.remote) == 1
    assert r.remote[event_id]["ended_at"] == sync_outbox._iso(latest_end)


def test_different_window_keeps_separate_activity_and_sync_records(recorder):
    r = recorder
    r.save(30)
    r.save(60, windows=[{"app": "Sample Editor", "title": "other.txt", "url": ""}])
    assert len(r.rows("activity")) == 2
    queued = r.rows("sync_outbox")
    assert len(queued) == 2
    assert len({event["id"] for event in queued}) == 2
    assert r.outbox._cycle(CONFIG) == 2
    assert len(r.remote) == 2
