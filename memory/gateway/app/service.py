import base64
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from schemas.v1.metadata import (
    MemoryCreate,
    MemoryListRequest,
    MemorySearchRequest,
    SessionCommitRequest,
    SessionPutRequest,
)

from .graph_store import GraphStore
from .policy import PolicyError, should_promote
from .session_store import SessionStore


def encode_list_cursor(value: datetime, memory_id: str) -> str:
    payload = json.dumps({"v": value.isoformat(), "i": memory_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_list_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.fromisoformat(payload["v"]), str(payload["i"])
    except (ValueError, KeyError, TypeError) as exc:
        raise PolicyError("invalid cursor") from exc


class MemoryService:
    def __init__(self, graph: GraphStore, sessions: SessionStore):
        self.graph = graph
        self.sessions = sessions

    async def remember(self, memory: MemoryCreate, actor_agent: str | None = None) -> dict[str, Any]:
        record = await self.graph.remember(memory, actor_agent=actor_agent)
        return self._write_response(record)

    @staticmethod
    def _acceptance_message(status: str) -> str:
        if status == "ready":
            return "Saved and ready; check status with memory_get(memory_id)."
        if status in ("pending", "processing"):
            return "Accepted. Continue without waiting; check status with memory_get(memory_id)."
        return "Canonical record exists; check enrichment status with memory_get(memory_id)."

    @staticmethod
    def _write_response(record) -> dict[str, Any]:
        result = record.model_dump(mode="json")
        result["acceptance"] = MemoryService._acceptance_message(record.enrichment_status)
        return result

    async def search(self, request: MemorySearchRequest) -> list[dict[str, Any]]:
        return await self.graph.search(request)

    async def get(self, memory_id: UUID) -> dict[str, Any] | None:
        record = await self.graph.get(memory_id)
        return record.model_dump(mode="json") if record else None

    async def list_memories(self, request: MemoryListRequest) -> dict[str, Any]:
        """Read-only chronological paging over the shared collection."""
        settings = self.graph.settings
        records = await self.graph.list_records(
            user=settings.memory_default_user, project=settings.memory_default_project,
            agent=request.agent or "agent",
            since=request.since, until=request.until, field=request.field,
            types=request.types, agent_filter=request.agent,
            cursor=decode_list_cursor(request.cursor) if request.cursor else None,
            limit=request.limit + 1,
        )
        next_cursor = None
        if len(records) > request.limit:
            records = records[:request.limit]
            last = records[-1]
            next_cursor = encode_list_cursor(
                getattr(last, request.field), str(last.memory_id),
            )
        return {
            "items": [record.model_dump(mode="json") for record in records],
            "next_cursor": next_cursor,
        }

    async def supersede(
        self, memory_id: UUID, replacement: MemoryCreate, actor_agent: str | None = None,
    ) -> dict[str, Any]:
        record = await self.graph.supersede(memory_id, replacement, actor_agent=actor_agent)
        return self._write_response(record)

    async def forget(self, memory_id: UUID) -> bool:
        return await self.graph.forget(memory_id)

    async def session_put(self, request: SessionPutRequest) -> dict[str, Any]:
        return await self.sessions.put(request)

    async def session_commit(self, request: SessionCommitRequest) -> dict[str, Any]:
        candidates = await self.sessions.candidates(request.session_id, request.keys)
        committed: list[dict[str, Any]] = []
        discarded: list[dict[str, str]] = []
        for key, memory in candidates.items():
            if not should_promote(memory):
                discarded.append({"key": key, "reason": "type is short-lived by policy"})
                continue
            record = await self.graph.remember(memory, actor_agent=memory.source.agent)
            item = {"key": key, "memory_id": str(record.memory_id), "enrichment_status": record.enrichment_status}
            item["acceptance"] = self._acceptance_message(record.enrichment_status)
            committed.append(item)
        if request.clear_committed:
            await self.sessions.clear_candidates(request.session_id, [item["key"] for item in committed])
        return {"session_id": request.session_id, "committed": committed, "discarded": discarded}
