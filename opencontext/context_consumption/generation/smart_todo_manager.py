#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Smart Todo Manager - Hibi 向けの ToDo 生成。

直近の画面の書き起こし（screen_summary）と 30 秒ごとの活動要約（activity）、
同期サーバの当日の宣言作業（work sessions）を材料に、画面に出ている
「やらなければいけないこと」を根拠付きで最大 3 件まで拾う。同期サーバに
届かなくてもローカルの材料だけで動く（fail-soft。止まるのはこの生成だけ）。
既存の未完了 ToDo との重複除去は文字 3-gram Jaccard のまま。
"""

import datetime
import re
from typing import Any, Dict, List, Optional

from opencontext.config.global_config import get_prompt_group
from opencontext.context_consumption.generation.debug_helper import DebugHelper
from opencontext.context_consumption.generation.materials import (
    build_generation_materials,
    fetch_today_sessions,
    format_user_prompt,
    parse_todo_items,
)
from opencontext.llm.global_vlm_client import generate_with_messages
from opencontext.storage.global_storage import get_storage
from opencontext.utils.json_parser import parse_json_from_response
from opencontext.utils.logging_utils import get_logger

# Backward-compatible re-export (tests import the material builder paths).
from opencontext.context_consumption.generation.materials import build_tip_materials  # noqa: F401

logger = get_logger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")

# Two todos whose description 3-gram Jaccard is at or above this value count
# as duplicates (replaces the removed embedding-based similarity check).
TODO_DUPLICATE_JACCARD = 0.6


def trigram_jaccard(a: str, b: str) -> float:
    """Jaccard similarity of character 3-gram sets.

    Consecutive whitespace/newlines are collapsed to a single half-width
    space first. Both texts under 3 characters (empty gram sets) count as
    identical. Pure function, mirrored after live_monitor._jaccard3.
    """

    def grams(text: str) -> set:
        normalized = _WHITESPACE_RE.sub(" ", text or "")
        return {normalized[i : i + 3] for i in range(len(normalized) - 2)}

    ga, gb = grams(a), grams(b)
    if not ga and not gb:
        return 1.0
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def deduplicate_todo_texts(
    new_texts: List[str],
    existing_texts: List[str],
    threshold: float = TODO_DUPLICATE_JACCARD,
) -> List[bool]:
    """Decide for each new todo text whether it is a duplicate.

    Pure function: returns one keep-flag per ``new_texts`` entry. A text is
    dropped when it matches an existing todo or an earlier kept entry of the
    same batch at ``trigram_jaccard >= threshold``.
    """
    kept: List[str] = []
    flags: List[bool] = []
    for text in new_texts:
        normalized = (text or "").strip()
        if not normalized:
            flags.append(False)
            continue
        duplicate = any(
            trigram_jaccard(normalized, (existing or "").strip()) >= threshold
            for existing in existing_texts
            if (existing or "").strip()
        ) or any(trigram_jaccard(normalized, prev) >= threshold for prev in kept)
        flags.append(not duplicate)
        if not duplicate:
            kept.append(normalized)
    return flags


class SmartTodoManager:
    """
    Smart Todo Manager
    Picks up "things that need doing" from what is actually on the screen
    (mail / notifications asking for a reply, the user's own TODO notes,
    unresolved errors, declared work left idle), with evidence.
    """

    def generate_todo_tasks(self, start_time: int, end_time: int) -> Optional[Dict[str, Any]]:
        """
        Generate up to 3 todo tasks from the recent time window's materials.
        Returns None when there are no materials or no valid task.
        """
        try:
            # 1. Materials: window activities / screen summaries + today's sessions
            materials = self._collect_materials(start_time, end_time)
            if not materials:
                logger.info("No materials for todo generation; skipped.")
                return None

            # 2. One structured-route LLM call, then validate the JSON output
            raw = self._extract_raw_tasks(materials, start_time, end_time)
            tasks = parse_todo_items(raw)
            if not tasks:
                logger.info("No todo with valid evidence was generated.")
                return None

            # 3. Deduplicate against existing incomplete todos (unchanged mechanism)
            tasks = self._deduplicate_todos(tasks)
            if not tasks:
                return None

            # 4. Store in the SQLite todo table (fail-soft per task)
            todo_ids = []
            for task in tasks:
                deadline = self._task_deadline(task)
                todo_id = get_storage().insert_todo(
                    content=task["content"],
                    urgency=task["urgency"],
                    end_time=deadline,
                    assignee="",
                    reason=task["reason"],
                )
                todo_ids.append(todo_id)

                # Queue for upload to the sync server (fail-soft)
                try:
                    from opencontext.screen_record.sync_outbox import enqueue_outbox_event

                    enqueue_outbox_event(
                        kind="todo",
                        started_at=datetime.datetime.now(),
                        ended_at=deadline,
                        app="",
                        title=(task["content"] or "")[:100],
                        text=task["content"] or "",
                        meta={
                            "reason": task["reason"],
                            "urgency": task["urgency"],
                            "evidence_time": task["evidence_time"],
                            "evidence_title": task["evidence_title"],
                        },
                        source="minecontext-desktop",
                    )
                except Exception as e:
                    logger.warning(f"Outbox enqueue failed (non-fatal): {e}")

            return {
                "content": f"{len(todo_ids)} 件のToDoを生成しました。",
                "todo_ids": todo_ids,
                "tasks": tasks,
            }

        except Exception as e:
            logger.exception(f"Failed to generate smart Todo tasks: {e}")
            return None

    def _collect_materials(self, start_time: int, end_time: int) -> List[str]:
        """Build prompt materials; storage / sync failures degrade to []."""
        try:
            start = datetime.datetime.fromtimestamp(start_time)
            end = datetime.datetime.fromtimestamp(end_time)
            activities = get_storage().get_activities(
                start_time=start, end_time=end, limit=50
            )
            screen_summaries = get_storage().get_screen_summaries(
                start_time=start, end_time=end, limit=50
            )
            sessions = fetch_today_sessions()
            materials = build_generation_materials(activities, screen_summaries, sessions)
            logger.info(
                f"Todo materials: {len(materials)} lines "
                f"({len(sessions)} declared sessions)."
            )
            return materials
        except Exception as e:
            logger.exception(f"Failed to collect todo materials: {e}")
            return []

    def _extract_raw_tasks(
        self, materials: List[str], start_time: int, end_time: int
    ) -> Optional[Any]:
        """One LLM call on the structured route; returns the parsed JSON."""
        prompt_group = get_prompt_group("generation.todo_extraction")
        system_prompt = prompt_group.get("system", "")
        user_prompt = format_user_prompt(
            prompt_group.get("user", ""),
            current_time=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            start_time_str=datetime.datetime.fromtimestamp(start_time).strftime("%H:%M"),
            end_time_str=datetime.datetime.fromtimestamp(end_time).strftime("%H:%M"),
            materials=materials,
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = generate_with_messages(messages)

        DebugHelper.save_generation_debug(
            task_type="todo",
            messages=messages,
            response=response,
            metadata={
                "start_time": start_time,
                "end_time": end_time,
                "num_materials": len(materials),
            },
        )
        return parse_json_from_response(response)

    def _task_deadline(self, task: Dict[str, Any]) -> Optional[datetime.datetime]:
        """Explicit due date from the materials, end-of-day when no time."""
        if not task.get("due_date"):
            return None
        try:
            due_str = f"{task['due_date']} {task.get('due_time') or '23:59'}"
            return datetime.datetime.strptime(due_str, "%Y-%m-%d %H:%M")
        except ValueError:
            return None

    def _deduplicate_todos(
        self, new_tasks: List[Dict], threshold: float = TODO_DUPLICATE_JACCARD
    ) -> List[Dict]:
        """Deduplicate new todos against existing incomplete todos and the batch.

        Uses character 3-gram Jaccard similarity instead of the removed
        embedding vector search. Storage failures degrade to no dedup
        (fail-soft).
        """
        if not new_tasks:
            return []

        existing_texts: List[str] = []
        try:
            existing_todos = get_storage().get_todos(status=0, limit=200)
            existing_texts = [todo.get("content") or "" for todo in existing_todos]
        except Exception as e:
            logger.warning(f"Failed to load existing todos for dedup: {e}")

        keep_flags = deduplicate_todo_texts(
            [task.get("content", "") for task in new_tasks],
            existing_texts,
            threshold=threshold,
        )

        filtered_tasks = []
        for task, keep in zip(new_tasks, keep_flags):
            if not keep:
                logger.info(
                    f"🚫 Todo filtered (duplicate): {task.get('content', '')[:60]}"
                )
                continue
            filtered_tasks.append(task)
        return filtered_tasks
