# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the direct sync-server registration of generated todos.

Covers the urgency conversion (2/1/0 -> high/medium/low), the
POST /v1/todos payload shape, and the storage routing of
SmartTodoManager._store_tasks: a successful POST stores nothing locally,
while sync disabled / send failure falls back to the local todo table +
outbox kind='todo' event. All HTTP is mocked; no real server is touched.
"""

import datetime

import httpx
import pytest

import opencontext.context_consumption.generation.smart_todo_manager as todo_manager
import opencontext.screen_record.sync_outbox as sync_outbox
from opencontext.context_consumption.generation.smart_todo_manager import (
    SmartTodoManager,
    build_todo_api_payload,
    urgency_to_api,
)

TASK = {
    "content": "請求書の支払いを完了する",
    "urgency": 2,
    "evidence_time": "09:30",
    "evidence_title": "Gmail - 支払い提醒",
    "reason": "根拠: 09:30 「Gmail - 支払い提醒」",
    "due_date": "2026-10-15",
    "due_time": "17:00",
}

TASK_WITHOUT_DUE = {
    "content": "画面メモを整理する",
    "urgency": 0,
    "evidence_time": "10:15",
    "evidence_title": "vim memo.txt",
    "reason": "根拠: 10:15 「vim memo.txt」",
}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _ErrorResponse:
    def raise_for_status(self):
        raise RuntimeError("http 500")


class _FakeStorage:
    def __init__(self, todos=None):
        self.todos = todos or []
        self.inserted = []

    def get_todos(self, status=None, limit=100, **kwargs):
        return self.todos

    def insert_todo(self, **kwargs):
        self.inserted.append(kwargs)
        return 101


class TestUrgencyToApi:
    def test_maps_local_urgency_levels(self):
        assert urgency_to_api(2) == "high"
        assert urgency_to_api(1) == "medium"
        assert urgency_to_api(0) == "low"

    def test_unknown_values_fall_back_to_low(self):
        assert urgency_to_api(None) == "low"
        assert urgency_to_api(7) == "low"


class TestBuildTodoApiPayload:
    def test_full_payload_shape(self):
        payload = build_todo_api_payload(TASK, device_id="dev-1")
        assert payload == {
            "title": "請求書の支払いを完了する",
            "urgency": "high",
            "source": "auto",
            "evidence": "09:30 Gmail - 支払い提醒",
            "due_date": "2026-10-15",
            "device_id": "dev-1",
        }

    def test_optional_fields_omitted_when_missing(self):
        payload = build_todo_api_payload(TASK_WITHOUT_DUE)
        assert payload == {
            "title": "画面メモを整理する",
            "urgency": "low",
            "source": "auto",
            "evidence": "10:15 vim memo.txt",
        }
        assert "due_date" not in payload
        assert "device_id" not in payload


class TestStoreTasksServerSuccess:
    def _patch(self, monkeypatch, response):
        posts = []

        def fake_post(url, json=None, timeout=None):
            posts.append({"url": url, "json": json, "timeout": timeout})
            return response

        monkeypatch.setattr(httpx, "post", fake_post)
        storage = _FakeStorage()
        enqueued = []
        monkeypatch.setattr(todo_manager, "get_storage", lambda: storage)
        monkeypatch.setattr(
            sync_outbox, "enqueue_outbox_event", lambda **kwargs: enqueued.append(kwargs)
        )
        return posts, storage, enqueued

    def test_success_skips_local_save_and_outbox(self, monkeypatch):
        posts, storage, enqueued = self._patch(
            monkeypatch, _FakeResponse({"id": "uuid-1", "deduped": False})
        )
        todo_ids = SmartTodoManager()._store_tasks(
            [TASK], ("http://sync.example", "dev-1")
        )
        assert todo_ids == ["uuid-1"]
        assert posts == [
            {
                "url": "http://sync.example/v1/todos",
                "json": build_todo_api_payload(TASK, "dev-1"),
                "timeout": 10.0,
            }
        ]
        assert storage.inserted == []
        assert enqueued == []

    def test_deduped_server_answer_also_skips_local_save(self, monkeypatch):
        posts, storage, enqueued = self._patch(
            monkeypatch, _FakeResponse({"id": "uuid-2", "deduped": True})
        )
        todo_ids = SmartTodoManager()._store_tasks(
            [TASK], ("http://sync.example", "")
        )
        assert todo_ids == ["uuid-2"]
        assert storage.inserted == []
        assert enqueued == []


class TestStoreTasksFallback:
    def _patch(self, monkeypatch, post_response=None):
        if post_response is not None:
            monkeypatch.setattr(httpx, "post", lambda *a, **kw: post_response)
        else:
            monkeypatch.setattr(httpx, "post", lambda *a, **kw: _ErrorResponse())
        storage = _FakeStorage()
        enqueued = []

        def record_enqueue(**kwargs):
            enqueued.append(kwargs)
            return "event-id"

        monkeypatch.setattr(todo_manager, "get_storage", lambda: storage)
        monkeypatch.setattr(sync_outbox, "enqueue_outbox_event", record_enqueue)
        return storage, enqueued

    def test_send_failure_saves_locally_and_enqueues(self, monkeypatch):
        storage, enqueued = self._patch(monkeypatch)
        todo_ids = SmartTodoManager()._store_tasks(
            [TASK], ("http://sync.example", "dev-1")
        )
        assert todo_ids == [101]
        assert len(storage.inserted) == 1
        assert storage.inserted[0]["content"] == TASK["content"]
        assert storage.inserted[0]["urgency"] == 2
        assert storage.inserted[0]["end_time"] == datetime.datetime(2026, 10, 15, 17, 0)
        assert len(enqueued) == 1
        assert enqueued[0]["kind"] == "todo"
        assert enqueued[0]["text"] == TASK["content"]

    def test_sync_disabled_saves_locally_without_posting(self, monkeypatch):
        posts = []

        def fail_post(*args, **kwargs):
            posts.append(args)
            raise AssertionError("must not POST when sync is disabled")

        storage, enqueued = self._patch(monkeypatch, post_response=None)
        # _patch installed a failing httpx.post; replace it so an unexpected
        # POST is recorded (and asserted) instead of silently failing over
        monkeypatch.setattr(httpx, "post", fail_post)
        todo_ids = SmartTodoManager()._store_tasks([TASK_WITHOUT_DUE], None)
        assert todo_ids == [101]
        assert posts == []
        assert len(storage.inserted) == 1
        assert storage.inserted[0]["end_time"] is None
        assert len(enqueued) == 1
        assert enqueued[0]["kind"] == "todo"
