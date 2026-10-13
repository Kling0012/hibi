# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Pure-function tests for the Hibi todo / tip generation pipeline.

Covers the shared material builder (SQLite rows + sync-server sessions ->
prompt materials), secret detection, and the LLM-output validators (JSON ->
todo / tip items with evidence, length limits and secret filtering).
"""

import datetime
import os
import time

import pytest

from opencontext.context_consumption.generation.materials import (
    build_generation_materials,
    build_session_lines,
    build_tip_materials,
    contains_secret,
    format_tip_text,
    format_user_prompt,
    normalize_hhmm,
    parse_tip_items,
    parse_todo_items,
)

NOW = datetime.datetime(2026, 10, 13, 12, 34, 56)


@pytest.fixture
def tokyo_tz():
    """Pin the process timezone so UTC->local assertions are deterministic."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Tokyo"
    time.tzset()
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


class TestContainsSecret:
    def test_detects_email(self):
        assert contains_secret("foo@example.com に返信する")

    def test_detects_credential_assignments(self):
        assert contains_secret("password: hunter2")
        assert contains_secret("API_KEY=abc123def")
        assert contains_secret("パスワード：xyz123")

    def test_detects_tokens(self):
        assert contains_secret("Authorization: Bearer AbCdEf12345678")
        assert contains_secret("sk-abcdefghijklmnop")
        assert contains_secret("a1b2c3d4" * 4)  # 32 hex chars

    def test_clean_text_passes(self):
        assert not contains_secret("画面に残ったTODOを整理する")
        assert not contains_secret("token の説明を書く")  # no value assignment
        assert not contains_secret("")
        assert not contains_secret(None)


class TestBuildSessionLines:
    def test_ended_idle_session(self):
        lines = build_session_lines(
            [
                {
                    "title": "P4-A 動作確認",
                    "started_at": "2026-10-12T22:47:03",
                    "ended_at": "2026-10-12T22:47:03",
                    "status": "ended",
                    "end_reason": "idle",
                    "duration_minutes": 0.0,
                }
            ]
        )
        assert lines == ["[session] 22:47〜22:47 宣言作業「P4-A 動作確認」（放置で終了）"]

    def test_active_session_and_duration(self):
        lines = build_session_lines(
            [
                {
                    "title": "ToDo 生成の改修",
                    "started_at": "2026-10-13T13:47:00",
                    "status": "active",
                },
                {
                    "title": "作業A",
                    "started_at": "2026-10-13T10:00:00",
                    "ended_at": "2026-10-13T10:35:00",
                    "status": "ended",
                    "end_reason": "manual",
                    "duration_minutes": 35,
                },
            ]
        )
        assert lines == [
            "[session] 13:47〜 宣言作業「ToDo 生成の改修」（進行中）",
            "[session] 10:00〜10:35 宣言作業「作業A」（手動で終了・35分）",
        ]

    def test_skips_invalid_rows(self):
        assert build_session_lines([{}, None, {"title": "", "started_at": "x"}, 5]) == []

    def test_unknown_reason_and_cancelled(self):
        lines = build_session_lines(
            [
                {
                    "title": "A",
                    "started_at": "2026-10-13T09:00:00",
                    "ended_at": "2026-10-13T09:10:00",
                    "status": "ended",
                },
                {
                    "title": "B",
                    "started_at": "2026-10-13T09:20:00",
                    "status": "cancelled",
                },
            ]
        )
        assert lines == [
            "[session] 09:00〜09:10 宣言作業「A」（終了）",
            "[session] 09:20〜 宣言作業「B」（キャンセル）",
        ]

    def test_converts_utc_timeline_timestamps(self, tokyo_tz):
        lines = build_session_lines(
            [{"title": "夜の作業", "started_at": "2026-10-12T13:47:03Z", "status": "active"}]
        )
        assert lines == ["[session] 22:47〜 宣言作業「夜の作業」（進行中）"]


