from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
import fakeredis.aioredis
from gateway.app.decision import select_match
from gateway.app.enrichment import Extraction
from gateway.app.ingestion import Ingestor
from schemas.v1.metadata import MemoryCreate


def test_match_needs_unambiguous_candidate_and_confidence():
    options = [{"uuid": "a", "question_id": "a"}, {"uuid": "b", "question_id": "b"}]
    answers = {"a": {"probabilities": {"2": .99}, "confidence": .98}, "b": {"probabilities": {"0": .99}, "confidence": .98}}
    assert select_match(answers, options, .9) == ("a", False)
    answers["b"] = {"probabilities": {"1": .8}, "confidence": .8}
    assert select_match(answers, options, .9) == (None, True)
    answers["b"] = answers["a"]
    assert select_match(answers, options, .9) == (None, True)


def test_extraction_rejects_dangling_and_reversed_temporal_facts():
    data = {"entities": [{"name": "A", "type": "Project", "summary": "A"}], "facts": [{"source": 0, "target": 1, "relation": "USES", "fact": "A uses B"}]}
    with pytest.raises(ValueError, match="absent entity"):
        Extraction.model_validate(data)
    data["facts"][0].update(target=0, valid_from="2026-02-01T00:00:00Z", valid_to="2026-01-01T00:00:00Z")
    with pytest.raises(ValueError, match="reversed"):
        Extraction.model_validate(data)


async def test_accept_is_durable_without_wakeup_and_retries_are_idempotent():
    from gateway.app.policy import PolicyError
    stored = {}
    driver = SimpleNamespace(execute_query=AsyncMock())
    async def save(record, group, fingerprint):
        stored[record.memory_id] = record
        driver.execute_query.return_value = ([{"fingerprint": fingerprint}], None, None)
    store = SimpleNamespace(graphiti=SimpleNamespace(llm_client=None, driver=driver), settings=None, get=AsyncMock(side_effect=lambda key: stored.get(key)), save_canonical=AsyncMock(side_effect=save))
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    # fakeredis without Lua cannot emulate redis-py's distributed lock release.
    class Lock:
        async def acquire(self, blocking=False): return True
        async def release(self): pass
    redis.lock = lambda *args, **kwargs: Lock()
    ingestor = Ingestor(store, redis)
    memory = MemoryCreate(type="decision", content="Use A", scope={"project":"test"}, source={"agent":"codex"}, idempotency_key="same")
    first = await ingestor.accept(memory, "codex")
    second = await ingestor.accept(memory, "codex")
    assert first.memory_id == second.memory_id
    assert first.enrichment_status == "pending"
    assert store.save_canonical.await_count == 1
    assert await redis.zcard("memory:enrichment:wakeups") == 0
    with pytest.raises(PolicyError, match="different content"):
        await ingestor.accept(memory.model_copy(update={"content":"Use B"}), "codex")
    await redis.aclose()


async def test_saved_plan_replays_without_model_and_keeps_episode_identity():
    import json
    from schemas.v1.metadata import MemoryRecord
    record = MemoryRecord(type="decision", content="Use A", scope={"project":"test"}, source={"agent":"codex"})
    plan = {"status":"planned", "episode":{"uuid":"stable-episode"}, "metrics":{}}
    driver = SimpleNamespace(execute_query=AsyncMock(side_effect=[([{"id":str(record.memory_id), "plan":json.dumps(plan), "attempts":1}],None,None), ([],None,None), ([],None,None), ([],None,None)]))
    store = SimpleNamespace(graphiti=SimpleNamespace(llm_client=None, driver=driver), settings=SimpleNamespace(memory_enrichment_timeout_seconds=180), get=AsyncMock(return_value=record))
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    ingestor = Ingestor(store, redis)
    ingestor.enricher = SimpleNamespace(plan=AsyncMock(side_effect=AssertionError("must not rerun model")), write=AsyncMock())
    await ingestor.step()
    ingestor.enricher.plan.assert_not_awaited()
    assert ingestor.enricher.write.await_args.args[0] == plan
    final = driver.execute_query.await_args.kwargs
    assert final["status"] == "ready" and final["episode"] == "stable-episode"
    await redis.aclose()


async def test_glm_reserves_output_budget_and_uses_low_reasoning():
    from gateway.app.config import Settings
    from gateway.app.graph_store import build_llm_client
    from graphiti_core.prompts.models import Message
    llm = build_llm_client(Settings(memory_llm_model="glm-5.3-flash", memory_llm_api_key="test"))
    llm.client.with_options = MagicMock(return_value=llm.client)
    response = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"value":1}'))], usage=None)
    llm.client.chat.completions.create = AsyncMock(return_value=response)
    assert await llm._generate_response([Message(role="user", content="JSON")]) == {"value":1}
    kwargs = llm.client.chat.completions.create.await_args.kwargs
    assert kwargs["reasoning_effort"] == "low" and kwargs["max_tokens"] >= 16384
    response.choices[0].finish_reason = "length"
    with pytest.raises(ValueError, match="incomplete"):
        await llm._generate_response([Message(role="user", content="JSON")])
    await llm.client.close()


async def test_glm_retries_array_root_without_passing_it_to_graphiti():
    from gateway.app.config import Settings
    from gateway.app.graph_store import build_llm_client
    from graphiti_core.prompts.models import Message
    llm = build_llm_client(Settings(memory_llm_model="glm-5.3-flash", memory_llm_api_key="test"))
    llm.client.with_options = MagicMock(return_value=llm.client)
    def response(text):
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",message=SimpleNamespace(content=text))],usage=None)
    llm.client.chat.completions.create = AsyncMock(side_effect=[response('[]'),response('{"value":1}')])
    assert await llm._generate_response([Message(role="user",content="JSON")]) == {"value":1}
    assert llm.client.chat.completions.create.await_count == 2
    llm.client.chat.completions.create = AsyncMock(return_value=response('[]'))
    with pytest.raises(ValueError, match="wrong_root"):
        await llm._generate_response([Message(role="user",content="JSON")])
    assert llm.client.chat.completions.create.await_count == 2
    await llm.client.close()
