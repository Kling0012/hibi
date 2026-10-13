#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Smart Todo Manager - Intelligently identifies and manages to-do items based on user activity.
"""

import datetime
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from opencontext.config.global_config import get_prompt_group
from opencontext.context_consumption.generation.debug_helper import DebugHelper
from opencontext.context_consumption.generation.smart_tip_generator import build_tip_materials
from opencontext.llm.global_vlm_client import generate_with_messages
from opencontext.storage.global_storage import get_storage
from opencontext.utils.json_parser import parse_json_from_response
from opencontext.utils.logging_utils import get_logger

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


@dataclass
class TodoTask:
    """To-do task data structure."""

    title: str
    description: str
    category: str = "general"
    priority: str = "normal"
    due_date: Optional[str] = None
    due_time: Optional[str] = None
    estimated_duration: Optional[str] = None
    assignee: Optional[str] = None
    participants: List[str] = field(default_factory=list)
    context_reference: Optional[str] = None
    reason: Optional[str] = None
    created_at: Optional[str] = None


class SmartTodoManager:
    """
    Smart Todo Manager
    Intelligently identifies and generates to-do items based on user activity context.
    """

    def _map_priority_to_urgency(self, priority: str) -> int:
        """Map priority to a numerical urgency value."""
        priority_map = {"low": 0, "medium": 1, "high": 2, "urgent": 3}
        return priority_map.get(priority.lower(), 0)

    def generate_todo_tasks(self, start_time: int, end_time: int) -> Optional[str]:
        """
        Generate Todo tasks based on recent activity, combining activity insights and historical todo information.
        """
        try:
            # 1. Get insights from recent activities
            activity_insights = self._get_recent_activity_insights(start_time, end_time)
            # 2. Get regular context data
            contexts = self._get_task_relevant_contexts(start_time, end_time, activity_insights)
            # 3. Get historical todo completion status
            # historical_todos = self._get_historical_todos()
            historical_todos  = []
            # 4. Synthesize all information to generate high-quality todos
            tasks = self._extract_tasks_from_contexts_enhanced(
                contexts, start_time, end_time, activity_insights, historical_todos
            )

            if not tasks:
                return None
            # Store in the SQLite todo table
            todo_ids = []
            for task in tasks:
                participants_str = ""
                if task.get("participants") and len(task["participants"]) > 0:
                    participants_str = ",".join(task["participants"])

                content = task.get("description", "")
                reason = task.get("reason", "")
                urgency = self._map_priority_to_urgency(task.get("priority", "normal"))

                deadline = None
                if task.get("due_date"):
                    try:
                        if task.get("due_time"):
                            deadline_str = f"{task['due_date']} {task['due_time']}"
                            deadline = datetime.datetime.strptime(deadline_str, "%Y-%m-%d %H:%M")
                        else:
                            deadline = datetime.datetime.strptime(task["due_date"], "%Y-%m-%d")
                    except Exception:
                        pass

                todo_id = get_storage().insert_todo(
                    content=content,
                    urgency=urgency,
                    end_time=deadline,
                    assignee=participants_str,
                    reason=reason,
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
                        title=(content or "")[:100],
                        text=content or "",
                        meta={"reason": reason, "urgency": urgency,
                              "participants": task.get("participants", [])},
                        source="minecontext-desktop",
                    )
                except Exception as e:
                    logger.warning(f"Outbox enqueue failed (non-fatal): {e}")

            # Return the complete result for external event processing
            return {
                "content": f"{len(todo_ids)} tasks have been generated.",
                "todo_ids": todo_ids,
                "tasks": tasks,
            }

        except Exception as e:
            logger.exception(f"Failed to generate smart Todo tasks: {e}")
            return None

    def _get_recent_activity_insights(self, start: int, end: int) -> Dict[str, Any]:
        """Get insights extracted from recent activities."""
        try:
            start_time = datetime.datetime.fromtimestamp(start)
            end_time = datetime.datetime.fromtimestamp(end)
            # Query recent activity records
            activities = get_storage().get_activities(
                start_time=start_time, end_time=end_time, limit=100
            )
            if not activities:
                return {}
            merged_insights = {"potential_todos": [], "key_entities": [], "focus_areas": []}

            for activity in activities:
                # Parse metadata
                if activity.get("metadata"):
                    try:
                        metadata = json.loads(activity["metadata"])
                        insights = metadata.get("extracted_insights", {})
                        if "potential_todos" in insights:
                            merged_insights["potential_todos"].extend(insights["potential_todos"])
                        if "key_entities" in insights:
                            merged_insights["key_entities"].extend(insights["key_entities"])
                        if "focus_areas" in insights:
                            merged_insights["focus_areas"].extend(insights["focus_areas"])
                    except json.JSONDecodeError:
                        logger.debug(f"Failed to parse activity metadata: {activity.get('id')}")
                        continue
            merged_insights["key_entities"] = list(set(merged_insights["key_entities"]))[:10]
            merged_insights["focus_areas"] = list(set(merged_insights["focus_areas"]))[:5]
            return merged_insights

        except Exception as e:
            logger.exception(f"Failed to get activity insights: {e}")
            return {}

    def _get_historical_todos(self, days: int = 7, limit: int = 50) -> List[Dict[str, Any]]:
        """Get historical todo records."""
        try:
            start_time = datetime.datetime.now() - datetime.timedelta(days=days)
            todos = get_storage().get_todos(limit=limit, start_time=start_time)
            return todos
        except Exception as e:
            logger.exception(f"Failed to get historical todos: {e}")
            return []

    def _get_task_relevant_contexts(
        self, start_time: int, end_time: int, activity_insights: Dict[str, Any] = None
    ) -> List[str]:
        """Get task-relevant context material from SQLite."""
        try:
            start = datetime.datetime.fromtimestamp(start_time)
            end = datetime.datetime.fromtimestamp(end_time)

            activities = get_storage().get_activities(
                start_time=start, end_time=end, limit=50
            )
            screen_summaries = get_storage().get_screen_summaries(
                start_time=start, end_time=end, limit=50
            )

            all_contexts_data = build_tip_materials(activities, screen_summaries)
            logger.info(
                f"Retrieved {len(all_contexts_data)} context records relevant to task identification."
            )
            return all_contexts_data

        except Exception as e:
            logger.exception(f"Failed to get task-relevant context: {e}")
            return []

    def _extract_tasks_from_contexts_enhanced(
        self,
        context_data: List[Any],
        start_time: int,
        end_time: int,
        activity_insights: Dict[str, Any] = None,
        historical_todos: List[Dict[str, Any]] = None,
    ) -> List[Dict]:
        """
        Task extraction, combining multiple information sources.
        """
        try:
            # Get the prompt template for task extraction
            prompt_group = get_prompt_group("generation.todo_extraction")
            system_prompt = prompt_group["system"]
            user_prompt_template = prompt_group["user"]

            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Build the user prompt
            user_prompt = user_prompt_template.format(
                current_time=current_time,
                historical_todos=(
                    json.dumps(historical_todos, ensure_ascii=False, indent=2)
                    if historical_todos
                    else "[]"
                ),
                potential_todos=(
                    json.dumps(
                        activity_insights.get("potential_todos", []), ensure_ascii=False, indent=2
                    )
                    if activity_insights
                    else "[]"
                ),
                context_data=(
                    json.dumps(context_data, ensure_ascii=False, indent=2) if context_data else "[]"
                ),
            )
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            # Call the large model to extract tasks, enabling tools to get relevant background information
            task_response = generate_with_messages(
                messages,
            )

            # Save debug information
            DebugHelper.save_generation_debug(
                task_type="todo",
                messages=messages,
                response=task_response,
                metadata={
                    "start_time": start_time,
                    "end_time": end_time,
                    "num_contexts": len(context_data) if context_data else 0,
                    "num_historical_todos": len(historical_todos) if historical_todos else 0,
                },
            )

            tasks = parse_json_from_response(task_response)
            tasks = self._post_process_tasks(tasks)

            # Deduplicate against existing incomplete todos (character 3-gram Jaccard)
            tasks = self._deduplicate_todos(tasks)

            logger.info(f"Identified {len(tasks)} tasks from the context after deduplication.")
            return tasks

        except Exception as e:
            logger.exception(f"Failed to extract tasks from context: {e}")
            return []

    def _post_process_tasks(self, tasks: List[Dict]) -> List[Dict]:
        """Post-process tasks to complete information."""
        processed_tasks = []

        for task in tasks:
            try:
                if not task.get("description", "") or not task.get("description", "").strip():
                    continue
                # Ensure necessary fields exist
                processed_task = {
                    "title": task.get("title", "Untitled Task"),
                    "description": task.get("description", ""),
                    "category": task.get("category", "General Task"),
                    "priority": task.get("priority", "Medium Priority"),
                    "due_date": task.get("due_date", ""),
                    "due_time": task.get("due_time", ""),
                    "estimated_duration": task.get("estimated_duration", ""),
                    "assignee": task.get("assignee", ""),  # Task assignee
                    "participants": task.get("participants", []),  # List of participants
                    "context_reference": task.get("context_reference", ""),
                    "created_at": datetime.datetime.now().isoformat(),
                    "reason": task.get("reason", ""),
                }

                # Process the deadline
                processed_task = self._process_task_deadline(processed_task)

                # Process personnel information
                processed_task = self._process_task_people(processed_task)

                processed_tasks.append(processed_task)

            except Exception as e:
                logger.debug(f"Failed to post-process task: {e}")
                continue

        return processed_tasks

    def _process_task_deadline(self, task: Dict) -> Dict:
        """Process the task deadline."""
        try:
            current_time = datetime.datetime.now()

            # If there is no due date, set a default deadline based on priority
            if not task["due_date"]:
                if task["priority"] == "High Priority":
                    # High-priority task: today or tomorrow
                    due_date = current_time + datetime.timedelta(days=1)
                    task["due_date"] = due_date.strftime("%Y-%m-%d")
                    if not task["due_time"]:
                        task["due_time"] = "18:00"
                elif task["priority"] == "Medium Priority":
                    # Medium-priority task: within 3 days
                    due_date = current_time + datetime.timedelta(days=3)
                    task["due_date"] = due_date.strftime("%Y-%m-%d")
                else:
                    # Low-priority task: within a week
                    due_date = current_time + datetime.timedelta(days=7)
                    task["due_date"] = due_date.strftime("%Y-%m-%d")

            # If there is a date but no time, set a default time
            if task["due_date"] and not task["due_time"]:
                if task["priority"] == "High Priority":
                    task["due_time"] = "12:00"
                else:
                    task["due_time"] = "17:00"

            return task

        except Exception as e:
            logger.debug(f"Failed to process deadline for task {task.get('title', 'unknown')}: {e}")
            return task

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
            [task.get("description", "") for task in new_tasks],
            existing_texts,
            threshold=threshold,
        )

        filtered_tasks = []
        for task, keep in zip(new_tasks, keep_flags):
            if not keep:
                logger.info(
                    f"🚫 Todo filtered (duplicate): {task.get('description', '')[:60]}"
                )
                continue
            filtered_tasks.append(task)
        return filtered_tasks

    def _process_task_people(self, task: Dict) -> Dict:
        """Process task personnel information."""
        try:
            # Clean and validate the assignee field
            if task.get("assignee"):
                assignee = str(task["assignee"]).strip()
                if assignee and assignee != "":
                    task["assignee"] = assignee
                else:
                    task["assignee"] = ""

            # Clean and validate the participants field
            if task.get("participants"):
                if isinstance(task["participants"], list):
                    # Filter out empty and duplicate values
                    participants = [
                        str(p).strip() for p in task["participants"] if p and str(p).strip()
                    ]
                    task["participants"] = list(set(participants))  # Remove duplicates
                elif isinstance(task["participants"], str):
                    # If it is a string, try to parse it (it may be comma-separated)
                    participants = [p.strip() for p in task["participants"].split(",") if p.strip()]
                    task["participants"] = participants
                else:
                    task["participants"] = []
            else:
                task["participants"] = []

            # Ensure the assignee is not duplicated in the participants
            if task["assignee"] and task["assignee"] in task["participants"]:
                task["participants"].remove(task["assignee"])

            return task

        except Exception as e:
            logger.debug(
                f"Failed to process personnel information for task {task.get('title', 'unknown')}: {e}"
            )
            return task
