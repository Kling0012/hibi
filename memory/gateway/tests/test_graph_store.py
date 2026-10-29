from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from gateway.app.config import Settings
from gateway.app.graph_store import (
    GraphStore,
    build_cloudflare_embedder_config,
    build_embedder_config,
    build_llm_client,
    build_llm_config,
    effective_fact_valid_to,
    normalize_graph_attributes,
    validate_supersede,
)
from gateway.app.policy import PolicyError
from schemas.v1.metadata import MemoryCreate, MemoryRecord


def test_llm_and_embedding_credentials_are_routed_to_their_own_clients():
    settings = Settings(
        memory_llm_api_key="provider-key",
        openai_api_key="fallback-key",
        memory_llm_base_url="https://provider.example/v1",
        memory_embedding_api_key="embedding-key",
        memory_embedding_base_url="http://127.0.0.1:8081/v1",
    )

    llm = build_llm_config(settings)
    embedder = build_embedder_config(settings)

    assert llm.api_key == "provider-key"
    assert llm.base_url == "https://provider.example/v1"
    assert embedder.api_key == "embedding-key"
    assert embedder.base_url == "http://127.0.0.1:8081/v1"


def test_openai_provider_does_not_redirect_local_embeddings():
    settings = Settings(
        memory_llm_provider="openai",
        memory_llm_api_key="provider-key",
        memory_embedding_api_key="embedding-key",
        memory_embedding_base_url="http://127.0.0.1:8081/v1",
    )

    assert build_llm_config(settings).base_url is None
    assert build_embedder_config(settings).base_url == "http://127.0.0.1:8081/v1"


def test_cloudflare_embedding_config_uses_workers_ai_openai_endpoint():
    settings = Settings(
        cloudflare_account_id="account-id",
        cloudflare_api_token="cloudflare-key",
    )

    embedder = build_cloudflare_embedder_config(settings)

    assert embedder.api_key == "cloudflare-key"
    assert embedder.base_url == (
        "https://api.cloudflare.com/client/v4/accounts/account-id/ai/v1"
    )
    assert embedder.embedding_model == "@cf/qwen/qwen3-embedding-0.6b"


def test_json_object_fallback_is_passed_to_generic_client():
    settings = Settings(
        memory_llm_api_key="provider-key",
        memory_llm_structured_output_mode="json_object",
    )

    assert build_llm_client(settings).structured_output_mode == "json_object"


def test_nested_graph_attributes_are_preserved_as_json_strings():
    normalized = normalize_graph_attributes({
        "evidence": {"title": "AmberDB", "description": "evaluation database"},
        "tags": ["evaluation", "canary"],
        "score": 1.0,
    })

    assert normalized == {
        "evidence": '{"description": "evaluation database", "title": "AmberDB"}',
        "tags": ["evaluation", "canary"],
        "score": 1.0,
    }


def test_supersede_rejects_scope_changes():
    old = MemoryRecord(
        type="stable_fact", content="old", scope={"project": "a"},
        source={"agent": "codex"}, valid_from="2026-01-01T00:00:00Z",
    )
    replacement = MemoryCreate(
        type="stable_fact", content="new", scope={"project": "b"},
        source={"agent": "codex"}, valid_from="2026-02-01T00:00:00Z",
    )

    with pytest.raises(PolicyError, match="same scope"):
        validate_supersede(old, replacement, "codex")


def test_canonical_valid_to_fills_missing_graph_fact_boundary():
    boundary = datetime(2026, 9, 1, tzinfo=timezone.utc)

    assert effective_fact_valid_to(None, boundary) == "2026-09-01T00:00:00+00:00"


async def test_supersede_uses_replacement_valid_from_as_temporal_boundary():
    old = MemoryRecord(
        type="stable_fact", content="old", scope={"project": "a"},
        source={"agent": "codex"}, valid_from="2026-01-01T00:00:00Z",
    )
    replacement = MemoryCreate(
        type="stable_fact", content="new", scope={"project": "a"},
        source={"agent": "codex"}, valid_from="2026-02-01T00:00:00Z",
    )
    new = MemoryRecord(**replacement.model_dump(), supersedes=old.memory_id)
    store = GraphStore.__new__(GraphStore)
    store.get = AsyncMock(return_value=old)
    store.remember = AsyncMock(return_value=new)
    store.graphiti = type("Graph", (), {"driver": type("Driver", (), {})()})()
    store.graphiti.driver.execute_query = AsyncMock()

    result = await store.supersede(old.memory_id, replacement, actor_agent="codex")

    assert result == new
    kwargs = store.graphiti.driver.execute_query.await_args.kwargs
    assert kwargs["boundary"] == "2026-02-01T00:00:00+00:00"
