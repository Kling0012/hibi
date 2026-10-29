import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from graphiti_core import Graphiti
from graphiti_core.graphiti import RELEVANT_SCHEMA_LIMIT
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.config import ModelSize
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.nodes import EpisodeType, EpisodicNode
from openai import AsyncOpenAI

from schemas.v1 import ENTITY_TYPES
from schemas.v1.metadata import MemoryCreate, MemoryRecord, MemorySearchRequest, MemoryType

from .config import Settings
from .embedding import FailoverEmbedder, MetricsEmbedder
from .jev_router import JevNodeRouter, JevNodeRoutingMixin
from .policy import PolicyError, group_id, readable_groups, validate_write
from .structured_response import generate_validated_json
from .search_rerank import SearchReranker


log = logging.getLogger(__name__)


RECORD_RETURN = """
RETURN r.memory_id AS memory_id, r.type AS type, r.content AS content,
       r.scope_json AS scope_json, r.source_json AS source_json,
       r.visibility AS visibility, r.created_at AS created_at,
       r.valid_from AS valid_from, r.valid_to AS valid_to,
       r.schema_version AS schema_version, r.episode_uuid AS episode_uuid,
       r.refs_json AS refs_json, r.tags_json AS tags_json, r.name AS name,
       r.supersedes AS supersedes, r.superseded_by AS superseded_by,
       coalesce(r.enrichment_status, 'ready') AS enrichment_status,
       r.enrichment_error AS enrichment_error, r.idempotency_key AS idempotency_key
"""


def normalize_graph_attributes(attributes: dict[str, Any] | None) -> dict[str, Any]:
    """Keep property-graph values primitive while preserving nested data as JSON."""
    normalized: dict[str, Any] = {}
    primitive = (str, int, float, bool)
    for key, value in (attributes or {}).items():
        if value is None or isinstance(value, primitive):
            normalized[key] = value
        elif isinstance(value, list) and all(item is None or isinstance(item, primitive) for item in value):
            normalized[key] = value
        else:
            normalized[key] = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return normalized


def validate_supersede(old: MemoryRecord, replacement: MemoryCreate, actor_agent: str | None) -> None:
    old_group = group_id(old.scope, old.visibility)
    replacement_group = validate_write(replacement, actor_agent)
    if replacement_group != old_group:
        raise PolicyError("replacement must remain in the same scope as the original memory")
    if replacement.valid_from < old.valid_from:
        raise PolicyError("replacement valid_from cannot precede the original memory")


def effective_fact_valid_to(edge_invalid_at: datetime | None, record_valid_to: datetime | None) -> str | None:
    boundary = edge_invalid_at or record_valid_to
    return boundary.isoformat() if boundary else None


def as_utc(value: datetime) -> datetime:
    """Treat naive datetimes as UTC; stored ISO strings mix offsets."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def pending_search_terms(query: str) -> list[str]:
    """Small, case-insensitive keyword set for canonical records without graph facts."""
    terms: list[str] = []
    for token in re.findall(r"\w+", query):
        if len(token) < 2:
            continue
        terms.append(token.casefold())
        if len(token) > 4 and re.search(r"[一-龯々ぁ-んァ-ヶー]", token):
            # Mixed Japanese/Latin tokens can span a whole sentence. Bigrams
            # from CJK runs also match a shorter canonical phrase within it.
            for run in re.findall(r"[一-龯々ぁ-んァ-ヶー]+", token):
                terms.extend(run[index:index + 2].casefold()
                             for index in range(len(run) - 1))
    return list(dict.fromkeys(terms))[:16]


class CompatibleGraphiti(Graphiti):
    """Compatibility layer for graphiti-core #1885 until upstream serializes nested attributes."""

    async def _process_episode_data(self, episode, nodes, entity_edges, *args, **kwargs):
        for node in nodes:
            node.attributes = normalize_graph_attributes(node.attributes)
        for edge in entity_edges:
            edge.attributes = normalize_graph_attributes(edge.attributes)
        return await super()._process_episode_data(episode, nodes, entity_edges, *args, **kwargs)


