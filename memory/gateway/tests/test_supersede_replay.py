"""Reproduce supersede replays through the real canonical acceptance path."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from gateway.app.graph_store import GraphStore
from gateway.app.ingestion import Ingestor
from schemas.v1.metadata import MemoryCreate, MemoryRecord


@pytest.mark.parametrize("status", ["pending", "processing", "failed", "uncertain", "ready"])
async def test_supersede_replay_closes_original_only_when_replacement_is_ready(status):
    old = MemoryRecord(
        type="stable_fact", content="original", scope={"project": "replay-test"},
        source={"agent": "repro"}, valid_from="2026-01-01T00:00:00Z",
    )
    replacement = MemoryCreate(
        type="stable_fact", content="replacement", scope={"project": "replay-test"},
        source={"agent": "repro"}, valid_from="2026-02-01T00:00:00Z",
        idempotency_key="supersede-replay",
    )
    records = {old.memory_id: old}
    fingerprints = {}
    original = old.model_dump()

    async def get(memory_id):
        return records.get(memory_id)

    async def save(record, group, fingerprint):
        records[record.memory_id] = record
        fingerprints[record.memory_id] = fingerprint

    async def execute_query(query, **kwargs):
        if "RETURN r.fingerprint AS fingerprint" in query:
            return ([{"fingerprint": fingerprints[UUID(kwargs["id"])]}], None, None)
        # Model the current finalizer's canonical write without a live database.
        if "SET old.valid_to = $boundary, old.superseded_by = $new_id" in query:
            previous = records[UUID(kwargs["old_id"])]
            previous.valid_to = datetime.fromisoformat(kwargs["boundary"])
            previous.superseded_by = UUID(kwargs["new_id"])
            return ([], None, None)
        raise AssertionError(f"unexpected database query: {query}")

    store = GraphStore.__new__(GraphStore)
    store.settings = None
    store.get = AsyncMock(side_effect=get)
    store.save_canonical = AsyncMock(side_effect=save)
    store.graphiti = SimpleNamespace(
        driver=SimpleNamespace(execute_query=AsyncMock(side_effect=execute_query)),
    )
    lock = SimpleNamespace(acquire=AsyncMock(return_value=True), release=AsyncMock())
    store.ingestor = Ingestor(store, SimpleNamespace(lock=lambda *args, **kwargs: lock))
    store.finalize_supersede = AsyncMock(wraps=store.finalize_supersede)

    accepted = await store.supersede(old.memory_id, replacement, actor_agent="repro")
    assert accepted.enrichment_status == "pending"
    assert old.model_dump() == original
    store.finalize_supersede.assert_not_awaited()
    store.save_canonical.assert_awaited_once()

    # Simulate enrichment advancing after initial acceptance, then resend the
    # identical key and payload through GraphStore.remember and Ingestor.accept.
    accepted.enrichment_status = status
    replay = await store.supersede(old.memory_id, replacement, actor_agent="repro")

    assert replay is accepted
    assert replay.enrichment_status == status
    assert store.ingestor.metrics["idempotent_replays"] == 1
    store.save_canonical.assert_awaited_once()
    if status != "ready":
        assert (old.valid_to, old.superseded_by) == (None, None), (
            f"{status} replay expired the current original: "
            f"valid_to={old.valid_to}, superseded_by={old.superseded_by}"
        )
        assert old.model_dump() == original
        store.finalize_supersede.assert_not_awaited()
        return

    assert old.valid_to == replacement.valid_from
    assert old.superseded_by == accepted.memory_id
    store.finalize_supersede.assert_awaited_once_with(
        old.memory_id, accepted.memory_id, replacement.valid_from,
    )
    finalized = old.model_dump()
    for _ in range(2):
        assert await store.supersede(old.memory_id, replacement, actor_agent="repro") is accepted
        assert old.model_dump() == finalized
    assert store.ingestor.metrics["idempotent_replays"] == 3
    store.save_canonical.assert_awaited_once()
