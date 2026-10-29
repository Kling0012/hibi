# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Reproduce timeline item detail loss using a synthetic sync-server response."""

from copy import deepcopy

import httpx
import pytest

import opencontext.tools.retrieval_tools.sync_tools as sync_tools


@pytest.fixture
def mixed_timeline():
    return {
        "date": "2026-10-21",
        "tz": "Asia/Tokyo",
        "items": [
            {
                "type": "session",
                "t": "2026-10-21T00:00:00Z",
                "session": {
                    "id": "session-1",
                    "title": "Review synthetic timeline",
                    "status": "ended",
                    "started_at": "2026-10-21T00:00:00Z",
                    "ended_at": "2026-10-21T00:30:00Z",
                    "duration_minutes": 30.0,
                },
            },
            {
                "type": "event",
                "t": "2026-10-21T00:05:00Z",
                "event": {
                    "id": "event-1",
                    "kind": "screen_summary",
                    "title": "Read synthetic notes",
                    "text": "Reading a local sample document.",
                    "started_at": "2026-10-21T00:05:00Z",
                    "ended_at": "2026-10-21T00:10:00Z",
                },
            },
            {
                "type": "ai_work",
                "t": "2026-10-21T00:15:00Z",
                "event": {
                    "id": "ai-work-1",
                    "kind": "ai_work",
                    "title": "Inspect synthetic timeline",
                    "text": "Checked a sample timeline response.",
                    "started_at": "2026-10-21T00:15:00Z",
                    "ended_at": "2026-10-21T00:16:00Z",
                },
            },
        ],
        "session_totals": [
            {"title": "Review synthetic timeline", "minutes": 30.0},
        ],
        "total_minutes": 30.0,
    }


@pytest.fixture
def timeline_result(monkeypatch, mixed_timeline):
    monkeypatch.setattr(sync_tools, "_get_server_url", lambda: "https://sync.example")
    monkeypatch.setattr(sync_tools, "_local_tz_name", lambda: "Asia/Tokyo")
    requests = []

    def fake_get(url, *, params, timeout):
        requests.append({"url": url, "params": params, "timeout": timeout})
        return httpx.Response(
            200,
            json=deepcopy(mixed_timeline),
            request=httpx.Request("GET", url, params=params),
        )

    monkeypatch.setattr(sync_tools.httpx, "get", fake_get)
    result = sync_tools.GetDayTimelineTool().execute(date=mixed_timeline["date"])

    assert requests == [
        {
            "url": "https://sync.example/v1/timeline",
            "params": {"date": "2026-10-21", "tz": "Asia/Tokyo"},
            "timeout": 15.0,
        },
    ]
    assert "error" not in result, result
    return result


@pytest.mark.parametrize(
    ("item_type", "detail_key"),
    [("session", "session"), ("event", "event"), ("ai_work", "event")],
)
def test_mixed_timeline_preserves_item_details(
    mixed_timeline, timeline_result, item_type, detail_key
):
    expected = next(item for item in mixed_timeline["items"] if item["type"] == item_type)
    actual = next(item for item in timeline_result["items"] if item["type"] == item_type)

    assert actual.get(detail_key) == expected[detail_key], (
        f"{item_type} detail was lost: {actual!r}"
    )


def test_mixed_timeline_preserves_metadata_and_totals(mixed_timeline, timeline_result):
    assert timeline_result["date"] == mixed_timeline["date"]
    assert timeline_result["tz"] == mixed_timeline["tz"]
    assert [(item["type"], item["t"]) for item in timeline_result["items"]] == [
        (item["type"], item["t"]) for item in mixed_timeline["items"]
    ]
    assert timeline_result["session_totals"] == mixed_timeline["session_totals"]
    assert timeline_result["total_minutes"] == mixed_timeline["total_minutes"]
