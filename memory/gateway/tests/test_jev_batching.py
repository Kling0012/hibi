"""Offline behavioral contracts for bounded Jev batching and raw-answer caching.

Every HTTP client in this module is replaced with an in-memory double. These
tests never send memories to Jev, write a graph, or need provider credentials.
"""
import asyncio
import copy
import time

import httpx
import pytest
from graphiti_core.prompts import dedupe_nodes
from graphiti_core.prompts.dedupe_nodes import NodeResolutions

from gateway.app import decision_batch
from gateway.app.config import Settings
from gateway.app.jev_router import JevNodeRouter, node_context
from gateway.app.jev_router import JevNodeRoutingMixin


@pytest.fixture(autouse=True)
def offline_http(monkeypatch):
    class OfflineClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            raise AssertionError("This test must explicitly install a fake response")

    monkeypatch.setattr(httpx, "AsyncClient", OfflineClient)


def settings(**overrides):
    values = {
        "typesafe_api_key": "offline-test-only",
        "memory_typesafe_model": "jev-1.13.0",
        "memory_decision_confidence": .9,
        "memory_jev_timeout_seconds": 5,
        "memory_jev_batch_size": 40,
        "memory_jev_max_concurrency": 3,
        "memory_jev_cache_max_entries": 256,
        "memory_jev_cache_ttl_seconds": 300,
        "memory_jev_max_pairs": 120,
        "memory_jev_max_context_chars": 24000,
        "memory_jev_max_request_bytes": 96000,
        "memory_jev_max_batches": 6,
        "memory_jev_max_total_request_bytes": 160000,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def prompt(count=1, *, evidence="本番サーバーの設定を確認した。", previous="旧設定", summary="記憶"):
    return dedupe_nodes.nodes({
        "previous_episodes": [{"content": previous, "timestamp": "2026-09-18"}],
        "episode_content": evidence,
        "extracted_nodes": [{"id": 0, "name": "Cedar", "entity_type": ["System"]}],
        "existing_nodes": [
            {"candidate_id": i, "name": f"Candidate-{i}", "summary": summary,
             "entity_types": ["System"]}
            for i in range(count)
        ],
    })


def answer(level=0, probability=1.0):
    probabilities = {str(i): 0.0 for i in range(3)}
    probabilities[str(level)] = probability
    probabilities["1"] += 1.0 - probability
    return {"type": "score", "probabilities": probabilities,
            "confidence": probability, "score": sum(int(k) * v for k, v in probabilities.items())}


def response(url, payload, *, value=None):
    return httpx.Response(200, request=httpx.Request("POST", url), json={
        "model": payload["model"],
        "answers": {key: copy.deepcopy(value or answer()) for key in payload["questions"]},
        "usage": {"input_tokens": 11, "output_tokens": 3},
    })


def install_success(monkeypatch, *, value=None):
    sent = []

    async def post(client, url, **kwargs):
        sent.append(copy.deepcopy(kwargs["json"]))
        return response(url, kwargs["json"], value=value)

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    return sent


def reason_count(router):
    return sum(value for key, value in router.metrics.items() if key.startswith("reason_"))


async def test_batches_preserve_all_evidence_candidate_coverage_and_input(monkeypatch):
    sent = install_success(monkeypatch)
    router = JevNodeRouter(settings(memory_jev_batch_size=2))
    messages = prompt(5)
    before = copy.deepcopy(messages)
    result = await router.resolve(messages)

    assert messages == before
    assert len(sent) == 3
    assert sorted(len(payload["questions"]) for payload in sent) == [1, 2, 2]
    assert all(payload["state"] == node_context(messages) for payload in sent)
    keys = [key for payload in sent for key in payload["questions"]]
    assert sorted(keys) == [f"pair_0_{i}" for i in range(5)]
    assert NodeResolutions.model_validate(result).entity_resolutions[0].duplicate_candidate_id == -1
    assert router.metrics["jev_calls"] == 3


async def test_global_concurrency_limit_spans_simultaneous_resolves(monkeypatch):
    active = peak = calls = 0
    saturated, release = asyncio.Event(), asyncio.Event()

    async def post(client, url, **kwargs):
        nonlocal active, peak, calls
        calls += 1
        active += 1
        peak = max(peak, active)
        if active == 3:
            saturated.set()
        try:
            await release.wait()
            return response(url, kwargs["json"])
        finally:
            active -= 1

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    router = JevNodeRouter(settings(memory_jev_batch_size=1))
    tasks = [asyncio.create_task(router.resolve(prompt(6, evidence=f"Host-{i}"))) for i in range(2)]
    try:
        await asyncio.wait_for(saturated.wait(), 2)
        for _ in range(20):
            await asyncio.sleep(0)
        assert active == peak == 3
    finally:
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
    assert not any(isinstance(result, BaseException) for result in results)
    assert calls == 12
    assert active == 0
    assert peak == 3


async def test_identical_concurrent_resolves_share_inflight_batches(monkeypatch):
    calls = 0
    started, release = asyncio.Event(), asyncio.Event()

    async def post(client, url, **kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return response(url, kwargs["json"])

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    router = JevNodeRouter(settings(memory_jev_batch_size=1))
    first = asyncio.create_task(router.resolve(prompt(3)))
    await asyncio.wait_for(started.wait(), 2)
    second = asyncio.create_task(router.resolve(prompt(3)))
    try:
        for _ in range(20):
            await asyncio.sleep(0)
    finally:
        release.set()
        results = await asyncio.gather(first, second)
    assert results[0] == results[1]
    assert calls == router.metrics["jev_calls"] == 3
    assert router.metrics["coalesced"] == 3


async def test_cached_answers_do_not_count_tokens_or_network_calls_twice(monkeypatch):
    sent = install_success(monkeypatch)
    router = JevNodeRouter(settings())
    first = await router.resolve(prompt())
    second = await router.resolve(prompt())
    assert first == second
    assert len(sent) == router.metrics["jev_calls"] == 1
    assert router.metrics["cache_misses"] == 1
    assert router.metrics["cache_hits"] == 1
    assert router.metrics["input_tokens"] == 11
    assert router.metrics["output_tokens"] == 3


@pytest.mark.parametrize("changed", ["current", "previous", "candidate", "model"])
async def test_changed_evidence_or_model_cannot_reuse_cached_decision(monkeypatch, changed):
    sent = install_success(monkeypatch)
    router = JevNodeRouter(settings())
    await router.resolve(prompt())
    kwargs = {}
    if changed == "current":
        kwargs["evidence"] = "別のSSH先にあるテスト環境の設定。"
    elif changed == "previous":
        kwargs["previous"] = "新しい証拠: 同名だが別物。"
    elif changed == "candidate":
        kwargs["summary"] = "記憶が更新され、この候補は別ホストを指す。"
    else:
        router.settings.memory_typesafe_model = "jev-1.14.0"
    await router.resolve(prompt(**kwargs))
    assert len(sent) == 2
    assert router.metrics["cache_hits"] == 0


async def test_changed_question_batch_cannot_reuse_completed_request(monkeypatch):
    sent = install_success(monkeypatch)
    router = JevNodeRouter(settings(memory_jev_batch_size=2))
    await router.resolve(prompt(2))
    router.settings.memory_jev_batch_size = 1
    await router.resolve(prompt(2))
    assert len(sent) == 3
    assert router.metrics["cache_hits"] == 0


async def test_cached_raw_probabilities_are_reclassified_with_new_threshold(monkeypatch):
    sent = install_success(monkeypatch, value=answer(2, .95))
    router = JevNodeRouter(settings())
    accepted = await router.resolve(prompt())
    assert accepted["entity_resolutions"][0]["duplicate_candidate_id"] == 0
    router.settings.memory_decision_confidence = .99
    with pytest.raises(ValueError):
        await router.resolve(prompt())
    router.settings.memory_decision_confidence = .9
    assert await router.resolve(prompt()) == accepted
    assert len(sent) == 1
    assert router.metrics["cache_hits"] == 2
    assert reason_count(router) >= 1


@pytest.mark.parametrize("model", ["jev-latest", "jev-preview"])
async def test_unpinned_model_aliases_disable_completed_result_cache(monkeypatch, model):
    sent = install_success(monkeypatch)
    router = JevNodeRouter(settings(memory_typesafe_model=model))
    await router.resolve(prompt())
    await router.resolve(prompt())
    assert len(sent) == 2
    assert router.metrics["cache_hits"] == 0


async def test_zero_cache_capacity_disables_completed_result_cache(monkeypatch):
    sent = install_success(monkeypatch)
    router = JevNodeRouter(settings(memory_jev_cache_max_entries=0))
    await router.resolve(prompt())
    await router.resolve(prompt())
    assert len(sent) == 2
    assert not router.runner.cache


async def test_cache_is_bounded_and_recently_used_entry_survives(monkeypatch):
    sent = install_success(monkeypatch)
    router = JevNodeRouter(settings(memory_jev_cache_max_entries=2))
    for evidence in ("A", "B", "A", "C", "A", "B"):
        await router.resolve(prompt(evidence=evidence))
        assert len(router.runner.cache) <= 2
    assert len(sent) == 4
    assert router.metrics["cache_hits"] == 2
    assert router.metrics["cache_evictions"] >= 1


async def test_cache_ttl_expires_without_patching_event_loop_clock(monkeypatch):
    sent = install_success(monkeypatch)
    offset = 0

    class Clock:
        def monotonic(self):
            return time.monotonic() + offset

        def __getattr__(self, name):
            return getattr(time, name)

    monkeypatch.setattr(decision_batch, "time", Clock())
    router = JevNodeRouter(settings(memory_jev_cache_ttl_seconds=1))
    await router.resolve(prompt())
    await router.resolve(prompt())
    offset = 2
    await router.resolve(prompt())
    assert len(sent) == 2
    assert router.metrics["cache_hits"] == 1
    assert router.metrics["cache_expired"] >= 1


@pytest.mark.parametrize("failure", ["http", "missing", "extra", "nan", "bad_json"])
async def test_failed_or_malformed_batch_is_not_cached(monkeypatch, failure):
    calls = 0

    async def post(client, url, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            return response(url, kwargs["json"])
        request = httpx.Request("POST", url)
        if failure == "http":
            return httpx.Response(503, request=request)
        if failure == "bad_json":
            return httpx.Response(200, request=request, text="not-json")
        values = {key: answer() for key in kwargs["json"]["questions"]}
        if failure == "missing":
            values.pop(next(iter(values)))
        elif failure == "extra":
            values["unrequested_pair"] = answer()
        else:
            values[next(iter(values))]["confidence"] = "NaN"
        return httpx.Response(200, request=request, json={"answers": values})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    router = JevNodeRouter(settings())
    with pytest.raises((httpx.HTTPError, ValueError, KeyError, TypeError)):
        await router.resolve(prompt())
    assert reason_count(router) >= 1
    await router.resolve(prompt())
    assert calls == 2
    assert router.metrics["cache_hits"] == 0


@pytest.mark.parametrize("field", ["confidence", "probability"])
async def test_huge_json_integer_is_invalid_response_not_uncaught_overflow(monkeypatch, field):
    calls = 0

    async def post(client, url, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            return response(url, kwargs["json"])
        malformed = answer()
        if field == "confidence":
            malformed["confidence"] = 10 ** 400
        else:
            malformed["probabilities"]["2"] = 10 ** 400
        return response(url, kwargs["json"], value=malformed)

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    router = JevNodeRouter(settings())
    with pytest.raises(ValueError):
        await router.resolve(prompt())
    assert router.metrics["reason_invalid_response"] == 1
    assert not router.runner.cache
    await router.resolve(prompt())
    assert calls == 2
    assert router.metrics["cache_hits"] == 0


@pytest.mark.parametrize("budget", ["pairs", "context", "utf8_request"])
async def test_excess_budget_fails_before_any_call_without_truncating(monkeypatch, budget):
    sent = install_success(monkeypatch)
    overrides = {}
    messages = prompt(121)
    if budget == "context":
        overrides["memory_jev_max_context_chars"] = 1000
        messages = prompt(evidence="証拠" * 1000)
    elif budget == "utf8_request":
        overrides["memory_jev_max_request_bytes"] = 2048
        messages = prompt(evidence="証" * 1000)
    before = copy.deepcopy(messages)
    router = JevNodeRouter(settings(**overrides))
    with pytest.raises(ValueError):
        await router.resolve(messages)
    assert sent == []
    assert messages == before
    assert reason_count(router) >= 1


async def test_long_multibyte_state_cannot_multiply_into_unbounded_http_batches(monkeypatch):
    sent = install_success(monkeypatch)
    messages = dedupe_nodes.nodes({
        "previous_episodes": [],
        "episode_content": "記" * 9000,
        "extracted_nodes": [{"id": i, "name": f"Incoming{i}"} for i in range(4)],
        "existing_nodes": [{"candidate_id": i, "name": f"Candidate{i}"} for i in range(30)],
    })
    before = copy.deepcopy(messages)
    router = JevNodeRouter(settings())
    with pytest.raises(ValueError):
        await router.resolve(messages)
    assert sent == []
    assert messages == before
    assert router.metrics["candidate_pairs"] == 120
    assert router.metrics["reason_batch_budget"] == 1


async def test_batch_failure_cancels_and_drains_other_owned_requests(monkeypatch):
    active = calls = cancelled = 0
    all_started = asyncio.Event()

    async def post(client, url, **kwargs):
        nonlocal active, calls, cancelled
        calls += 1
        active += 1
        if calls == 3:
            all_started.set()
        try:
            await all_started.wait()
            if "pair_0_0" in kwargs["json"]["questions"]:
                return httpx.Response(503, request=httpx.Request("POST", url))
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled += 1
            raise
        finally:
            active -= 1

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    router = JevNodeRouter(settings(memory_jev_batch_size=1))
    with pytest.raises(httpx.HTTPError):
        await asyncio.wait_for(router.resolve(prompt(3)), 2)
    assert active == 0
    assert calls == 3
    assert cancelled == 2
    assert not router.runner.flights


async def test_total_deadline_covers_queued_batches_and_leaves_no_background_calls(monkeypatch):
    calls = active = 0

    async def post(client, url, **kwargs):
        nonlocal calls, active
        calls += 1
        active += 1
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    router = JevNodeRouter(settings(memory_jev_batch_size=1, memory_jev_max_concurrency=1,
                                    memory_jev_timeout_seconds=.05))
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(router.resolve(prompt(5)), 2)
    assert calls == 1
    assert active == 0
    assert not router.runner.flights
    assert reason_count(router) >= 1


async def test_explicit_disjoint_types_are_filtered_without_model_call(monkeypatch):
    sent = install_success(monkeypatch)
    messages = dedupe_nodes.nodes({
        "previous_episodes": [],
        "episode_content": "同名候補だが型が異なる。",
        "extracted_nodes": [{"id": 0, "name": "Cedar", "entity_type": ["System"]}],
        "existing_nodes": [{"candidate_id": 0, "name": "Cedar", "entity_types": ["Person"]}],
    })
    router = JevNodeRouter(settings())
    result = await router.resolve(messages)
    assert result["entity_resolutions"][0]["duplicate_candidate_id"] == -1
    assert sent == []
    assert router.metrics["deterministic_pairs"] == 1


async def test_partial_fallback_sends_only_unresolved_entity_to_llm(monkeypatch):
    async def post(client, url, **kwargs):
        values = {}
        for key in kwargs["json"]["questions"]:
            values[key] = answer(0) if key.startswith("pair_0_") else answer(1)
        return httpx.Response(200, request=httpx.Request("POST", url),
                              json={"answers": values})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    messages = dedupe_nodes.nodes({
        "previous_episodes": [],
        "episode_content": "二つの対象を照合する。",
        "extracted_nodes": [{"id": 0, "name": "Alpha"}, {"id": 1, "name": "Beta"}],
        "existing_nodes": [{"candidate_id": 0, "name": "Candidate"}],
    })

    class FallbackClient:
        def __init__(self):
            self.calls = []

        async def generate_response(self, messages, **kwargs):
            self.calls.append(messages)
            assert len(node_context(messages)["incoming"]) == 1
            return {"entity_resolutions": [
                {"id": 0, "name": "Beta", "duplicate_candidate_id": 0}
            ]}

    class PartialClient(JevNodeRoutingMixin, FallbackClient):
        pass

    client = PartialClient()
    client.node_router = JevNodeRouter(settings(memory_node_partial_fallback_mode="on"))
    result = await client.generate_response(
        messages, response_model=NodeResolutions, prompt_name="dedupe_nodes.nodes",
    )
    assert [row["duplicate_candidate_id"] for row in result["entity_resolutions"]] == [-1, 0]
    assert len(client.calls) == 1
    assert client.node_router.metrics["partial_fallbacks"] == 1


async def test_deadline_is_not_restarted_after_each_successful_batch(monkeypatch):
    calls = active = 0

    async def post(client, url, **kwargs):
        nonlocal calls, active
        calls += 1
        active += 1
        try:
            await asyncio.sleep(.04)
            return response(url, kwargs["json"])
        finally:
            active -= 1

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    router = JevNodeRouter(settings(memory_jev_batch_size=1, memory_jev_max_concurrency=1,
                                    memory_jev_timeout_seconds=.09))
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(router.resolve(prompt(5)), 2)
    assert 1 <= calls < 5
    assert active == 0
    assert not router.runner.flights


async def test_cancelling_one_coalesced_caller_preserves_other_caller(monkeypatch):
    calls = 0
    started, release = asyncio.Event(), asyncio.Event()

    async def post(client, url, **kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return response(url, kwargs["json"])

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    router = JevNodeRouter(settings())
    first = asyncio.create_task(router.resolve(prompt()))
    await asyncio.wait_for(started.wait(), 2)
    second = asyncio.create_task(router.resolve(prompt()))
    try:
        for _ in range(20):
            await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
    finally:
        release.set()
        result = await second
    assert result["entity_resolutions"][0]["duplicate_candidate_id"] == -1
    assert calls == 1
    assert not router.runner.flights


async def test_cancelling_only_caller_is_not_swallowed_and_drains_network(monkeypatch):
    active = 0
    started = asyncio.Event()

    async def post(client, url, **kwargs):
        nonlocal active
        active += 1
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    router = JevNodeRouter(settings())
    task = asyncio.create_task(router.resolve(prompt()))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert active == 0
    assert not router.runner.flights
    assert not router.runner.cache


async def test_no_candidates_resolves_locally_without_judgment(monkeypatch):
    sent = install_success(monkeypatch)
    router = JevNodeRouter(settings())
    result = await router.resolve(prompt(0))
    assert result["entity_resolutions"][0]["duplicate_candidate_id"] == -1
    assert sent == []