class TestBuildGenerationMaterials:
    def test_sessions_come_first_and_survive_the_cap(self):
        activities = [
            {"id": i, "title": f"活動{i}", "content": "", "start_time": NOW}
            for i in range(5)
        ]
        sessions = [
            {
                "title": "宣言作業",
                "started_at": "2026-10-13T09:00:00",
                "status": "active",
            }
        ]
        materials = build_generation_materials(activities, [], sessions, max_items=3)
        assert len(materials) == 3
        assert materials[0] == "[session] 09:00〜 宣言作業「宣言作業」（進行中）"
        assert materials[1].startswith("[activity]")

    def test_without_sessions_matches_build_tip_materials(self):
        activities = [{"id": 1, "title": "作業A", "content": "", "start_time": NOW}]
        assert build_generation_materials(activities, []) == build_tip_materials(
            activities, []
        )

    def test_none_sessions_tolerated(self):
        assert build_generation_materials([], [], None) == []


class TestNormalizeHhmm:
    def test_normalizes(self):
        assert normalize_hhmm("09:05") == "09:05"
        assert normalize_hhmm("9:30") == "09:30"
        assert normalize_hhmm(" 23:59 ") == "23:59"

    def test_rejects_invalid(self):
        assert normalize_hhmm("24:00") is None
        assert normalize_hhmm("12:60") is None
        assert normalize_hhmm("9:5") is None
        assert normalize_hhmm("abc") is None
        assert normalize_hhmm(None) is None


class TestParseTodoItems:
    def test_valid_item_with_due(self):
        items = parse_todo_items(
            [
                {
                    "content": "請求書の支払いを完了する",
                    "urgency": "高",
                    "evidence_time": "9:30",
                    "evidence_title": "Gmail - 支払い提醒",
                    "due_date": "2026-10-15",
                    "due_time": "17:00",
                }
            ]
        )
        assert items == [
            {
                "content": "請求書の支払いを完了する",
                "urgency": 2,
                "evidence_time": "09:30",
                "evidence_title": "Gmail - 支払い提醒",
                "reason": "根拠: 09:30 「Gmail - 支払い提醒」",
                "due_date": "2026-10-15",
                "due_time": "17:00",
            }
        ]

    def test_drops_items_without_valid_evidence(self):
        raw = [
            {"content": "A", "urgency": "高", "evidence_time": "10:00"},  # no title
            {"content": "B", "urgency": "高", "evidence_title": "x"},  # no time
            {"content": "C", "urgency": "高", "evidence_time": "25:00", "evidence_title": "x"},
            {"content": "D", "urgency": "高"},  # no evidence at all
        ]
        assert parse_todo_items(raw) == []

    def test_drops_over_length_and_multiline_content(self):
        assert parse_todo_items(
            [{"content": "あ" * 40, "urgency": "低", "evidence_time": "10:00", "evidence_title": "x"}]
        )
        assert parse_todo_items(
            [{"content": "あ" * 41, "urgency": "低", "evidence_time": "10:00", "evidence_title": "x"}]
        ) == []
        assert parse_todo_items(
            [{"content": "一行\n二行", "urgency": "低", "evidence_time": "10:00", "evidence_title": "x"}]
        ) == []

    def test_urgency_mapping(self):
        for urgency, expected in [("高", 2), ("中", 1), ("低", 0), ("urgent", 2), ("HIGH", 2)]:
            items = parse_todo_items(
                [{"content": "x", "urgency": urgency, "evidence_time": "10:00", "evidence_title": "t"}]
            )
            assert items and items[0]["urgency"] == expected
        assert (
            parse_todo_items(
                [{"content": "x", "urgency": "??", "evidence_time": "10:00", "evidence_title": "t"}]
            )
            == []
        )

    def test_drops_secret_items(self):
        raw = [
            {"content": "foo@example.com に返信", "urgency": "高", "evidence_time": "10:00", "evidence_title": "t"},
            {"content": "OK なタスク", "urgency": "高", "evidence_time": "10:00", "evidence_title": "token: abc123"},
        ]
        assert parse_todo_items(raw) == []

    def test_caps_at_three(self):
        raw = [
            {"content": f"タスク{i}", "urgency": "低", "evidence_time": "10:00", "evidence_title": "t"}
            for i in range(5)
        ]
        assert len(parse_todo_items(raw)) == 3

    def test_accepts_wrapped_array_and_rejects_garbage(self):
        raw = {"todos": [{"content": "x", "urgency": "低", "evidence_time": "10:00", "evidence_title": "t"}]}
        assert len(parse_todo_items(raw)) == 1
        assert parse_todo_items("not json") == []
        assert parse_todo_items({"todos": "x"}) == []

    def test_invalid_due_is_omitted(self):
        items = parse_todo_items(
            [{"content": "x", "urgency": "低", "evidence_time": "10:00", "evidence_title": "t", "due_date": "2026-13-40"}]
        )
        assert items and "due_date" not in items[0]


