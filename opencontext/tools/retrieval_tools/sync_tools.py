# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Sync-server retrieval tools

Search and day-timeline lookup against the sync server, which holds the
consolidated activity / screen records of all devices. These replace the
removed chromadb context retrieval tools. Every failure is fail-soft: the
tool returns a result object describing the problem instead of raising.
"""

import os
from datetime import datetime, timedelta
from typing import Any, Dict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from opencontext.tools.base import BaseTool
from opencontext.utils.logging_utils import get_logger

logger = get_logger(__name__)

_REQUEST_TIMEOUT = 15.0
_SEARCH_TOP_K = 20

_UNREACHABLE_MESSAGE = (
    "同期サーバに接続できません。同期設定の server_url を確認してください。"
)


def _get_server_url() -> str:
    """Sync server base URL (user_setting overrides included), '' when unset."""
    try:
        from opencontext.config.global_config import get_config

        sync_config = get_config("sync") or {}
        return str(sync_config.get("server_url") or "").rstrip("/")
    except Exception as e:
        logger.warning(f"Failed to read sync config: {e}")
        return ""


def _local_tz_name() -> str:
    """Validated IANA name from TZ or /etc/localtime; never guess UTC."""
    marker = "zoneinfo/"
    tz_name = os.environ.get("TZ", "").removeprefix(":")
    if tz_name:
        if os.path.isabs(tz_name) and marker in tz_name:
            tz_name = tz_name.split(marker, 1)[1]
        try:
            return ZoneInfo(tz_name).key
        except (ValueError, ZoneInfoNotFoundError):
            logger.warning("TZ is not a valid IANA name; checking the system timezone")
    try:
        target = os.path.realpath("/etc/localtime")
        if marker in target:
            zone = ZoneInfo(target.split(marker, 1)[1])
            if not tz_name:
                return zone.key
            # Resolve each sample through the process timezone separately:
            # astimezone() without an argument returns a fixed-offset tzinfo.
            # Seasonal samples detect common DST mismatches, not all rule differences.
            now = datetime.now()
            samples = (now, now + timedelta(days=183))
            if all(
                sample.astimezone().utcoffset() == sample.astimezone(zone).utcoffset()
                for sample in samples
            ):
                return zone.key
    except (OSError, ValueError, ZoneInfoNotFoundError):
        pass
    raise ValueError("Local timezone is unknown or invalid; set TZ to a valid IANA name")


def _error_result(reason: str) -> Dict[str, Any]:
    return {"error": reason, "message": _UNREACHABLE_MESSAGE, "results": []}


class SearchRecordsTool(BaseTool):
    """
    Search recorded activity / screen records on the sync server.

    Calls POST {sync.server_url}/v1/search with {query, from, to, k}.
    """

    @classmethod
    def get_name(cls) -> str:
        """Get tool name"""
        return "search_records"

    @classmethod
    def get_description(cls) -> str:
        """Get tool description"""
        return """Search the user's recorded activity and screen records stored on the sync server.

**What this tool retrieves:**
- Activity records and screen summaries from all synced devices
- Time range, app, window title and text of each record

**When to use this tool:**
- When you need to know what the user was doing or seeing
- When searching past work by keyword

**Parameters:**
- query (required): free-text search keywords
- from / to (optional): Unix epoch seconds bounding the search range

**Returns:**
- Up to 20 matching records, newest relevance first"""

    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        """Get tool parameters"""
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords"},
                "from": {
                    "type": "integer",
                    "description": "Search from this Unix timestamp (seconds, optional)",
                },
                "to": {
                    "type": "integer",
                    "description": "Search up to this Unix timestamp (seconds, optional)",
                },
            },
            "required": ["query"],
        }

    def execute(self, query: str = "", **kwargs) -> Dict[str, Any]:
        """Execute the search. `from` arrives via kwargs (reserved keyword)."""
        server_url = _get_server_url()
        if not server_url:
            return _error_result("sync server URL is not configured")

        body: Dict[str, Any] = {"query": query, "k": _SEARCH_TOP_K}
        # Accept both "from" and "from_" styles for the reserved-word parameter
        from_value = kwargs.get("from", kwargs.get("from_"))
        if from_value is not None:
            body["from"] = from_value
        if kwargs.get("to") is not None:
            body["to"] = kwargs["to"]

        try:
            response = httpx.post(
                f"{server_url}/v1/search", json=body, timeout=_REQUEST_TIMEOUT
            )
            response.raise_for_status()
            data = response.json()
            results = [
                {
                    "started_at": hit.get("event", {}).get("started_at"),
                    "ended_at": hit.get("event", {}).get("ended_at"),
                    "kind": hit.get("event", {}).get("kind"),
                    "app": hit.get("event", {}).get("app"),
                    "title": hit.get("event", {}).get("title"),
                    "text": hit.get("event", {}).get("text"),
                    "score": hit.get("score"),
                }
                for hit in data.get("hits", [])
            ]
            return {"query": query, "results": results, "total": len(results)}
        except Exception as e:
            logger.warning(f"Sync search failed: {e}")
            return _error_result(str(e))


class GetDayTimelineTool(BaseTool):
    """
    Get one day's timeline from the sync server.

    Calls GET {sync.server_url}/v1/timeline?date=&tz=<local tz>.
    """

    @classmethod
    def get_name(cls) -> str:
        """Get tool name"""
        return "get_day_timeline"

    @classmethod
    def get_description(cls) -> str:
        """Get tool description"""
        return """Get the timeline of one day from the sync server.

**What this tool retrieves:**
- Chronological items (records and work sessions) of the requested day
- Session totals and total recorded minutes

**When to use this tool:**
- When you need an overview of how a day was spent
- When the user asks about a specific date

**Parameters:**
- date (required): the day, YYYY-MM-DD

**Returns:**
- Timeline items with time and event details, plus session summaries"""

    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        """Get tool parameters"""
        return {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Day to retrieve, YYYY-MM-DD",
                },
            },
            "required": ["date"],
        }

    def execute(self, date: str = "", **kwargs) -> Dict[str, Any]:
        """Execute the timeline lookup."""
        server_url = _get_server_url()
        if not server_url:
            return _error_result("sync server URL is not configured")

        try:
            tz_name = _local_tz_name()
        except Exception as e:
            logger.warning(f"Sync timeline timezone detection failed: {e}")
            return {
                "error": str(e),
                "message": (
                    "ローカルのタイムゾーンを確認できないため、日別タイムラインを取得できません。"
                    "環境変数 TZ に Asia/Tokyo などの IANA 名を設定してアプリを再起動してください。"
                ),
                "results": [],
            }

        try:
            response = httpx.get(
                f"{server_url}/v1/timeline",
                params={"date": date, "tz": tz_name},
                timeout=_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            items = [
                {
                    "type": item.get("type"),
                    "t": item.get("t"),
                    "event": item.get("event"),
                    **({"session": item.get("session")} if item.get("type") == "session" else {}),
                }
                for item in data.get("items", [])
            ]
            return {
                "date": data.get("date", date),
                "tz": data.get("tz"),
                "items": items,
                "session_totals": data.get("session_totals", []),
                "total_minutes": data.get("total_minutes", 0),
            }
        except Exception as e:
            logger.warning(f"Sync timeline lookup failed: {e}")
            return _error_result(str(e))
