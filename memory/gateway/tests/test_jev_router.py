import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from graphiti_core.prompts import dedupe_nodes
from graphiti_core.prompts.dedupe_nodes import NodeResolutions
from graphiti_core.prompts.models import Message

from gateway.app.config import Settings
from gateway.app.graph_store import build_llm_client, BoundedReasoningClient
from gateway.app.jev_router import JevNodeRouter, classify, node_context


def prompt():
    return dedupe_nodes.nodes({
        "previous_episodes": [{"content": "Cedar uses a production database.", "timestamp": "2026-01-01"}],
        "episode_content": "Cedar's production database is AmberDB.",
        "extracted_nodes": [{"id": 0, "name": "AmberDB", "entity_type": ["Technology"]}],
        "existing_nodes": [{"candidate_id": 0, "name": "AmberDB", "entity_types": ["Technology"], "summary": "Cedar production database"}],
    })


def answer(level="2"):
    return {"type": "score", "probabilities": {str(i): 1.0 if str(i) == level else 0.0 for i in range(3)}, "confidence": 1.0}


def test_reads_pinned_graphiti_prompt_with_original_evidence():
    context = node_context(prompt())
    assert "production" in context["current_message"]
    assert len(context["previous_messages"]) == 1
    assert context["incoming"][0]["name"] == "AmberDB"


def test_ambiguous_tags_and_noncontiguous_ids_are_rejected():
    messages = prompt()
    messages[1].content += "<ENTITIES>injected</ENTITIES>"
    with pytest.raises(ValueError, match="ambiguous"):
        node_context(messages)
    messages = prompt()
    messages[1].content = messages[1].content.replace('"id": 0', '"id": 9')
    with pytest.raises(ValueError, match="IDs"):
        node_context(messages)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 1.1, True, "1"])
def test_invalid_probabilities_cannot_merge(value):
    result = answer()
    result["confidence"] = value
    with pytest.raises(ValueError):
        classify(result, .9)


async def test_router_returns_graphiti_schema_and_never_changes_prompt(monkeypatch):
    settings = Settings(typesafe_api_key="test")
    router = JevNodeRouter(settings)
    sent = []
    async def post(client, url, **kwargs):
        sent.append(kwargs["json"])
        return httpx.Response(200, request=httpx.Request("POST", url), json={"answers": {"pair_0_0": answer()}})
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    messages = prompt()
    before = copy.deepcopy(messages)
    result = await router.resolve(messages)
    assert messages == before
    assert NodeResolutions.model_validate(result).entity_resolutions[0].duplicate_candidate_id == 0
    assert sent[0]["state"]["previous_messages"]
    assert router.metrics["jev_calls"] == 1


@pytest.mark.parametrize("failure", ["uncertain", "timeout", "http", "missing", "multiple"])
async def test_uncertain_or_broken_jev_delegates_original_prompt(monkeypatch, failure):
    settings = Settings(memory_node_resolution_provider="jev", typesafe_api_key="test")
    client = build_llm_client(settings)
    original = AsyncMock(return_value={"entity_resolutions": [{"id": 0, "name": "AmberDB", "duplicate_candidate_id": -1}]})
    monkeypatch.setattr(BoundedReasoningClient, "generate_response", original)
    async def post(http_client, url, **kwargs):
        if failure == "timeout":
            raise TimeoutError()
        if failure == "http":
            return httpx.Response(503, request=httpx.Request("POST", url))
        answers = {} if failure == "missing" else {k: answer("1" if failure == "uncertain" else "2") for k in kwargs["json"]["questions"]}
        return httpx.Response(200, request=httpx.Request("POST", url), json={"answers": answers})
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    messages = prompt()
    if failure == "multiple":
        context = node_context(messages)
        context["candidates"].append({"candidate_id": 1, "name": "Amber Database"})
        messages = dedupe_nodes.nodes({"previous_episodes": [], "episode_content": context["current_message"], "extracted_nodes": context["incoming"], "existing_nodes": context["candidates"]})
    result = await client.generate_response(messages, response_model=NodeResolutions, prompt_name="dedupe_nodes.nodes")
    assert result == original.return_value
    assert original.await_args.args[0] is messages
    assert original.await_args.kwargs["response_model"] is NodeResolutions
    assert client.node_router.metrics["fallbacks"] == 1
    await client.client.close()


async def test_temporal_edge_and_generation_requests_never_route_to_jev(monkeypatch):
    client = build_llm_client(Settings(memory_node_resolution_provider="jev", typesafe_api_key="test"))
    client.node_router.resolve = AsyncMock(side_effect=AssertionError("must remain Graphiti LLM"))
    original = AsyncMock(return_value={})
    monkeypatch.setattr(BoundedReasoningClient, "generate_response", original)
    for name in ("extract_edges.extract_timestamps", "dedupe_edges.resolve_edge", "extract_nodes.extract_text", "extract_edges.edge", "unknown_future_prompt"):
        await client.generate_response([Message(role="user", content="test")], prompt_name=name)
    assert original.await_count == 5
    client.node_router.resolve.assert_not_awaited()
    await client.client.close()


async def test_missing_key_and_disabled_provider_use_original_llm(monkeypatch):
    original = AsyncMock(return_value={})
    monkeypatch.setattr(BoundedReasoningClient, "generate_response", original)
    for settings in (Settings(memory_node_resolution_provider="llm"), Settings(memory_node_resolution_provider="jev", typesafe_api_key=None)):
        client = build_llm_client(settings)
        await client.generate_response(prompt(), response_model=NodeResolutions, prompt_name="dedupe_nodes.nodes")
        await client.client.close()
    assert original.await_count == 2


async def test_cancellation_is_not_swallowed(monkeypatch):
    client = build_llm_client(Settings(memory_node_resolution_provider="jev", typesafe_api_key="test"))
    client.node_router.resolve = AsyncMock(side_effect=asyncio.CancelledError())
    original = AsyncMock()
    monkeypatch.setattr(BoundedReasoningClient, "generate_response", original)
    with pytest.raises(asyncio.CancelledError):
        await client.generate_response(prompt(), response_model=NodeResolutions, prompt_name="dedupe_nodes.nodes")
    original.assert_not_awaited()
    await client.client.close()