class TestParseTipItems:
    VALID = {
        "title": "同じエラーを繰り返し見ている",
        "body": "エラーメッセージの番号で直接検索すると原因が出やすい。",
        "evidence_time": "14:02",
        "evidence_title": "vim server.py",
    }

    def test_valid_item(self):
        items = parse_tip_items([self.VALID])
        assert items == [
            {
                "title": "同じエラーを繰り返し見ている",
                "body": "エラーメッセージの番号で直接検索すると原因が出やすい。",
                "evidence_time": "14:02",
                "evidence_title": "vim server.py",
            }
        ]

    def test_length_limits(self):
        ok = dict(self.VALID, title="あ" * 30, body="い" * 120)
        assert len(parse_tip_items([ok])) == 1
        for over in ({"title": "あ" * 31, "body": "い" * 120}, {"title": "あ" * 30, "body": "い" * 121}):
            assert parse_tip_items([dict(self.VALID, **over)]) == []

    def test_drops_missing_evidence_and_secrets(self):
        assert parse_tip_items([dict(self.VALID, evidence_time=None)]) == []
        assert parse_tip_items([dict(self.VALID, evidence_title="")]) == []
        assert parse_tip_items([dict(self.VALID, body="password: abcd1234 でログイン")]) == []

    def test_caps_at_two_and_wrapped_array(self):
        raw = [self.VALID] * 4
        assert len(parse_tip_items(raw)) == 2
        assert len(parse_tip_items({"tips": [self.VALID]})) == 1
        assert parse_tip_items([]) == []


class TestFormatTipText:
    def test_format(self):
        assert (
            format_tip_text(
                {
                    "title": "休憩の提案",
                    "body": "3 時間連続で作業している。10 分離れて画面から離れると捗る。",
                    "evidence_time": "15:00",
                    "evidence_title": "VSCode main.py",
                }
            )
            == "休憩の提案：3 時間連続で作業している。10 分離れて画面から離れると捗る。（15:00 VSCode main.py）"
        )


class TestFormatUserPrompt:
    def test_fills_placeholders(self):
        prompt = format_user_prompt(
            "時刻: {current_time} / {start_time_str}〜{end_time_str}\n{materials}",
            current_time="2026-10-13 15:00",
            start_time_str="14:00",
            end_time_str="15:00",
            materials=["[screen] a", "[activity] b"],
        )
        assert prompt == "時刻: 2026-10-13 15:00 / 14:00〜15:00\n[screen] a\n[activity] b"

    def test_falls_back_on_unknown_placeholder(self):
        prompt = format_user_prompt(
            "古い雛形 {context_data}",
            current_time="2026-10-13 15:00",
            start_time_str="14:00",
            end_time_str="15:00",
            materials=["[screen] a"],
        )
        assert "2026-10-13 15:00" in prompt
        assert "[screen] a" in prompt
