"""Durable canonical outbox, Valkey wakeups, and one bounded enrichment worker."""
import asyncio
from collections import Counter
import contextlib
import hashlib
import json
import logging
import time
import traceback
from pydantic import ValidationError
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, UUID, uuid5

from .enrichment import Enricher
from .policy import PolicyError, validate_write, group_id
from schemas.v1.metadata import MemoryRecord

log = logging.getLogger(__name__)


class Ingestor:
    def __init__(self, store, redis):
        self.store, self.redis = store, redis
        self.enricher = (Enricher(store.graphiti, store.settings)
                         if store.settings is not None
                         and hasattr(store.settings, "memory_decision_backend") else None)
        self.task = None
        self.metrics = Counter()

    async def start(self):
        self.task = asyncio.create_task(self.run(), name="memory-enrichment")

    async def close(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task

    async def accept(self, memory, actor_agent=None, memory_id=None, created_at=None, supersedes=None):
        group = validate_write(memory, actor_agent)
        if memory.idempotency_key:
            memory_id = uuid5(NAMESPACE_URL, json.dumps([group, memory.source.agent, memory.idempotency_key]))
        record = MemoryRecord(**memory.model_dump(), **({"memory_id": memory_id} if memory_id else {}), **({"created_at": created_at} if created_at else {}), supersedes=supersedes, enrichment_status="pending")
        payload = memory.model_dump(mode="json")
        # default timestamps change on request retries; the first accepted timestamp wins.
        if "valid_from" not in memory.model_fields_set:
            payload.pop("valid_from", None)
        fingerprint = hashlib.sha256(json.dumps({"memory": payload, "supersedes": str(supersedes)}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        # Briefly serialize canonical writes, including idempotency checks,
        # so concurrent agents can all be accepted without a busy response.
        lock = self.redis.lock("memory:accept-lock", timeout=30, blocking_timeout=5)
        if not await lock.acquire():
            raise PolicyError("memory acceptance is busy; check memory_get before retrying")
        try:
            existing = await self.store.get(record.memory_id)
            if existing:
                rows, _, _ = await self.store.graphiti.driver.execute_query("MATCH (r:MemoryRecord {memory_id:$id}) RETURN r.fingerprint AS fingerprint", id=str(record.memory_id))
                if rows[0]["fingerprint"] != fingerprint:
                    raise PolicyError("idempotency key was already used for different content")
                self.metrics["idempotent_replays"] += 1
                return existing
            await self.store.save_canonical(record, group, fingerprint=fingerprint)
            self.metrics["accepted"] += 1
        finally:
            await lock.release()
        # The DB record is the durable outbox; run() polls it (one-second default), so
        # acceptance does not wait for a separate Valkey wakeup write.
        return record

    async def run(self):
        while True:
            try:
                lock = self.redis.lock(
                    "memory:enrichment:worker",
                    timeout=self.store.settings.memory_enrichment_lease_seconds,
                    blocking=False,
                )
                if await lock.acquire():
                    try:
                        await self.step()
                    finally:
                        await lock.release()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("enrichment worker error: %s", type(exc).__name__)
            await asyncio.sleep(getattr(
                self.store.settings, "memory_enrichment_poll_seconds", 1.0
            ))

    async def step(self):
        driver = self.store.graphiti.driver
        stale = (datetime.now(timezone.utc)-timedelta(
            seconds=getattr(self.store.settings, "memory_enrichment_lease_seconds", 240)
        )).isoformat()
        rows, _, _ = await driver.execute_query("""
            MATCH (r:MemoryRecord)
            WHERE r.enrichment_status = 'pending'
               OR (r.enrichment_status = 'processing' AND r.enrichment_started < $stale)
            RETURN r.memory_id AS id, r.plan_json AS plan, coalesce(r.enrichment_attempts,0) AS attempts
            ORDER BY r.created_at LIMIT 1
        """, stale=stale)
        if not rows:
            self.metrics["empty_polls"] += 1
            return
        row = rows[0]
        record = await self.store.get(UUID(row["id"]))
        group = group_id(record.scope, record.visibility)
        started = time.monotonic()
        self.metrics["started"] += 1
        await driver.execute_query("MATCH (r:MemoryRecord {memory_id:$id}) SET r.enrichment_status='processing', r.enrichment_started=$now, r.enrichment_attempts=$attempts", id=row["id"], now=datetime.now(timezone.utc).isoformat(), attempts=row["attempts"]+1)
        try:
            async with asyncio.timeout(self.store.settings.memory_enrichment_timeout_seconds):
                if getattr(self.store.settings, "memory_ingestion_mode", "typesafe_async") == "graphiti_async":
                    result = await self.store.add_record_episode(record)
                    status, episode = "ready", result.episode.uuid
                    metrics = {"pipeline": "graphiti_standard", "attempt": row["attempts"] + 1}
                else:
                    plan = (json.loads(row["plan"]) if row.get("plan")
                            else await self.enricher.plan(record, group))
                    await driver.execute_query(
                        "MATCH (r:MemoryRecord {memory_id:$id}) SET r.plan_json=$plan",
                        id=row["id"], plan=json.dumps(plan, ensure_ascii=False),
                    )
                    metrics = plan["metrics"]
                    if plan["status"] == "uncertain":
                        status, episode = "uncertain", ""
                    else:
                        await self.enricher.write(plan, group)
                        status, episode = "ready", plan["episode"]["uuid"]
                await driver.execute_query("MATCH (r:MemoryRecord {memory_id:$id}) SET r.enrichment_status=$status, r.episode_uuid=$episode, r.enrichment_error='', r.enrichment_seconds=$seconds, r.enrichment_metrics=$metrics", id=row["id"], status=status, episode=episode, seconds=time.monotonic()-started, metrics=json.dumps(metrics))
                if status == "ready" and record.supersedes:
                    await self.store.finalize_supersede(record.supersedes, record.memory_id,
                                                        record.valid_from)
                await self.redis.zrem("memory:enrichment:wakeups", row["id"])
                self.metrics["completed_" + status] += 1
                self.metrics["milliseconds"] += (time.monotonic() - started) * 1000
                log.info("enrichment completed id=%s status=%s seconds=%.3f metrics=%s", row["id"], status, time.monotonic()-started, metrics)
        except asyncio.CancelledError:
            await driver.execute_query("MATCH (r:MemoryRecord {memory_id:$id}) SET r.enrichment_status='pending'", id=row["id"])
            raise
        except Exception as exc:
            status = ("failed" if row["attempts"] + 1
                      >= getattr(self.store.settings, "memory_enrichment_max_attempts", 3)
                      else "pending")
            details = json.dumps(exc.errors(include_input=False, include_context=False, include_url=False)) if isinstance(exc, ValidationError) else type(exc).__name__
            await driver.execute_query("MATCH (r:MemoryRecord {memory_id:$id}) SET r.enrichment_status=$status, r.enrichment_error=$error", id=row["id"], status=status, error=details)
            self.metrics["failures"] += 1
            self.metrics["failure_" + type(exc).__name__] += 1
            log.error("enrichment failed id=%s error=%s stack=%s", row["id"], details, [(f.name, f.lineno) for f in traceback.extract_tb(exc.__traceback__)])
