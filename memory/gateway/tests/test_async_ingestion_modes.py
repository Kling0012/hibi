from collections import Counter
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from gateway.app.ingestion import Ingestor
from schemas.v1.metadata import MemoryRecord, MemoryType, Scope, Source, Visibility


class FakeDriver:
    def __init__(self, memory_id):
        self.memory_id = memory_id
        self.queries = []

    async def execute_query(self, query, **kwargs):
        self.queries.append((query, kwargs))
        if "ORDER BY r.created_at LIMIT 1" in query:
            return ([{"id": str(self.memory_id), "plan": None, "attempts": 0}], None, None)
        return ([], None, None)


class FakeRedis:
    def __init__(self):
        self.removed = []

    async def zrem(self, key, value):
        self.removed.append((key, value))


async def test_graphiti_async_uses_standard_pipeline_and_delays_supersede_close():
    old_id, new_id = uuid4(), uuid4()
    record = MemoryRecord(
        memory_id=new_id,
        type=MemoryType.stable_fact,
        content="replacement",
        scope=Scope(user="default", project="all", agent=None),
        source=Source(agent="codex"),
        visibility=Visibility.project,
        supersedes=old_id,
        enrichment_status="pending",
    )
    driver = FakeDriver(new_id)
    finalized = []

    class Store:
        settings = SimpleNamespace(
            memory_enrichment_lease_seconds=240,
            memory_enrichment_timeout_seconds=30,
            memory_enrichment_max_attempts=3,
            memory_ingestion_mode="graphiti_async",
        )
        graphiti = SimpleNamespace(driver=driver)

        async def get(self, memory_id):
            return record

        async def add_record_episode(self, current):
            assert current.memory_id == new_id
            return SimpleNamespace(episode=SimpleNamespace(uuid="episode-new"))

        async def finalize_supersede(self, previous, replacement, boundary):
            finalized.append((previous, replacement, boundary))

    ingestor = object.__new__(Ingestor)
    ingestor.store = Store()
    ingestor.redis = FakeRedis()
    ingestor.enricher = None
    ingestor.metrics = Counter()
    ingestor.task = None
    await ingestor.step()
    assert finalized == [(old_id, new_id, record.valid_from)]
    assert ingestor.metrics["completed_ready"] == 1
    assert ingestor.redis.removed == [("memory:enrichment:wakeups", str(new_id))]
