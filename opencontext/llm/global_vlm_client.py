#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Global LLM manager singleton wrapper.
All generation now goes through the priority-based ProviderRouter
(opencontext/llm/provider_router.py); this module keeps the historical
module-level API (generate_with_messages / agent helpers) and the tool
executor loop used by agent flows.
"""

import asyncio
import concurrent.futures
import json
import threading
from typing import Any, Dict, List, Optional

from opencontext.llm.provider_router import (
    ROUTE_CHAT,
    ROUTE_STRUCTURED,
    ROUTE_VISION,
    get_provider_router,
    messages_contain_image,
)
from opencontext.utils.json_parser import parse_json_from_response
from opencontext.utils.logging_utils import get_logger

logger = get_logger(__name__)


class GlobalVLMClient:
    """
    Global LLM manager (singleton pattern)
    """

    _instance = None
    _lock = threading.Lock()
    _initialized = False

    def __new__(cls):
        """Ensure singleton pattern"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize VLM client"""
        if not self._initialized:
            with self._lock:
                if not GlobalVLMClient._initialized:
                    self._tools_executor = None
                    self._auto_initialized = False
                    GlobalVLMClient._initialized = True

    @classmethod
    def get_instance(cls) -> "GlobalVLMClient":
        """
        Get global LLM manager instance
        """
        instance = cls()
        if not instance._auto_initialized:
            instance._auto_initialize()
        return instance

    @classmethod
    def reset(cls):
        """Reset singleton instance (mainly for testing)"""
        with cls._lock:
            cls._instance = None
            cls._initialized = False

    def _auto_initialize(self):
        """Auto-initialize tools executor (LLM access lives in ProviderRouter)"""
        if self._auto_initialized:
            return
        from opencontext.tools.tools_executor import ToolsExecutor

        self._tools_executor = ToolsExecutor()
        self._auto_initialized = True
        router = get_provider_router()
        if router.has_route(ROUTE_VISION) or router.has_route(ROUTE_STRUCTURED):
            logger.info("GlobalVLMClient initialized (via ProviderRouter)")
        else:
            logger.warning(
                "GlobalVLMClient: no vision/structured provider configured; "
                "LLM-dependent features are disabled until settings are provided"
            )

    def _get_tools_executor(self):
        if self._tools_executor is None:
            from opencontext.tools.tools_executor import ToolsExecutor

            self._tools_executor = ToolsExecutor()
        return self._tools_executor

    def is_initialized(self) -> bool:
        router = get_provider_router()
        return router.has_route(ROUTE_VISION) or router.has_route(ROUTE_STRUCTURED)

    def reinitialize(self):
        """Thread-safe reinitialization (rebuild router from config)"""
        try:
            from opencontext.llm.provider_router import reinitialize_provider_router

            reinitialize_provider_router()
            logger.info("GlobalVLMClient reinitialized (router rebuilt)")
            return True
        except Exception as e:
            logger.error(f"Failed to reinitialize VLM client: {e}")
            return False

    @staticmethod
    def _default_route(messages: list, tools) -> str:
        """Pick route: tools -> chat; images -> vision; otherwise structured."""
        if tools:
            return ROUTE_CHAT
        if messages_contain_image(messages):
            return ROUTE_VISION
        return ROUTE_STRUCTURED

    def generate_with_messages(
        self, messages: list, enable_executor: bool = True, max_calls: int = 5, **kwargs
    ):
        router = get_provider_router()
        tools = kwargs.pop("tools", None)
        route = kwargs.pop("route", None) or self._default_route(messages, tools)
        if not tools:
            return router.generate_sync(route, messages, **kwargs)

        response = router.generate_completion_sync(route, messages, tools=tools, **kwargs)
        call_count = 0
        while enable_executor:
            call_count += 1
            if call_count > max_calls:
                logger.warning(
                    f"Reached maximum tool call limit ({max_calls}), stopping further calls"
                )
                messages.append(
                    {
                        "role": "system",
                        "content": f"System notice: Maximum tool call limit ({max_calls}) reached. Cannot execute more tool calls. Please answer the user's question directly without attempting more tool calls.",
                    }
                )
                response = router.generate_completion_sync(
                    route, messages, tools=tools, **kwargs
                )
                break
            message = response.choices[0].message
            if not message.tool_calls:
                break
            messages.append(message)
            tool_calls = message.tool_calls
            tool_call_info = []
            for tc in tool_calls:
                function_name = tc.function.name
                function_args = parse_json_from_response(tc.function.arguments)
                tool_call_info.append((tc.id, function_name, function_args))
            results = []
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future_to_tool = {
                    executor.submit(
                        self._get_tools_executor().run, function_name, function_args
                    ): (tool_id, function_name)
                    for tool_id, function_name, function_args in tool_call_info
                }
                for future in concurrent.futures.as_completed(future_to_tool):
                    tool_id, function_name = future_to_tool[future]
                    try:
                        content = future.result()
                        results.append((tool_id, function_name, content))
                    except Exception as e:
                        logger.error(f"Tool call {function_name} failed: {e}")
                        results.append((tool_id, function_name, "failed"))
            for tool_id, function_name, content in results:
                messages.append(
                    {
                        "role": "tool",
                        "name": function_name,
                        "content": json.dumps(content),
                        "tool_call_id": tool_id,
                    }
                )
            response = router.generate_completion_sync(route, messages, tools=tools, **kwargs)

        message = response.choices[0].message
        return message.content

    async def generate_with_messages_async(
        self, messages: list, enable_executor: bool = True, max_calls: int = 5, **kwargs
    ):
        router = get_provider_router()
        tools = kwargs.pop("tools", None)
        route = kwargs.pop("route", None) or self._default_route(messages, tools)
        if not tools:
            return await router.generate(route, messages, **kwargs)

        response = await router.generate_completion(route, messages, tools=tools, **kwargs)
        call_count = 0
        while enable_executor:
            call_count += 1
            if call_count > max_calls:
                messages.append(
                    {
                        "role": "system",
                        "content": f"System notice: Maximum tool call limit ({max_calls}) reached. Cannot execute more tool calls. Please answer the user's question directly without attempting more tool calls.",
                    }
                )
                response = await router.generate_completion(
                    route, messages, tools=tools, **kwargs
                )
                break
            message = response.choices[0].message
            if not message.tool_calls:
                break
            messages.append(message)
            tool_calls = message.tool_calls

            # Collect all async tasks for tool calls
            tasks = []
            tool_call_info = []
            for tc in tool_calls:
                function_name = tc.function.name
                function_args = parse_json_from_response(tc.function.arguments)
                if function_args is not None:
                    tasks.append(
                        self._get_tools_executor().run_async(function_name, function_args)
                    )
                    tool_call_info.append((tc.id, function_name))
                else:
                    logger.error(
                        f"Failed to parse arguments for {function_name}: {tc.function.arguments}"
                    )

            # Execute all tool calls in parallel
            results = await asyncio.gather(*tasks)

            # Collect results and add to message list
            for result, (tool_id, function_name) in zip(results, tool_call_info):
                messages.append(
                    {
                        "role": "tool",
                        "name": function_name,
                        "content": json.dumps(result),
                        "tool_call_id": tool_id,
                    }
                )

            # Call LLM again
            response = await router.generate_completion(
                route, messages, tools=tools, **kwargs
            )

        message = response.choices[0].message
        return message.content

    async def generate_for_agent_async(self, messages: list, tools: list = None, **kwargs):
        """
        Agent-specific generation method that returns raw response without auto-executing tool calls

        Args:
            messages: Message list
            tools: Available tool definitions
            **kwargs: Other parameters

        Returns:
            Raw LLM response object, including possible tool_calls
        """
        router = get_provider_router()
        route = kwargs.pop("route", None) or ROUTE_CHAT
        return await router.generate_completion(route, messages, tools=tools, **kwargs)

    async def generate_stream_for_agent(self, messages: list, tools: list = None, **kwargs):
        """
        Agent-specific streaming generation method
        """
        router = get_provider_router()
        route = kwargs.pop("route", None) or ROUTE_CHAT
        async for chunk in router.stream_completion(route, messages, tools=tools, **kwargs):
            yield chunk

    async def execute_tool_async(self, tool_call):
        """
        Execute a single tool call independently

        Args:
            tool_call: OpenAI format tool call object

        Returns:
            Tool execution result
        """
        function_name = tool_call.function.name
        function_args = parse_json_from_response(tool_call.function.arguments)

        if function_args is None:
            logger.error(
                f"Failed to parse arguments for {function_name}: {tool_call.function.arguments}"
            )
            return {"error": f"Failed to parse arguments for {function_name}"}

        try:
            result = await self._get_tools_executor().run_async(function_name, function_args)
            logger.info(f"Tool {function_name} executed successfully")
            return result
        except Exception as e:
            logger.exception(f"Tool {function_name} execution failed: {e}")
            return {"error": str(e)}


def is_initialized() -> bool:
    return GlobalVLMClient.get_instance().is_initialized()


def generate_with_messages(
    messages: list, enable_executor: bool = True, max_calls: int = 5, **kwargs
):
    return GlobalVLMClient.get_instance().generate_with_messages(
        messages, enable_executor, max_calls, **kwargs
    )


async def generate_with_messages_async(
    messages: list, enable_executor: bool = True, max_calls: int = 5, **kwargs
):
    return await GlobalVLMClient.get_instance().generate_with_messages_async(
        messages, enable_executor, max_calls, **kwargs
    )


async def generate_for_agent_async(messages: list, tools: list = None, **kwargs):
    return await GlobalVLMClient.get_instance().generate_for_agent_async(messages, tools, **kwargs)


async def generate_stream_for_agent(messages: list, tools: list = None, **kwargs):
    async for chunk in GlobalVLMClient.get_instance().generate_stream_for_agent(
        messages, tools, **kwargs
    ):
        yield chunk
