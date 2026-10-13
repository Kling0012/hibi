# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
OpenContext retrieval_tools module initialization

Sync-server retrieval tools and SQLite document retrieval tools. The
chromadb context retrieval tools were removed when search moved to the
sync server.
"""

# Base classes
from .base_document_retrieval_tool import BaseDocumentRetrievalTool

# Document retrieval tools (SQLite-based)
from .get_activities_tool import GetActivitiesTool
from .get_daily_reports_tool import GetDailyReportsTool
from .get_tips_tool import GetTipsTool
from .get_todos_tool import GetTodosTool

# Sync-server retrieval tools
from .sync_tools import GetDayTimelineTool, SearchRecordsTool

__all__ = [
    # Base classes
    "BaseDocumentRetrievalTool",
    # Sync-server retrieval tools
    "SearchRecordsTool",
    "GetDayTimelineTool",
    # Document retrieval tools
    "GetDailyReportsTool",
    "GetActivitiesTool",
    "GetTipsTool",
    "GetTodosTool",
]
