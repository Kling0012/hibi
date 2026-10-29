from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError

from schemas.v1.metadata import (
    MemoryCreate,
    MemoryListRequest,
    MemorySearchRequest,
    SessionCommitRequest,
    SessionPutRequest,
)

from .policy import PolicyError

router = APIRouter()


def service(request: Request):
    return request.app.state.memory_service


@router.get("/health")
async def health(request: Request):
    graph_status = await service(request).graph.status()
    valkey_ok = await service(request).sessions.redis.ping()
    return {"ok": bool(valkey_ok), "service": "agent-memory", "schema_version": 1, "long_term": graph_status}


@router.get("/status")
async def status(request: Request):
    return await service(request).graph.status()


@router.post("/v1/memories")
async def remember(memory: MemoryCreate, request: Request):
    return await service(request).remember(memory, actor_agent=memory.source.agent)


@router.post("/v1/memories/search")
async def memory_search(body: MemorySearchRequest, request: Request):
    return {"results": await service(request).search(body)}


@router.get("/v1/memories")
async def memory_list(
    request: Request,
    since: str | None = None,
    until: str | None = None,
    field: str = "created_at",
    types: str | None = None,
    agent: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    cursor: str | None = None,
):
    """Page canonical records by time range (read-only, shared collection)."""
    try:
        body = MemoryListRequest.model_validate({
            "since": since, "until": until, "field": field,
            "types": [item for item in (types or "").split(",") if item],
            "agent": agent, "limit": limit, "cursor": cursor,
        })
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        return await service(request).list_memories(body)
    except PolicyError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/v1/memories/{memory_id}")
async def memory_get(memory_id: UUID, request: Request):
    result = await service(request).get(memory_id)
    if result is None:
        raise HTTPException(404, "memory not found")
    return result


@router.post("/v1/memories/{memory_id}/supersede")
async def memory_supersede(memory_id: UUID, replacement: MemoryCreate, request: Request):
    try:
        return await service(request).supersede(memory_id, replacement, replacement.source.agent)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/v1/memories/{memory_id}/enrichment")
async def enrichment_status(memory_id: UUID, request: Request):
    try:
        return await service(request).graph.enrichment_details(memory_id)
    except KeyError as exc:
        raise HTTPException(404, "memory not found") from exc


@router.post("/v1/memories/{memory_id}/retry-enrichment")
async def retry_enrichment(memory_id: UUID, request: Request):
    try:
        return await service(request).graph.retry_enrichment(memory_id)
    except KeyError as exc:
        raise HTTPException(404, "memory not found") from exc


@router.post("/v1/memories/{memory_id}/resume-standard")
async def resume_standard(memory_id: UUID, request: Request, allow_reviewed_retry: bool = False):
    from .recovery import resume_standard as recover
    try:
        return await recover(service(request).graph, memory_id, allow_reviewed_retry)
    except KeyError as exc:
        raise HTTPException(404, "memory not found") from exc


@router.delete("/v1/memories/{memory_id}")
async def memory_forget(memory_id: UUID, request: Request):
    if not await service(request).forget(memory_id):
        raise HTTPException(404, "memory not found")
    return {"deleted": True, "memory_id": str(memory_id)}


@router.post("/v1/sessions/put")
async def session_put(body: SessionPutRequest, request: Request):
    return await service(request).session_put(body)


@router.get("/v1/sessions/{session_id}")
async def session_get(session_id: str, request: Request, key: str | None = None):
    return {"session_id": session_id, "data": await service(request).sessions.get(session_id, key)}


@router.get("/v1/sessions/{session_id}/search")
async def session_search(session_id: str, request: Request, q: str = Query(min_length=1)):
    return {"session_id": session_id, "results": await service(request).sessions.search(session_id, q)}


@router.post("/v1/sessions/commit")
async def session_commit(body: SessionCommitRequest, request: Request):
    return await service(request).session_commit(body)
