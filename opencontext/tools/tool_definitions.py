# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Tool: tool_definitions
"""

from opencontext.tools.retrieval_tools import *

# Sync-server retrieval tools (search moved from chromadb to the sync server)
SYNC_RETRIEVAL_TOOLS = [
    {"type": "function", "function": SearchRecordsTool.get_definition()},
    {"type": "function", "function": GetDayTimelineTool.get_definition()},
]

# Document retrieval tools (SQLite-based)
DOCUMENT_RETRIEVAL_TOOLS = [
    {"type": "function", "function": GetDailyReportsTool.get_definition()},
    {"type": "function", "function": GetActivitiesTool.get_definition()},
    {"type": "function", "function": GetTipsTool.get_definition()},
    {"type": "function", "function": GetTodosTool.get_definition()},
]

ALL_RETRIEVAL_TOOL_DEFINITIONS = SYNC_RETRIEVAL_TOOLS + DOCUMENT_RETRIEVAL_TOOLS

# The chromadb profile entity tool was removed with the vector storage.
ALL_PROFILE_TOOL_DEFINITIONS: list = []

ALL_TOOL_DEFINITIONS = ALL_RETRIEVAL_TOOL_DEFINITIONS + ALL_PROFILE_TOOL_DEFINITIONS
