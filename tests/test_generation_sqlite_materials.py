# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Pure-function tests for the SQLite-based generation pipeline.

Covers the tip/todo material builder (activity + screen_summary rows ->
LLM material strings) and the todo de-duplication that replaced the
removed embedding vector search (character 3-gram Jaccard >= 0.6).
"""

import datetime

from opencontext.context_consumption.generation.smart_tip_generator import (
    build_tip_materials,
)
from opencontext.context_consumption.generation.smart_todo_manager import (
    deduplicate_todo_texts,
    trigram_jaccard,
)

NOW = datetime.datetime(2026, 10, 13, 12, 34, 56)


class TestBuildTipMaterials:
    def test_formats_activity_row(self):
        activities = [
            {
                "id": 1,
                "title": "設計レビュー",
                "content": "API 仕様の見直しをした",
                "start_time": NOW,
                "end_time": NOW + datetime.timedelta(minutes=30),
            }
        ]
        materials = build_tip_materials(activities, [])
        assert materials == [
            "[activity] 2026-10-13 12:34-2026-10-13 13:04 設計レビュー: API 仕様の見直しをした"
        ]

    def test_formats_screen_summary_row(self):
        summaries = [
            {
                "id": 1,
                "title": "画面メモ",
                "content": "ログの修正箇所を確認",
                "app": "vim",
                "window_title": "main.py",
                "created_at": NOW,
            }
        ]
        materials = build_tip_materials([], summaries)
        assert materials == ["[screen] 2026-10-13 12:34 画面メモ (vim main.py): ログの修正箇所を確認"]

    def test_skips_empty_rows_and_keeps_order(self):
        activities = [{"id": 1, "title": "", "content": ""}, {"id": 2, "title": "作業A", "content": ""}]
        materials = build_tip_materials(activities, [])
        assert materials == ["[activity] ? 作業A"]

    def test_caps_at_max_items(self):
        rows = [{"id": i, "title": f"タスク{i}", "content": ""} for i in range(5)]
        assert len(build_tip_materials(rows, [], max_items=3)) == 3

    def test_tolerates_missing_fields(self):
        assert build_tip_materials([{}, None], [{}]) == []


class TestTrigramJaccard:
    def test_identical_texts_score_one(self):
        assert trigram_jaccard("レポートを提出する", "レポートを提出する") == 1.0

    def test_short_texts_both_empty_are_identical(self):
        assert trigram_jaccard("", "") == 1.0
        assert trigram_jaccard(" ab ", "  ab\n") == 1.0  # whitespace collapsed

    def test_one_side_empty_scores_zero(self):
        assert trigram_jaccard("abc", "") == 0.0
        assert trigram_jaccard(None, "abc") == 0.0

    def test_different_texts_score_below_threshold(self):
        assert trigram_jaccard("レポートを提出する", "会議の資料を作る") < 0.6

    def test_rephrase_scores_high(self):
        # A re-description of the same task: well above 0.6
        a = "ユーザーはさくらのレンタルサーバの管理画面でパスワードを変更していました"
        b = "ユーザーはさくらのレンタルサーバの管理画面でパスワードを変更していました。"
        assert trigram_jaccard(a, b) >= 0.6


class TestDeduplicateTodoTexts:
    def test_drops_existing_and_in_batch_duplicates(self):
        flags = deduplicate_todo_texts(
            ["レポートを提出する", "レポートを提出する", "全く別のタスク"],
            ["レポートを提出する"],
        )
        assert flags == [False, False, True]

    def test_keeps_all_when_nothing_matches(self):
        flags = deduplicate_todo_texts(["タスクA", "タスクB"], ["古いタスクZ"])
        assert flags == [True, True]

    def test_drops_empty_texts(self):
        assert deduplicate_todo_texts(["", "   "], []) == [False, False]

    def test_near_duplicate_of_existing_is_dropped(self):
        a = "ユーザーはさくらのレンタルサーバの管理画面でパスワードを変更していました"
        b = "ユーザーはさくらのレンタルサーバの管理画面でパスワードを変更していました。"
        assert deduplicate_todo_texts([b], [a]) == [False]

    def test_empty_existing_list(self):
        assert deduplicate_todo_texts(["タスクA"], []) == [True]