def build_llm_config(settings: Settings) -> LLMConfig:
    api_key = settings.memory_llm_api_key or settings.openai_api_key or "not-configured"
    base_url = None if settings.memory_llm_provider == "openai" else settings.memory_llm_base_url
    return LLMConfig(
        api_key=api_key,
        model=settings.memory_llm_model,
        small_model=settings.memory_llm_small_model,
        base_url=base_url,
        temperature=0,
    )


def build_embedder_config(settings: Settings) -> OpenAIEmbedderConfig:
    return OpenAIEmbedderConfig(
        api_key=settings.memory_embedding_api_key,
        base_url=settings.memory_embedding_base_url,
        embedding_model=settings.memory_embedding_model,
        embedding_dim=settings.memory_embedding_dim,
    )


def build_cloudflare_embedder_config(settings: Settings) -> OpenAIEmbedderConfig:
    if not settings.cloudflare_account_id or not settings.cloudflare_api_token:
        raise ValueError(
            "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are required for Cloudflare embeddings"
        )
    return OpenAIEmbedderConfig(
        api_key=settings.cloudflare_api_token,
        base_url=(
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{settings.cloudflare_account_id}/ai/v1"
        ),
        embedding_model=settings.memory_embedding_cloudflare_model,
        embedding_dim=settings.memory_embedding_dim,
    )


def build_embedder(settings: Settings) -> EmbedderClient:
    if settings.memory_embedding_mode == "local":
        return MetricsEmbedder(
            OpenAIEmbedder(config=build_embedder_config(settings)), provider="local"
        )

    cloudflare_config = build_cloudflare_embedder_config(settings)
    cloudflare_client = AsyncOpenAI(
        api_key=cloudflare_config.api_key,
        base_url=cloudflare_config.base_url,
        timeout=settings.memory_embedding_cloudflare_timeout_seconds,
        max_retries=0,
    )
    cloudflare = MetricsEmbedder(
        OpenAIEmbedder(config=cloudflare_config, client=cloudflare_client),
        provider="cloudflare",
    )
    if settings.memory_embedding_mode == "cloudflare":
        return cloudflare
    local = MetricsEmbedder(
        OpenAIEmbedder(config=build_embedder_config(settings)), provider="local"
    )
    return FailoverEmbedder(
        cloudflare,
        local,
        cooldown_seconds=settings.memory_embedding_fallback_cooldown_seconds,
    )


class BoundedReasoningClient(OpenAIGenericClient):
    """GLM-5.3 defaults to maximum thinking; reserve room for actual JSON output."""

    @property
    def structured_response_metrics(self):
        if not hasattr(self, "_structured_metrics"):
            self._structured_metrics = {}
        return self._structured_metrics

    async def _generate_response(self, messages, response_model=None, max_tokens=2048, model_size=ModelSize.medium):
        if not (self.model or "").lower().startswith("glm-5.3"):
            return await super()._generate_response(messages, response_model, max_tokens, model_size)
        request_messages = [{"role": m.role, "content": self._clean_input(m.content)} for m in messages if m.role in ("user", "system")]
        return await generate_validated_json(
            self.client.with_options(max_retries=0).chat.completions.create,
            messages=request_messages,
            response_model=response_model,
            request_kwargs={
                "model": self.model,
                "temperature": self.temperature,
                "max_tokens": max(16384, max_tokens),
                "reasoning_effort": "low",
                "response_format": self._build_response_format(response_model),
            },
            strip_code_fences=self._strip_code_fences,
            metrics=self.structured_response_metrics,
        )


class GraphitiJevClient(JevNodeRoutingMixin, BoundedReasoningClient):
    pass


def build_llm_client(settings: Settings) -> OpenAIGenericClient:
    client = GraphitiJevClient(
        config=build_llm_config(settings),
        max_tokens=settings.memory_llm_max_tokens,
        structured_output_mode=settings.memory_llm_structured_output_mode,
    )
    if settings.memory_ingestion_mode == "graphiti" and settings.memory_node_resolution_provider == "jev":
        client.node_router = JevNodeRouter(settings)
    return client


class GraphStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.ingestor = None
        llm_config = build_llm_config(settings)
        llm = build_llm_client(settings)
        embedder = build_embedder(settings)
        driver = FalkorDriver(
            host=settings.falkordb_host,
            port=settings.falkordb_port,
            database=settings.falkordb_database,
        )
        self.graphiti = CompatibleGraphiti(
            graph_driver=driver,
            llm_client=llm,
            embedder=embedder,
            cross_encoder=OpenAIRerankerClient(client=llm, config=llm_config),
        )
        self.search_reranker = SearchReranker(settings)

    async def initialize(self) -> None:
        await self.graphiti.build_indices_and_constraints()
        # These indexes cover the Gateway's canonical record layer in the same Falkor graph.
        for query in (
            "CREATE INDEX FOR (r:MemoryRecord) ON (r.memory_id)",
            "CREATE INDEX FOR (r:MemoryRecord) ON (r.group_id)",
            "CREATE INDEX FOR (r:MemoryRecord) ON (r.type)",
        ):
            try:
                await self.graphiti.driver.execute_query(query)
            except Exception as exc:
                if "already indexed" not in str(exc).lower():
                    raise

    async def close(self) -> None:
        await self.graphiti.close()

    @staticmethod
    def _record_from_row(row: dict[str, Any]) -> MemoryRecord:
        data = dict(row)
        data["scope"] = json.loads(data.pop("scope_json"))
        data["source"] = json.loads(data.pop("source_json"))
        data["refs"] = json.loads(data.pop("refs_json") or "[]")
        data["tags"] = json.loads(data.pop("tags_json") or "[]")
        data["name"] = data.get("name") or None
        data["valid_to"] = data.get("valid_to") or None
        data["episode_uuid"] = data.get("episode_uuid") or None
        data["supersedes"] = data.get("supersedes") or None
        data["superseded_by"] = data.get("superseded_by") or None
        data["idempotency_key"] = data.get("idempotency_key") or None
        return MemoryRecord.model_validate(data)

    async def remember(
        self,
        memory: MemoryCreate,
        *,
        actor_agent: str | None = None,
        memory_id: UUID | None = None,
        created_at: datetime | None = None,
        supersedes: UUID | None = None,
    ) -> MemoryRecord:
        if self.ingestor:
            return await self.ingestor.accept(memory, actor_agent, memory_id, created_at, supersedes)
        group = validate_write(memory, actor_agent)
        record = MemoryRecord(
            **memory.model_dump(),
            **({"memory_id": memory_id} if memory_id else {}),
            **({"created_at": created_at} if created_at else {}),
            supersedes=supersedes,
        )
        result = await self.add_record_episode(record)
        record.episode_uuid = result.episode.uuid
        await self.save_canonical(record, group)
        return record

    async def add_record_episode(self, record: MemoryRecord, episode_uuid: str | None = None):
        """The same standard Graphiti pipeline for new records and explicit recovery."""
        group = group_id(record.scope, record.visibility)
        episode_payload = {
            "memory_id": str(record.memory_id),
            "type": record.type.value,
            "content": record.content,
            "scope": record.scope.model_dump(),
            "source": record.source.model_dump(),
            "visibility": record.visibility.value,
            "valid_from": record.valid_from.isoformat(),
            "refs": record.refs,
            "tags": record.tags,
            "schema_version": record.schema_version,
        }
        body = json.dumps(episode_payload, ensure_ascii=False)
        previous_episode_uuids = None
        if episode_uuid is not None:
            # Graphiti 0.30.2 interprets uuid as an EXISTING episode to process.
            # Recovery already checked absence and claimed the canonical record.
            # Seed only its episode through Graphiti's public model API; Graphiti
            # still owns every extracted node/edge, temporal decision and final write.
            _, driver, _ = self.graphiti._resolve_request_scope(group)
            previous = await self.graphiti.retrieve_episodes(
                record.valid_from, last_n=RELEVANT_SCHEMA_LIMIT+1, group_ids=[group],
                source=EpisodeType.json, driver=driver,
            )
            previous_episode_uuids = [episode.uuid for episode in previous if episode.uuid != episode_uuid][:RELEVANT_SCHEMA_LIMIT]
            await EpisodicNode(
                uuid=episode_uuid, name=record.name or f"memory:{record.memory_id}",
                group_id=group, source=EpisodeType.json, content=body,
                source_description=record.source.uri or f"agent:{record.source.agent}",
                created_at=datetime.now(timezone.utc), valid_at=record.valid_from,
            ).save(driver)
        return await self.graphiti.add_episode(
            name=record.name or f"memory:{record.memory_id}",
            episode_body=body,
            source=EpisodeType.json,
            source_description=record.source.uri or f"agent:{record.source.agent}",
            reference_time=record.valid_from,
            group_id=group,
            uuid=episode_uuid,
            previous_episode_uuids=previous_episode_uuids,
            entity_types=ENTITY_TYPES,
            custom_extraction_instructions=(
                "Extract only durable facts, entities, and relationships. Preserve temporal changes, "
                "causes, decisions, requirements, procedures, issues, solutions, and artifact paths."
            ),
        )

    async def save_canonical(self, record: MemoryRecord, group: str, fingerprint: str = "") -> None:
        params = {
            "memory_id": str(record.memory_id), "type": record.type.value,
            "content": record.content, "scope_json": record.scope.model_dump_json(),
            "source_json": record.source.model_dump_json(), "visibility": record.visibility.value,
            "created_at": record.created_at.isoformat(), "valid_from": record.valid_from.isoformat(),
            "valid_to": record.valid_to.isoformat() if record.valid_to else "", "schema_version": record.schema_version,
            "episode_uuid": record.episode_uuid or "", "refs_json": json.dumps(record.refs, ensure_ascii=False),
            "tags_json": json.dumps(record.tags, ensure_ascii=False), "name": record.name or "",
            "group_id": group, "supersedes": str(record.supersedes) if record.supersedes else "",
            "enrichment_status": record.enrichment_status,
            "idempotency_key": record.idempotency_key or "", "fingerprint": fingerprint,
        }
        await self.graphiti.driver.execute_query(
            """
            CREATE (r:MemoryRecord {
              memory_id: $memory_id, type: $type, content: $content,
              scope_json: $scope_json, source_json: $source_json, visibility: $visibility,
              created_at: $created_at, valid_from: $valid_from, valid_to: $valid_to,
              schema_version: $schema_version, episode_uuid: $episode_uuid,
              refs_json: $refs_json, tags_json: $tags_json, name: $name,
              group_id: $group_id, supersedes: $supersedes, superseded_by: '',
              enrichment_status: $enrichment_status, idempotency_key: $idempotency_key,
              fingerprint: $fingerprint
            })
            """,
            **params,
        )
    async def get(self, memory_id: UUID) -> MemoryRecord | None:
        rows, _, _ = await self.graphiti.driver.execute_query(
            f"MATCH (r:MemoryRecord {{memory_id: $memory_id}}) {RECORD_RETURN}",
            memory_id=str(memory_id), routing_="r",
        )
        return self._record_from_row(rows[0]) if rows else None

    async def list_records(
        self, *, user: str, project: str | None, agent: str,
        include_history: bool = False, limit: int = 100,
        since: datetime | None = None, until: datetime | None = None,
        field: str = "created_at", types: list[MemoryType] | None = None,
        agent_filter: str | None = None,
        cursor: tuple[datetime, str] | None = None,
    ) -> list[MemoryRecord]:
        """Page canonical records chronologically for timeline rendering.

        Filtering and ordering run on parsed datetime values because stored
        ISO strings mix +00:00 and local offsets, so textual Cypher range
        comparisons would misorder valid_from. Scanning pages by memory_id
        keyset keeps the fetch stable regardless of the sort key.
        """
        if field not in ("created_at", "valid_from"):
            raise ValueError("field must be 'created_at' or 'valid_from'")
        since = as_utc(since) if since is not None else None
        until = as_utc(until) if until is not None else None
        groups = readable_groups(user, project, agent)
        history = "" if include_history else "AND r.valid_to = ''"
        type_filter = [item.value for item in (types or [])]
        type_clause = "" if not type_filter else "AND r.type IN $types"
        records: list[MemoryRecord] = []
        last_id = ""
        chunk = 500
        while True:
            rows, _, _ = await self.graphiti.driver.execute_query(
                f"""
                MATCH (r:MemoryRecord)
                WHERE r.group_id IN $groups AND r.memory_id > $last_id {history} {type_clause}
                {RECORD_RETURN}
                ORDER BY r.memory_id
                LIMIT $chunk
                """,
                groups=groups, last_id=last_id, types=type_filter,
                chunk=chunk, routing_="r",
            )
            records.extend(self._record_from_row(row) for row in rows)
            if len(rows) < chunk:
                break
            last_id = rows[-1]["memory_id"]
        if agent_filter:
            records = [r for r in records if r.source.agent == agent_filter]
        if since is not None:
            records = [r for r in records if as_utc(getattr(r, field)) >= since]
        if until is not None:
            records = [r for r in records if as_utc(getattr(r, field)) < until]
        records.sort(key=lambda r: (as_utc(getattr(r, field)), str(r.memory_id)))
        if cursor is not None:
            cursor_key = (as_utc(cursor[0]), cursor[1])
            records = [r for r in records
                       if (as_utc(getattr(r, field)), str(r.memory_id)) > cursor_key]
        return records[:limit]

    async def search(self, request: MemorySearchRequest) -> list[dict[str, Any]]:
        groups = readable_groups(request.user, request.project, request.agent)
        settings = getattr(self, "settings", None)
        rerank_mode = getattr(settings, "memory_search_rerank_mode", "off")
        rerank_candidates = getattr(settings, "memory_search_rerank_candidates", request.limit)
        candidate_limit = (max(request.limit, rerank_candidates)
                           if rerank_mode != "off" else request.limit)
        edges = await self.graphiti.search(
            request.query, group_ids=groups, num_results=candidate_limit * 3,
        )
        episode_ids = list(dict.fromkeys(ep for edge in edges for ep in edge.episodes))
        type_filter = [item.value for item in request.types]
        history = "" if request.include_history else "AND r.valid_to = ''"
        type_clause = "" if not type_filter else "AND r.type IN $types"
        if episode_ids:
            rows, _, _ = await self.graphiti.driver.execute_query(
                f"""
                MATCH (r:MemoryRecord)
                WHERE r.episode_uuid IN $episodes AND r.group_id IN $groups {history} {type_clause}
                {RECORD_RETURN}
                """,
                episodes=episode_ids, groups=groups, types=type_filter, routing_="r",
            )
        else:
            rows = []
        records = [self._record_from_row(row) for row in rows]
        by_episode = {record.episode_uuid: record for record in records}
        results: list[dict[str, Any]] = []
        seen: set[UUID] = set()
        for edge in edges:
            # The canonical lookup has no guaranteed row order. Preserve the
            # Graphiti edge/provenance order and apply the public limit only
            # after mapping and de-duplicating eligible canonical records.
            record = next((by_episode[ep] for ep in edge.episodes
                           if ep in by_episode and by_episode[ep].memory_id not in seen), None)
            if not record:
                continue
            seen.add(record.memory_id)
            results.append({
                "memory": record.model_dump(mode="json"),
                "enrichment_status": record.enrichment_status,
                "matched_fact": edge.fact,
                "fact_valid_from": edge.valid_at.isoformat() if edge.valid_at else None,
                "fact_valid_to": effective_fact_valid_to(edge.invalid_at, record.valid_to),
            })
            if len(results) >= candidate_limit:
                break
        # Keep the established Graphiti order and optional reranking intact.
        # Only un-enriched records are considered by this lightweight path.
        if rerank_mode != "off" and len(results) > 1:
            try:
                reranked = await self.search_reranker.rank(request.query, results)
            except Exception as exc:
                # Retrieval must remain available when the optional decision
                # backend fails. No result is dropped on this path.
                log.info("search rerank fallback error=%s", type(exc).__name__)
            else:
                if rerank_mode == "on":
                    results = reranked
                else:
                    self.search_reranker.metrics["shadow_requests"] += 1
                    if reranked[0]["memory"]["memory_id"] != results[0]["memory"]["memory_id"]:
                        self.search_reranker.metrics["shadow_top_changed"] += 1
        if getattr(self, "ingestor", None):
            terms = pending_search_terms(request.query)
            if terms:
                matches = " OR ".join(
                    f"toLower(r.{field}) CONTAINS $term_{index}"
                    for index in range(len(terms))
                    for field in ("content", "name", "tags_json")
                )
                pending_rows, _, _ = await self.graphiti.driver.execute_query(
                    f"""
                    MATCH (r:MemoryRecord)
                    WHERE r.enrichment_status IN ['pending', 'processing', 'failed', 'uncertain']
                      AND r.group_id IN $groups {history} {type_clause}
                      AND ({matches})
                    {RECORD_RETURN}
                    ORDER BY r.created_at DESC LIMIT $pending_limit
                    """,
                    groups=groups, types=type_filter,
                    pending_limit=max(request.limit * 5, 50),
                    **{f"term_{index}": term for index, term in enumerate(terms)},
                    routing_="r",
                )
                pending_records = [self._record_from_row(row) for row in pending_rows]
                def score(record):
                    haystack = " ".join((record.content, record.name or "", *record.tags)).casefold()
                    return sum(term in haystack for term in terms)
                ranked_pending = sorted(
                    ((score(record), record) for record in pending_records
                     if record.memory_id not in seen),
                    key=lambda item: item[0], reverse=True,
                )
                strong_threshold = (len(terms) + 1) // 2
                strong_count = sum(score >= strong_threshold for score, _ in ranked_pending)
                reserved = min(strong_count, max(1, request.limit // 4))
                # Strong canonical matches claim a small tail slot even when
                # Graphiti fills the limit; retained ready order stays intact.
                results = results[:request.limit - reserved]
                for _, record in ranked_pending[:request.limit - len(results)]:
                    results.append({
                        "memory": record.model_dump(mode="json"),
                        "enrichment_status": record.enrichment_status,
                        "matched_fact": None,
                        "fact_valid_from": record.valid_from.isoformat(),
                        "fact_valid_to": record.valid_to.isoformat() if record.valid_to else None,
                    })
                    if len(results) >= request.limit:
                        break
        return results[:request.limit]

    async def supersede(
        self, old_id: UUID, replacement: MemoryCreate, *, actor_agent: str | None = None,
    ) -> MemoryRecord:
        old = await self.get(old_id)
        if old is None:
            raise KeyError(f"memory not found: {old_id}")
        validate_supersede(old, replacement, actor_agent)
        new = await self.remember(replacement, actor_agent=actor_agent, supersedes=old_id)
        if new.enrichment_status != "ready":
            return new
        await self.finalize_supersede(old_id, new.memory_id, new.valid_from)
        return new

    async def finalize_supersede(
        self, old_id: UUID, new_id: UUID, boundary_time: datetime,
    ) -> None:
        """Close a current record once, only for its ready replacement."""
        boundary = boundary_time.isoformat()
        changed_at = datetime.now(timezone.utc).isoformat()
        await self.graphiti.driver.execute_query(
            """
            MATCH (old:MemoryRecord {memory_id: $old_id}),
                  (new:MemoryRecord {memory_id: $new_id})
            WHERE coalesce(new.enrichment_status, 'ready') = 'ready'
              AND new.supersedes = $old_id
              AND coalesce(old.valid_to, '') = ''
              AND coalesce(old.superseded_by, '') = ''
            SET old.valid_to = $boundary, old.superseded_by = $new_id
            MERGE (old)-[s:SUPERSEDED_BY]->(new) ON CREATE SET s.at = $changed_at
            """,
            old_id=str(old_id), new_id=str(new_id),
            boundary=boundary, changed_at=changed_at,
        )

    async def forget(self, memory_id: UUID) -> bool:
        record = await self.get(memory_id)
        if record is None:
            return False
        if record.episode_uuid:
            await self.graphiti.remove_episode(record.episode_uuid)
        await self.graphiti.driver.execute_query(
            "MATCH (r:MemoryRecord {memory_id: $memory_id}) DETACH DELETE r",
            memory_id=str(memory_id),
        )
        return True

    async def status(self) -> dict[str, Any]:
        rows, _, _ = await self.graphiti.driver.execute_query(
            "MATCH (r:MemoryRecord) RETURN count(r) AS total, "
            "sum(CASE WHEN r.valid_to = '' THEN 1 ELSE 0 END) AS current",
            routing_="r",
        )
        result = rows[0] if rows else {"total": 0, "current": 0}
        states, _, _ = await self.graphiti.driver.execute_query("MATCH (r:MemoryRecord) RETURN coalesce(r.enrichment_status,'ready') AS state, count(r) AS count", routing_="r")
        result["enrichment"] = {r["state"]: r["count"] for r in states}
        router = getattr(self.graphiti.llm_client, "node_router", None)
        embedding_snapshot = getattr(getattr(self.graphiti, "embedder", None), "snapshot", None)
        result["pipeline"] = {
            "ingestion_mode": self.settings.memory_ingestion_mode,
            "node_resolution_provider": self.settings.memory_node_resolution_provider,
            "node_routing_metrics": dict(router.metrics) if router else {},
            "structured_response_metrics": dict(
                getattr(self.graphiti.llm_client, "structured_response_metrics", {})
            ),
            "embedding_provider_metrics": (
                embedding_snapshot() if callable(embedding_snapshot) else None
            ),
            "search_rerank_mode": self.settings.memory_search_rerank_mode,
            "search_rerank_metrics": dict(self.search_reranker.metrics),
            "ingestion_metrics": (
                dict(self.ingestor.metrics) if self.ingestor else {}
            ),
            "direct_decision_metrics": (
                dict(self.ingestor.enricher.engine.metrics) if self.ingestor else {}
            ),
        }
        return result

    async def enrichment_details(self, memory_id: UUID):
        rows, _, _ = await self.graphiti.driver.execute_query("MATCH (r:MemoryRecord {memory_id:$id}) RETURN coalesce(r.enrichment_status,'ready') AS status, r.enrichment_error AS error, r.enrichment_seconds AS seconds, r.enrichment_metrics AS metrics, r.enrichment_attempts AS attempts", id=str(memory_id), routing_="r")
        if not rows:
            raise KeyError(str(memory_id))
        result = rows[0]
        result["metrics"] = json.loads(result["metrics"]) if result.get("metrics") else None
        return result

    async def retry_enrichment(self, memory_id: UUID):
        if not self.ingestor:
            raise PolicyError("asynchronous ingestion is not enabled")
        async with self.ingestor.redis.lock("memory:accept-lock", timeout=30, blocking_timeout=10):
            record = await self.get(memory_id)
            if not record:
                raise KeyError(str(memory_id))
            if record.enrichment_status != "failed":
                raise PolicyError("only failed enrichment can be retried; uncertain matches need review")
            await self.graphiti.driver.execute_query("MATCH (r:MemoryRecord {memory_id:$id}) SET r.enrichment_status='pending', r.previous_enrichment_error=r.enrichment_error, r.previous_enrichment_attempts=coalesce(r.previous_enrichment_attempts,0)+r.enrichment_attempts, r.enrichment_attempts=0", id=str(memory_id))
        return await self.enrichment_details(memory_id)
