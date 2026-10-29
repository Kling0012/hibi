"""Acceptance and canonical fallback regressions without network services."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from gateway.app.graph_store import GraphStore, pending_search_terms
from gateway.app.ingestion import Ingestor
from gateway.app.policy import PolicyError, group_id
from gateway.app.service import MemoryService
from schemas.v1.metadata import MemoryCreate, MemoryRecord, MemorySearchRequest, SessionCommitRequest


def memory(content="Alpha Gateway", **kwargs):
    values = {"type": "decision", "content": content,
              "scope": {"user": "default", "project": "all"},
              "source": {"agent": "codex"}}
    values.update(kwargs)
    return MemoryCreate(**values)


class Lock:
    def __init__(self, acquired=True):
        self.acquired = acquired
        self.acquired_with = None

    async def acquire(self, blocking=None):
        self.acquired_with = blocking
        return self.acquired

    async def release(self):
        pass


async def test_accept_uses_only_canonical_store_and_short_lock_wait():
    lock = Lock()
    store = SimpleNamespace(
        settings=None,
        graphiti=SimpleNamespace(add_episode=AsyncMock(side_effect=AssertionError("Graphiti")),
                                 embedder=AsyncMock(side_effect=AssertionError("embedding")),
                                 llm_client=AsyncMock(side_effect=AssertionError("LLM"))),
        get=AsyncMock(return_value=None), save_canonical=AsyncMock(),
    )
    lock_options = {}
    def make_lock(*args, **kwargs):
        lock_options.update(kwargs)
        return lock
    redis = SimpleNamespace(lock=make_lock)
    ingestor = Ingestor(store, redis)
    record = await ingestor.accept(memory(), "codex")
    assert record.enrichment_status == "pending"
    assert lock_options["blocking_timeout"] == 5
    assert lock.acquired_with is None
    store.save_canonical.assert_awaited_once()
    store.graphiti.add_episode.assert_not_awaited()
    assert not hasattr(redis, "zadd")


async def test_accept_fails_only_after_lock_wait_expires():
    lock = Lock(False)
    store = SimpleNamespace(settings=None, graphiti=SimpleNamespace(),
                            get=AsyncMock(), save_canonical=AsyncMock())
    lock_options = {}
    def make_lock(*args, **kwargs):
        lock_options.update(kwargs)
        return lock
    ingestor = Ingestor(store, SimpleNamespace(lock=make_lock))
    with pytest.raises(PolicyError, match="busy"):
        await ingestor.accept(memory(), "codex")
    assert lock_options["blocking_timeout"] == 5
    store.get.assert_not_awaited()


async def test_ten_concurrent_accepts_all_succeed_with_short_lock_wait():
    mutex = asyncio.Lock()
    records = []

    class WaitingLock:
        async def acquire(self):
            await asyncio.wait_for(mutex.acquire(), timeout=5)
            return True

        async def release(self):
            mutex.release()

    def make_lock(*args, **kwargs):
        assert kwargs["blocking_timeout"] == 5
        return WaitingLock()

    async def save(record, group, fingerprint):
        await asyncio.sleep(.002)
        records.append(record)

    store = SimpleNamespace(settings=None, graphiti=SimpleNamespace(),
                            get=AsyncMock(return_value=None),
                            save_canonical=AsyncMock(side_effect=save))
    ingestor = Ingestor(store, SimpleNamespace(lock=make_lock))
    accepted = await asyncio.gather(*(ingestor.accept(memory(f"Concurrent {i}"), "codex")
                                      for i in range(10)))
    assert len({record.memory_id for record in accepted}) == 10
    assert len(records) == 10
    assert all(record.enrichment_status == "pending" for record in accepted)


async def test_write_responses_explain_acceptance_and_preserve_existing_fields():
    candidate = memory()
    record = MemoryRecord(**candidate.model_dump(), enrichment_status="pending")
    graph = SimpleNamespace(remember=AsyncMock(return_value=record),
                            supersede=AsyncMock(return_value=record))
    sessions = SimpleNamespace(candidates=AsyncMock(return_value={"one": candidate}),
                               clear_candidates=AsyncMock())
    service = MemoryService(graph, sessions)
    remembered = await service.remember(candidate)
    superseded = await service.supersede(uuid4(), candidate)
    committed = await service.session_commit(SessionCommitRequest(session_id="test"))
    for item in (remembered, superseded, committed["committed"][0]):
        assert item["memory_id"] == str(record.memory_id)
        assert item["enrichment_status"] == "pending"
        assert "memory_get" in item["acceptance"]
        assert "Continue without waiting" in item["acceptance"]


@pytest.mark.parametrize("status", ["pending", "processing", "failed", "uncertain"])
async def test_unenriched_records_are_searchable_with_filters_and_status(status):
    good = MemoryRecord(**memory("Alpha Gateway", tags=["ingest"]).model_dump(),
                        enrichment_status=status)
    hidden = MemoryRecord(**memory("Alpha hidden", scope={"project": "private"}).model_dump(),
                          enrichment_status=status)
    old = MemoryRecord(**memory("Alpha old").model_dump(), enrichment_status=status,
                       valid_to="2026-09-24T00:00:00Z")
    wrong = MemoryRecord(**memory("Alpha wrong", type="preference").model_dump(),
                         enrichment_status=status)
    records = [good, hidden, old, wrong]

    async def query(statement, **params):
        assert "r.group_id IN $groups" in statement
        assert "r.enrichment_status IN" in statement
        assert "r.type IN $types" in statement
        assert "toLower(r.content) CONTAINS $term_0" in statement
        assert "toLower(r.name) CONTAINS $term_0" in statement
        assert "toLower(r.tags_json) CONTAINS $term_0" in statement
        assert params["term_0"] == "alpha"
        selected = [r for r in records if group_id(r.scope, r.visibility) in params["groups"]
                    and r.type.value in params["types"]
                    and ("r.valid_to = ''" not in statement or r.valid_to is None)]
        return selected, None, None

    store = GraphStore.__new__(GraphStore)
    store.ingestor = object()
    store.settings = SimpleNamespace(memory_search_rerank_mode="off")
    store.graphiti = SimpleNamespace(search=AsyncMock(return_value=[]),
                                    driver=SimpleNamespace(execute_query=AsyncMock(side_effect=query)))
    store._record_from_row = lambda row: row
    request = MemorySearchRequest(query="Alpha", user="default", project="all", agent="codex",
                                  types=["decision"])
    found = await store.search(request)
    assert [r["memory"]["memory_id"] for r in found] == [str(good.memory_id)]
    assert found[0]["enrichment_status"] == status
    assert found[0]["matched_fact"] is None
    found_history = await store.search(request.model_copy(update={"include_history": True}))
    assert {r["memory"]["memory_id"] for r in found_history} == {str(good.memory_id), str(old.memory_id)}


async def test_ready_rank_is_preserved_and_canonical_fallback_deduplicates():
    ready = MemoryRecord(**memory("Alpha ready").model_dump(), episode_uuid="ready-episode")
    pending = MemoryRecord(**memory("Alpha pending").model_dump(), enrichment_status="pending")
    edge = SimpleNamespace(episodes=["ready-episode"], fact="Alpha ready fact",
                           valid_at=None, invalid_at=None)
    async def query(statement, **params):
        if "r.episode_uuid IN $episodes" in statement:
            return [ready], None, None
        # A status transition or an unusual duplicate must not duplicate a memory_id.
        return [pending, ready], None, None
    store = GraphStore.__new__(GraphStore)
    store.ingestor = object()
    store.settings = SimpleNamespace(memory_search_rerank_mode="off")
    store.graphiti = SimpleNamespace(search=AsyncMock(return_value=[edge]),
                                    driver=SimpleNamespace(execute_query=AsyncMock(side_effect=query)))
    store._record_from_row = lambda row: row
    result = await store.search(MemorySearchRequest(query="Alpha", user="default", project="all",
                                                    agent="codex", limit=3))
    assert [item["memory"]["memory_id"] for item in result] == [str(ready.memory_id), str(pending.memory_id)]
    assert result[0]["matched_fact"] == "Alpha ready fact"


async def test_strong_pending_match_gets_tail_slot_when_ready_fills_limit():
    ready = [MemoryRecord(**memory(f"ready {i}").model_dump(), episode_uuid=f"ep-{i}")
             for i in range(4)]
    weak = MemoryRecord(**memory("Alpha only").model_dump(), enrichment_status="pending")
    strong = MemoryRecord(**memory("Alpha beta gamma").model_dump(), enrichment_status="processing")
    candidates = [weak, strong]
    edges = [SimpleNamespace(episodes=[item.episode_uuid], fact=f"fact {i}",
                             valid_at=None, invalid_at=None) for i, item in enumerate(ready)]

    async def query(statement, **params):
        if "r.episode_uuid IN $episodes" in statement:
            return ready, None, None
        return candidates, None, None

    store = GraphStore.__new__(GraphStore)
    store.ingestor = object()
    store.settings = SimpleNamespace(memory_search_rerank_mode="off")
    store.graphiti = SimpleNamespace(search=AsyncMock(return_value=edges),
                                    driver=SimpleNamespace(execute_query=AsyncMock(side_effect=query)))
    store._record_from_row = lambda row: row
    request = MemorySearchRequest(query="Alpha beta gamma", user="default", project="all",
                                  agent="codex", limit=4)
    result = await store.search(request)
    assert [item["memory"]["memory_id"] for item in result] == [
        *(str(item.memory_id) for item in ready[:3]), str(strong.memory_id),
    ]
    assert result[-1]["enrichment_status"] == "processing"
    assert len(result) == request.limit

    # Without a strong match, the ready results retain all four slots.
    candidates[:] = [weak]
    without_strong = await store.search(request)
    assert [item["memory"]["memory_id"] for item in without_strong] == [
        str(item.memory_id) for item in ready
    ]


async def test_japanese_sentence_finds_shorter_pending_record():
    query_text = "Claude Codeは会話役でCodexに実装を委任する"
    terms = pending_search_terms(query_text)
    assert len(terms) <= 16
    assert "実装" in terms and "委任" in terms
    pending = MemoryRecord(**memory("Codexに実装を委任する").model_dump(),
                           enrichment_status="pending")
    haystack = pending.content.casefold()
    assert sum(term in haystack for term in terms) >= (len(terms) + 1) // 2

    async def query(statement, **params):
        assert "r.enrichment_status IN" in statement
        assert any(value in haystack for key, value in params.items() if key.startswith("term_"))
        return [pending], None, None

    store = GraphStore.__new__(GraphStore)
    store.ingestor = object()
    store.settings = SimpleNamespace(memory_search_rerank_mode="off")
    store.graphiti = SimpleNamespace(search=AsyncMock(return_value=[]),
                                    driver=SimpleNamespace(execute_query=AsyncMock(side_effect=query)))
    store._record_from_row = lambda row: row
    result = await store.search(MemorySearchRequest(query=query_text, user="default",
                                                    project="all", agent="codex", limit=1))
    assert result[0]["memory"]["memory_id"] == str(pending.memory_id)
