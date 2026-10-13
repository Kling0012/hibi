#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Smart Tip Generator - Hibi 向けの「気づき」生成。

直近の画面の書き起こし（screen_summary）と 30 秒ごとの活動要約（activity）、
同期サーバの当日の宣言作業（work sessions）を材料に、根拠（時刻と画面）付きの
短い気づきを最大 2 件出す。同期サーバに届かなくてもローカルの材料だけで
動く（fail-soft。止まるのはこの生成だけ）。
"""

import datetime
from typing import Any, Dict, List, Optional

from opencontext.config.global_config import get_prompt_group
from opencontext.context_consumption.generation.debug_helper import DebugHelper
from opencontext.context_consumption.generation.materials import (
    build_generation_materials,
    fetch_today_sessions,
    format_tip_text,
    format_user_prompt,
    parse_tip_items,
)
from opencontext.llm.global_vlm_client import generate_with_messages
from opencontext.storage.global_storage import get_storage
from opencontext.utils.json_parser import parse_json_from_response
from opencontext.utils.logging_utils import get_logger

# Backward-compatible re-export: the todo manager and tests import the
# material builder from this module.
from opencontext.context_consumption.generation.materials import build_tip_materials  # noqa: F401

logger = get_logger(__name__)


class SmartTipGenerator:
    """
    Smart Tip Generator
    Generates short, evidence-backed tips (気づき) from the user's recent
    screen summaries, activities and declared work sessions.
    """

    def generate_smart_tip(self, start_time: int, end_time: int) -> Optional[Dict[str, Any]]:
        """
        Generate up to 2 tips from the recent time window's materials.
        Returns None when there are no materials or no valid tip.
        """
        try:
            # 1. Materials: window activities / screen summaries + today's sessions
            materials = self._collect_materials(start_time, end_time)
            if not materials:
                logger.info("No materials for tip generation; skipped.")
                return None

            # 2. One structured-route LLM call, then validate the JSON output
            raw = self._generate_raw_tip(materials, start_time, end_time)
            items = parse_tip_items(raw)
            if not items:
                logger.info("No tip with valid evidence was generated; skipped.")
                return None

            # 3. Drop tips already given in the last 24h (hourly runs on the
            #    same unresolved issue would otherwise repeat it)
            items = self._drop_recent_duplicates(items)
            if not items:
                logger.info("All generated tips repeat recent ones; skipped.")
                return None

            # 4. Store one line per tip in the SQLite tips table
            content = "\n".join(format_tip_text(item) for item in items)
            tip_id = get_storage().insert_tip(content=content)
            logger.info(f"Smart tip saved to the tips table, ID: {tip_id}")

            from opencontext.managers.event_manager import EventType, publish_event

            publish_event(
                event_type=EventType.TIP_GENERATED,
                data={
                    "doc_id": str(tip_id),
                    "doc_type": "tips",
                    "title": "気づき",
                    "content": content,
                },
            )
            return {
                "doc_id": str(tip_id),
                "title": "気づき",
                "content": content,
                "tips": items,
            }

        except Exception as e:
            logger.exception(f"Failed to generate smart tip: {e}")
            return None

    @staticmethod
    def _drop_recent_duplicates(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        try:
            # lazy: smart_todo_manager imports this module
            from opencontext.context_consumption.generation.smart_todo_manager import trigram_jaccard

            since = datetime.datetime.now() - datetime.timedelta(hours=24)
            recent = [
                line
                for tip in get_storage().get_tips(start_time=since, limit=50)
                for line in str(tip.get("content") or "").splitlines()
                if line.strip()
            ]
            return [
                item
                for item in items
                if all(trigram_jaccard(format_tip_text(item), line) < 0.6 for line in recent)
            ]
        except Exception as e:  # fail-soft: keep the new tips
            logger.warning(f"recent-tip dedupe skipped: {e}")
            return items

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
                f"Tip materials: {len(materials)} lines "
                f"({len(sessions)} declared sessions)."
            )
            return materials
        except Exception as e:
            logger.exception(f"Failed to collect tip materials: {e}")
            return []

    def _generate_raw_tip(
        self, materials: List[str], start_time: int, end_time: int
    ) -> Optional[Any]:
        """One LLM call on the structured route; returns the parsed JSON."""
        prompt_group = get_prompt_group("generation.smart_tip_generation")
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
            task_type="tips",
            messages=messages,
            response=response,
            metadata={
                "start_time": start_time,
                "end_time": end_time,
                "num_materials": len(materials),
            },
        )
        return parse_json_from_response(response)
