#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Intent Analysis Node
Analyzes user intent and enhances the query
"""

import json
from typing import Dict, List

from opencontext.config.global_config import get_prompt_group
from opencontext.context_consumption.context_agent.models.events import StreamEvent
from opencontext.llm.global_vlm_client import (
    generate_stream_for_agent,
    generate_with_messages_async,
)

from ..core.state import WorkflowState
from ..models.enums import EventType, NodeType, QueryType, WorkflowStage
from ..models.schemas import Intent
from .base import BaseNode


class IntentNode(BaseNode):
    """Intent analysis node"""

    def __init__(self, streaming_manager=None):
        super().__init__(NodeType.INTENT, streaming_manager)

    async def process(self, state: WorkflowState) -> WorkflowState:
        """Process intent analysis"""
        await self.streaming_manager.emit(
            StreamEvent(
                type=EventType.THINKING,
                content="Analyzing your intent...",
                stage=WorkflowStage.INTENT_ANALYSIS,
            )
        )
        # 1. Classify query type
        query_type = await self._classify_query(state.query.text, state.contexts.get_chat_history())
        if not query_type:
            await self.streaming_manager.emit(
                StreamEvent(
                    type=EventType.FAIL,
                    content="Intent analysis failed",
                    stage=WorkflowStage.INTENT_ANALYSIS,
                    metadata={"query": state.query.text},
                )
            )
            state.update_stage(WorkflowStage.FAILED)
            return state

        if query_type == QueryType.SIMPLE_CHAT:
            # For simple chats, directly call the large model for a reply
            return await self._simple_chat(state)
        state.intent = Intent(
            original_query=state.query.text, query_type=query_type, enhanced_query=state.query.text
        )
        return state

    async def _classify_query(self, query: str, chat_history: List[Dict[str, str]]) -> QueryType:
        """Use LLM to classify query types, including confidence assessment and fallback strategies"""
        prompt_group = get_prompt_group("chat_workflow.query_classification")
        messages = [
            {"role": "system", "content": prompt_group["system"]},
            {
                "role": "user",
                "content": prompt_group["user"].format(
                    query=query, chat_history=json.dumps(chat_history)
                ),
            },
        ]
        response = await generate_with_messages_async(
            messages,
            thinking="disabled",
        )
        response = response.strip().lower()
        if "simple_chat" in response:
            return QueryType.SIMPLE_CHAT
        elif "document_edit" in response:
            return QueryType.DOCUMENT_EDIT
        elif "qa_analysis" in response:
            return QueryType.QA_ANALYSIS
        elif "content_generation" in response:
            return QueryType.CONTENT_GENERATION
        return QueryType.QA_ANALYSIS

    async def _simple_chat(self, state: WorkflowState) -> WorkflowState:
        """Handle simple chats with streaming"""
        from ..models.schemas import ExecutionPlan, ExecutionResult

        await self.streaming_manager.emit(
            StreamEvent(
                type=EventType.THINKING,
                content="Generating reply...",
                stage=WorkflowStage.INTENT_ANALYSIS,
            )
        )
        prompt_template = get_prompt_group("chat_workflow.social_interaction")
        user_prompt = prompt_template["user"].format(query=state.query.text)
        messages = [
            {"role": "system", "content": prompt_template["system"]},
            {"role": "user", "content": user_prompt},
        ]
        if state.contexts.chat_history:
            recent_messages = state.contexts.chat_history[-10:]  # Last 10 messages
            for msg in recent_messages:
                messages.insert(1, {"role": msg.role, "content": msg.content})

        # Use streaming generation
        full_content = ""
        chunk_index = 0
        async for chunk in generate_stream_for_agent(messages):
            if chunk.choices and len(chunk.choices) > 0:
                delta = chunk.choices[0].delta
                if hasattr(delta, "content") and delta.content:
                    full_content += delta.content
                    # Emit streaming chunk event
                    await self.streaming_manager.emit(
                        StreamEvent(
                            type=EventType.STREAM_CHUNK,
                            content=delta.content,
                            stage=WorkflowStage.INTENT_ANALYSIS,
                            progress=0.5,
                            metadata={"index": chunk_index},
                        )
                    )
                    chunk_index += 1

        state.execution_result = ExecutionResult(
            success=True,
            plan=ExecutionPlan(),
            outputs=[full_content],
            metadata={"type": "simple_chat"},
        )
        await self.streaming_manager.emit(
            StreamEvent(
                type=EventType.DONE, content="Reply generated", stage=WorkflowStage.INTENT_ANALYSIS
            )
        )
        state.final_content = full_content
        state.update_stage(WorkflowStage.COMPLETED)
        return state

