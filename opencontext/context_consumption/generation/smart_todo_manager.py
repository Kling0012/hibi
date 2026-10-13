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
既存の未完了 ToDo との重複除去は文字 3-gram Jaccard のまま（同期サーバの
open な ToDo が取れればその題名も材料に加える）。

検証を通った ToDo は、sync が有効で server_url が設定されていれば
POST /v1/todos（source='auto'）で同期サーバに直接登録する（サーバ側でも
同じ 3-gram Jaccard で重複判定し、似た open の ToDo があれば既存を返す）。
送信できたらローカルの todo テーブルへの保存と、同期アウトボックスへの
kind='todo' イベントの enqueue は行わない（二重登録の防止）。sync が無効・
URL 未設定・送信失敗のときは今までどおりローカル保存＋アウトボックスへ
enqueue する（サーバは kind='todo' イベントからも ToDo を作る）。
"""

import datetime
import re
from typing import Any, Dict, List, Optional, Tuple

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

# Timeout for the direct sync-server todo calls (GET open list / POST).
_TODO_SYNC_TIMEOUT = 10.0

# Local todo-table urgency (2/1/0) -> sync-server /v1/todos urgency label.
_URGENCY_API_MAP = {2: "high", 1: "medium", 0: "low"}


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


# ---------------------------------------------------------------------------
# Direct sync-server registration (source='auto')
# ---------------------------------------------------------------------------

def urgency_to_api(urgency: Any) -> str:
    """Local todo urgency (2=高/1=中/0=低) -> server label. Unknown -> 'low'."""
    return _URGENCY_API_MAP.get(urgency, "low")


def build_todo_api_payload(task: Dict[str, Any], device_id: str = "") -> Dict[str, Any]:
    """A validated task dict -> POST /v1/todos body (source='auto').

    Pure function. Evidence is the「HH:MM 画面タイトル」line; due_date and
    device_id are included only when present.
    """
    payload: Dict[str, Any] = {
        "title": (task.get("content") or "").strip(),
        "urgency": urgency_to_api(task.get("urgency")),
        "source": "auto",
        "evidence": f"{task.get('evidence_time') or ''} {task.get('evidence_title') or ''}".strip(),
    }
    due_date = str(task.get("due_date") or "").strip()
    if due_date:
        payload["due_date"] = due_date
    if device_id:
        payload["device_id"] = device_id
    return payload


def get_sync_todo_target() -> Optional[Tuple[str, str]]:
    """(server_url, device_id) when sync is enabled with a server URL, else None.

    Reads the merged sync config (user_setting overrides included). Any
    failure is fail-soft and means "no direct registration".
    """
    try:
        from opencontext.config.global_config import get_config

        config = get_config("sync") or {}
        if not config.get("enabled"):
            return None
        server_url = str(config.get("server_url") or "").rstrip("/")
        if not server_url:
            return None
        return server_url, str(config.get("device_id") or "")
    except Exception as e:
        logger.warning(f"Failed to read sync config for todo registration: {e}")
        return None


def fetch_open_todo_titles(server_url: str) -> Optional[List[str]]:
    """Open todo titles from GET /v1/todos?status=open, None when unreachable.

    Fail-soft: the caller deduplicates on the local incomplete todos alone
    when this returns None.
    """
    try:
        import httpx

        response = httpx.get(
            f"{server_url}/v1/todos",
            params={"status": "open"},
            timeout=_TODO_SYNC_TIMEOUT,
        )
        response.raise_for_status()
        todos = response.json().get("todos")
        if not isinstance(todos, list):
            return None
        return [str(t.get("title") or "") for t in todos if isinstance(t, dict)]
    except Exception as e:
        logger.warning(f"Open todo fetch failed (dedup uses local todos only): {e}")
        return None


def post_todo_to_server(server_url: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """POST /v1/todos; the created (or deduped) server todo, None on failure.

    Fail-soft: None tells the caller to fall back to local storage.
    """
    try:
        import httpx

        response = httpx.post(
            f"{server_url}/v1/todos", json=payload, timeout=_TODO_SYNC_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else None
    except Exception as e:
        logger.warning(f"Todo POST to sync server failed (saving locally): {e}")
        return None


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

            # 3. Deduplicate against existing incomplete todos: the sync
            #    server's open todos (when reachable) plus the local ones
            sync_target = get_sync_todo_target()
            server_titles = (
                fetch_open_todo_titles(sync_target[0]) if sync_target else None
            )
            tasks = self._deduplicate_todos(tasks, server_titles=server_titles)
            if not tasks:
                return None

            # 4. Register on the sync server when possible (no local row /
            #    outbox event on success); otherwise local table + outbox
            todo_ids = self._store_tasks(tasks, sync_target)

            return {
                "content": f"{len(todo_ids)} 件のToDoを生成しました。",
                "todo_ids": todo_ids,
                "tasks": tasks,
            }

        except Exception as e:
            logger.exception(f"Failed to generate smart Todo tasks: {e}")
            return None

    def _store_tasks(
        self, tasks: List[Dict[str, Any]], sync_target: Optional[Tuple[str, str]]
    ) -> List[Any]:
        """Persist validated tasks: sync server first, local storage as fallback.

        A task whose POST /v1/todos succeeds (including the server-side
        "deduped" answer) is not written locally; sync disabled / URL unset /
        send failure keep the old local row + outbox event (fail-soft per
        task).
        """
        todo_ids: List[Any] = []
        for task in tasks:
            server_todo = None
            if sync_target:
                server_todo = post_todo_to_server(
                    sync_target[0], build_todo_api_payload(task, sync_target[1])
                )
            if server_todo is not None:
                deduped = bool(server_todo.get("deduped"))
                logger.info(
                    f"Todo registered on sync server{' (deduped)' if deduped else ''}: "
                    f"{task.get('content', '')[:60]}"
                )
                todo_ids.append(str(server_todo.get("id") or ""))
                continue
            todo_ids.append(self._save_todo_locally(task))
        return todo_ids

    def _save_todo_locally(self, task: Dict[str, Any]) -> Optional[int]:
        """Local todo-table row + outbox kind='todo' event (the fallback path)."""
        deadline = self._task_deadline(task)
        todo_id = get_storage().insert_todo(
            content=task["content"],
            urgency=task["urgency"],
            end_time=deadline,
            assignee="",
            reason=task["reason"],
        )

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
        return todo_id

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
        self,
        new_tasks: List[Dict],
        threshold: float = TODO_DUPLICATE_JACCARD,
        server_titles: Optional[List[str]] = None,
    ) -> List[Dict]:
        """Deduplicate new todos against existing incomplete todos and the batch.

        Uses character 3-gram Jaccard similarity instead of the removed
        embedding vector search. ``server_titles`` (the sync server's open
        todos, when reachable) join the local incomplete todos as dedup
        material. Storage failures degrade to no dedup (fail-soft).
        """
        if not new_tasks:
            return []

        existing_texts: List[str] = list(server_titles or [])
        try:
            existing_todos = get_storage().get_todos(status=0, limit=200)
            existing_texts += [todo.get("content") or "" for todo in existing_todos]
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
