import copy

import httpx
import pytest

from gateway.app.config import Settings
from gateway.app.search_rerank import SearchReranker


def settings(**overrides):
    values = {
        "typesafe_api_key": "offline-test-only",
        "memory_typesafe_model": "jev-1.13.0",
        "memory_jev_timeout_seconds": 5,
        "memory_jev_max_concurrency": 3,
        "memory_jev_cache_max_entries": 32,
        "memory_jev_cache_ttl_seconds": 300,
        "memory_jev_max_request_bytes": 96000,
        "memory_jev_max_estimated_tokens": 30000,
        "memory_jev_max_batches": 6,
        "memory_jev_max_total_request_bytes": 160000,
        "memory_search_rerank_batch_size": 20,
        "memory_search_rerank_timeout_seconds": 5,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def results():
    return [{
        "memory": {
            "memory_id": f"memory-{index}", "type": "stable_fact", "name": f"M{index}",
            "content": text, "valid_from": "2026-09-19T00:00:00Z", "valid_to": None,
        },
        "matched_fact": text,
    } for index, text in enumerate(("似た語だけ", "質問への直接の答え", "別ホスト"))]


async def test_noul_rerank_preserves_candidates_and_changes_only_order(monkeypatch):
    calls = 0

    async def post(client, url, **kwargs):
        nonlocal calls
        calls += 1
        scores = {"candidate_0": .2, "candidate_1": .95, "candidate_2": .1}
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "answers": {key: {"type": "noul", "noul": scores[key]}
                        for key in kwargs["json"]["questions"]}
        })

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    reranker = SearchReranker(settings())
    original = results()
    ranked = await reranker.rank("正しい設定は?", copy.deepcopy(original))
    assert [row["memory"]["memory_id"] for row in ranked] == [
        "memory-1", "memory-0", "memory-2"
    ]
    assert {row["memory"]["memory_id"] for row in ranked} == {
        row["memory"]["memory_id"] for row in original
    }
    assert calls == 1
    assert reranker.metrics["top_changed"] == 1


async def test_adapter_backend_uses_compatible_endpoint(monkeypatch):
    urls = []

    async def post(client, url, **kwargs):
        urls.append(url)
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "answers": {key: {"type": "noul", "noul": .5}
                        for key in kwargs["json"]["questions"]}
        })

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    reranker = SearchReranker(settings(
        memory_decision_backend="adapter",
        memory_decision_adapter_base_url="http://adapter.test",
        memory_decision_adapter_api_key="offline-adapter",
        memory_decision_adapter_model="test-model",
    ))
    await reranker.rank("query", results())
    assert urls == ["http://adapter.test/v1/systemone"]


async def test_invalid_noul_is_not_cached(monkeypatch):
    calls = 0

    async def post(client, url, **kwargs):
        nonlocal calls
        calls += 1
        value = 2 if calls == 1 else .5
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "answers": {key: {"type": "noul", "noul": value}
                        for key in kwargs["json"]["questions"]}
        })

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    reranker = SearchReranker(settings())
    with pytest.raises(ValueError):
        await reranker.rank("query", results())
    await reranker.rank("query", results())
    assert calls == 2
    assert reranker.metrics["cache_hits"] == 0
