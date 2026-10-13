#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0


"""
Context consumption manager, responsible for managing and coordinating context consumption components
"""

import threading
from datetime import datetime
from typing import Any, Dict, Optional

from opencontext.context_consumption.generation.smart_tip_generator import SmartTipGenerator
from opencontext.context_consumption.generation.smart_todo_manager import SmartTodoManager
from opencontext.managers.event_manager import EventType, get_event_manager
from opencontext.storage.global_storage import get_storage
from opencontext.utils.logging_utils import get_logger

logger = get_logger(__name__)


class ConsumptionManager:
    """
    Context consumption manager

    Responsible for managing and coordinating context consumption components, providing a unified interface for context consumption

    The 15-minute activity generator and the daily report generator were
    removed: activity records now come from live_monitor and reports are no
    longer generated locally. Only tips scheduled tasks remain; todos are
    added by the user on the sync server.
    """

    def __init__(self):
        """Initialize context consumption manager - parameters retained for backward compatibility but not used"""
        # Statistics
        self._statistics: Dict[str, Any] = {
            "total_queries": 0,
            "total_contexts_consumed": 0,
            "consumers": {},
            "errors": 0,
        }
        self._smart_tip_generator: Optional[SmartTipGenerator] = None
        self._smart_todo_manager: Optional[SmartTodoManager] = None

        # Load configuration from GlobalConfig
        from opencontext.config.global_config import GlobalConfig

        config = GlobalConfig.get_instance().get_config()
        content_gen_config = config.get("content_generation", {}) if config else {}

        # Scheduled task configuration (load from config)
        self._scheduled_tasks_enabled = False
        self._task_timers: Dict[str, threading.Timer] = {}
        self._task_intervals = {
            "tips": content_gen_config.get("tips", {}).get("interval", 3600),
            "todos": content_gen_config.get("todos", {}).get("interval", 1800),
        }
        self._task_enabled = {
            "tips": content_gen_config.get("tips", {}).get("enabled", True),
            # Ignore legacy user settings that enabled automatic todos.
            "todos": False,
        }

        # Maintain local last successful generation time
        self._last_generation_times = {
            "tips": None,
            "todos": None,
        }

        self._config_lock = threading.Lock()
        self._smart_tip_generator = SmartTipGenerator()
        self._smart_todo_manager = SmartTodoManager()

    @property
    def storage(self):
        return get_storage()

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get statistics

        Returns:
            Dict[str, Any]: Statistics
        """
        return self._statistics.copy()

    def shutdown(self) -> None:
        """
        Shutdown manager
        """
        logger.info("Shutting down context consumption manager...")

        # Stop all scheduled tasks
        self.stop_scheduled_tasks()

        logger.info("Context consumption manager shutdown complete")

    def _should_generate(self, task_type: str) -> bool:
        """Check if specified task type should be generated"""
        try:
            last_time = self._last_generation_times.get(task_type)
            if last_time is None:
                return True

            elapsed = (datetime.now() - last_time).total_seconds()
            interval = self._task_intervals.get(task_type, 0)
            should_generate = elapsed >= interval
            return should_generate

        except Exception as e:
            logger.debug(f"Check {task_type} generation time failed: {e}")
            return True

    def _last_generation_time(self, task_type: str) -> Optional[datetime]:
        """Get last generation time"""
        return self._last_generation_times.get(task_type)

    def start_scheduled_tasks(self, config: Dict[str, Any] = None):
        """Start scheduled tasks"""
        if self._scheduled_tasks_enabled:
            logger.warning("Scheduled tasks are already running")
            return

        if config:
            if "tips_interval" in config:
                self._task_intervals["tips"] = config["tips_interval"]
            if "todos_interval" in config:
                self._task_intervals["todos"] = config["todos_interval"]

        self._scheduled_tasks_enabled = True

        # Start various scheduled tasks
        self._start_tips_timer()

    def stop_scheduled_tasks(self):
        """Stop scheduled tasks"""
        if not self._scheduled_tasks_enabled:
            return

        self._scheduled_tasks_enabled = False

        # Cancel all timers
        for timer in self._task_timers.values():
            if timer:
                timer.cancel()
        self._task_timers.clear()

        logger.info("Scheduled tasks stopped")

    def _start_tips_timer(self):
        """Start smart tips timer"""
        if not self._task_enabled.get("tips", True):
            logger.info("Tips task is disabled, skipping timer start")
            return

        def generate_tips():
            if not (
                self._scheduled_tasks_enabled
                and self._smart_tip_generator
                and self._task_enabled.get("tips", True)
            ):
                return

            try:
                if self._should_generate("tips"):
                    end_time = int(datetime.now().timestamp())
                    last_generation_time = self._last_generation_time("tips")
                    start_time = (
                        int(last_generation_time.timestamp())
                        if last_generation_time
                        else end_time - self._task_intervals.get("tips", 60 * 60)
                    )
                    self._smart_tip_generator.generate_smart_tip(start_time, end_time)
                    self._last_generation_times["tips"] = datetime.now()
            except Exception as e:
                logger.exception(f"Failed to generate smart tip: {e}")

            self._schedule_next_check("tips", generate_tips)

        check_interval = self._calculate_check_interval("tips")
        self._task_timers["tips"] = threading.Timer(check_interval, generate_tips)
        self._task_timers["tips"].start()
        logger.info(
            f"Tips timer started, check interval: {check_interval}s, generation interval: {self._task_intervals['tips']}s"
        )

    def _start_todos_timer(self):
        """Start smart todo timer"""
        if not self._task_enabled.get("todos", False):
            logger.info("Todos task is disabled, skipping timer start")
            return

        def generate_todos():
            if not (
                self._scheduled_tasks_enabled
                and self._smart_todo_manager
                and self._task_enabled.get("todos", True)
            ):
                return

            try:
                if self._should_generate("todos"):
                    end_time = int(datetime.now().timestamp())
                    last_generation_time = self._last_generation_time("todos")
                    start_time = (
                        int(last_generation_time.timestamp())
                        if last_generation_time
                        else end_time - self._task_intervals.get("todos", 30 * 60)
                    )
                    self._smart_todo_manager.generate_todo_tasks(
                        start_time=start_time, end_time=end_time
                    )
                    self._last_generation_times["todos"] = datetime.now()
            except Exception as e:
                logger.exception(f"Failed to generate smart todo: {e}")

            self._schedule_next_check("todos", generate_todos)

        check_interval = self._calculate_check_interval("todos")
        self._task_timers["todos"] = threading.Timer(check_interval, generate_todos)
        self._task_timers["todos"].start()
        logger.info(
            f"Todos timer started, check interval: {check_interval}s, generation interval: {self._task_intervals['todos']}s"
        )

    def _calculate_check_interval(self, task_name: str) -> int:
        """Calculate check interval for a task (1/4 of generation interval with limits)"""
        interval = self._task_intervals.get(task_name, 900)
        limits = {"tips": 200, "todos": 250}
        max_check = limits.get(task_name, 180)
        return min(max_check, interval // 4)

    def _schedule_next_check(self, task_name: str, callback) -> None:
        """Schedule next check for a task"""
        if self._scheduled_tasks_enabled and self._task_enabled.get(task_name, True):
            check_interval = self._calculate_check_interval(task_name)
            self._task_timers[task_name] = threading.Timer(check_interval, callback)
            self._task_timers[task_name].start()

    def get_scheduled_tasks_status(self) -> Dict[str, Any]:
        return {
            "enabled": self._scheduled_tasks_enabled,
            "intervals": self._task_intervals.copy(),
            "active_timers": list(self._task_timers.keys()),
        }

    def get_task_config(self) -> Dict[str, Any]:
        """Get detailed task configuration"""
        with self._config_lock:
            return {
                "tips": {
                    "enabled": self._task_enabled.get("tips", True),
                    "interval": self._task_intervals.get("tips", 60 * 60),
                },
                "todos": {
                    "enabled": self._task_enabled.get("todos", False),
                    "interval": self._task_intervals.get("todos", 30 * 60),
                },
            }

    def update_task_config(self, config: Dict[str, Any]) -> bool:
        """
        Update task configuration dynamically
        """
        try:
            with self._config_lock:
                for task_name in ["tips", "todos"]:
                    if task_name in config:
                        self._update_interval_task(task_name, config[task_name])

                return True
        except Exception as e:
            logger.exception(f"Failed to update task config: {e}")
            return False

    def _update_interval_task(self, task_name: str, task_cfg: Dict[str, Any]) -> None:
        """Update configuration for interval-based tasks (tips/todos)"""
        if task_name == "todos":
            self._task_enabled["todos"] = False
            self._stop_task_timer("todos")
            return

        need_restart = False

        if "enabled" in task_cfg:
            old_enabled = self._task_enabled.get(task_name, True)
            new_enabled = task_cfg["enabled"]
            self._task_enabled[task_name] = new_enabled

            if old_enabled != new_enabled:
                action = "Enabling" if new_enabled else "Disabling"
                logger.info(f"{action} {task_name} task")
                if new_enabled:
                    need_restart = True
                else:
                    self._stop_task_timer(task_name)
                    return

        # Update interval
        if "interval" in task_cfg:
            old_interval = self._task_intervals.get(task_name)
            new_interval = task_cfg["interval"]
            self._task_intervals[task_name] = new_interval

            if old_interval != new_interval and self._task_enabled.get(task_name, True):
                logger.info(f"Updating {task_name} interval to {new_interval}s")
                need_restart = True

        if need_restart:
            self._restart_task_timer(task_name)

    def _stop_task_timer(self, task_name: str) -> None:
        """Stop a specific task timer"""
        if task_name in self._task_timers:
            timer = self._task_timers[task_name]
            if timer:
                timer.cancel()
            del self._task_timers[task_name]
            logger.info(f"Stopped {task_name} timer")

    def _restart_task_timer(self, task_name: str) -> None:
        """Restart a specific task timer"""
        if not self._scheduled_tasks_enabled:
            logger.warning(f"Cannot restart {task_name} timer: scheduled tasks not enabled")
            return

        # Stop existing timer
        self._stop_task_timer(task_name)

        # Start new timer based on task type
        if task_name == "tips":
            self._start_tips_timer()
        elif task_name == "todos":
            self._start_todos_timer()

        logger.info(f"Restarted {task_name} timer")

    def reset_statistics(self) -> None:
        """Reset statistics"""
        for consumer_name in self._statistics["consumers"]:
            self._statistics["consumers"][consumer_name] = {
                "queries": 0,
                "contexts_consumed": 0,
                "errors": 0,
            }

        self._statistics["total_queries"] = 0
        self._statistics["total_contexts_consumed"] = 0
        self._statistics["errors"] = 0