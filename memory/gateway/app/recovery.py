"""Explicit one-shot recovery of unwritten uncertain records via standard Graphiti."""
import asyncio
import json
import time
from datetime import datetime, timezone
from uuid import uuid5

from .policy import PolicyError, group_id


async def resume_standard(store, memory_id, allow_reviewed_retry=False):
    if store.ingestor or store.settings.memory_ingestion_mode != "graphiti":
        raise PolicyError("standard Graphiti mode is required")
    record = await store.get(memory_id)
    if record is None:
        raise KeyError(str(memory_id))
    allowed = {"uncertain", "review_required"} if allow_reviewed_retry else {"uncertain"}
    if record.enrichment_status not in allowed or record.episode_uuid:
        raise PolicyError("only uncertain records without a linked episode can be resumed")
    driver = store.graphiti.driver
    rows, _, _ = await driver.execute_query(
        "MATCH (r:MemoryRecord {memory_id:$id}) RETURN r.plan_json AS plan", id=str(memory_id))
    if not rows or json.loads(rows[0].get("plan") or "{}").get("status") != "uncertain":
        raise PolicyError("an unwritten uncertain plan is required")
    episode_id = str(uuid5(memory_id, "graphiti-standard-resume"))
    _, scoped, _ = store.graphiti._resolve_request_scope(group_id(record.scope, record.visibility))
    episodes, _, _ = await scoped.execute_query(
        "MATCH (e:Episodic) WHERE e.uuid IN $ids OPTIONAL MATCH (e)-[link]-() "
        "RETURN e.uuid AS uuid, e.content AS content, e.entity_edges AS edges, count(link) AS links",
        ids=[episode_id, str(uuid5(memory_id, "episode"))])
    # The only re-usable partial state is our untouched source episode: no entity
    # links, no fact UUIDs, and no facts referring to this episode anywhere in scope.
    if episodes and not (allow_reviewed_retry and record.enrichment_status == "review_required"):
        raise PolicyError("existing or partial episode requires inspection; refusing blind replay")
    for episode in episodes:
        body = json.loads(episode.get("content") or "{}")
        if (episode["uuid"] != episode_id or episode["links"] or episode.get("edges")
                or body.get("memory_id") != str(memory_id) or body.get("content") != record.content):
            raise PolicyError("partial graph is not a pristine source episode")
    facts, _, _ = await scoped.execute_query(
        "MATCH ()-[e:RELATES_TO]->() WHERE $id IN coalesce(e.episodes,[]) RETURN count(e) AS count",
        id=episode_id)
    if facts[0]["count"]:
        raise PolicyError("facts already refer to this episode; manual graph review required")
    # Atomic claim, never overwrite a concurrent recovery or a completed record.
    claimed, _, _ = await driver.execute_query("""
        MATCH (r:MemoryRecord {memory_id:$id})
        WHERE r.enrichment_status=$expected AND coalesce(r.episode_uuid,'')=''
        SET r.previous_enrichment_status=r.enrichment_status,
            r.previous_enrichment_metrics=r.enrichment_metrics,
            r.previous_enrichment_seconds=r.enrichment_seconds,
            r.enrichment_status='processing_standard', r.enrichment_started=$now,
            r.standard_episode_uuid=$episode
        RETURN r.memory_id AS id
    """, id=str(memory_id), expected=record.enrichment_status, now=datetime.now(timezone.utc).isoformat(), episode=episode_id)
    if not claimed:
        raise PolicyError("record already claimed")
    started = time.monotonic()
    router = getattr(store.graphiti.llm_client, "node_router", None)
    before = dict(router.metrics) if router else {}
    try:
        async with asyncio.timeout(1200):
            result = await store.add_record_episode(record, episode_uuid=episode_id)
        elapsed = time.monotonic()-started
        metrics = {k: v-before.get(k, 0) for k, v in router.metrics.items()} if router else {}
        metrics.update(mode="graphiti_standard", nodes=len(result.nodes), edges=len(result.edges),
                       routing_counter_scope="process delta; run recoveries sequentially")
        await driver.execute_query("""
            MATCH (r:MemoryRecord {memory_id:$id})
            SET r.enrichment_status='ready', r.episode_uuid=$episode,
                r.enrichment_error='', r.enrichment_seconds=$seconds,
                r.enrichment_metrics=$metrics
        """, id=str(memory_id), episode=result.episode.uuid, seconds=elapsed,
            metrics=json.dumps(metrics))
        return {"memory_id": str(memory_id), "status": "ready", "seconds": elapsed, "metrics": metrics}
    except (Exception, asyncio.CancelledError) as exc:
        # A failed add_episode may have written part of a graph. Never automatically
        # retry it or let the old async worker pick it up. Preserve plan and partial data.
        await driver.execute_query("""
            MATCH (r:MemoryRecord {memory_id:$id})
            SET r.enrichment_status='review_required', r.enrichment_error=$error,
                r.enrichment_seconds=$seconds
        """, id=str(memory_id), error=type(exc).__name__, seconds=time.monotonic()-started)
        raise
