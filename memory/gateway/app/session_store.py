import json
from typing import Any

from redis.asyncio import Redis

from schemas.v1.metadata import MemoryCreate, SessionPutRequest


class SessionStore:
    def __init__(self, redis: Redis, default_ttl: int):
        self.redis = redis
        self.default_ttl = default_ttl

    def _key(self, session_id: str) -> str:
        return f"memory:session:{session_id}"

    def _candidates_key(self, session_id: str) -> str:
        return f"memory:session:{session_id}:candidates"

    async def put(self, request: SessionPutRequest) -> dict[str, Any]:
        ttl = request.ttl_seconds or self.default_ttl
        key = self._key(request.session_id)
        await self.redis.hset(key, request.key, json.dumps(request.value, ensure_ascii=False))
        await self.redis.expire(key, ttl)
        if request.candidate:
            ckey = self._candidates_key(request.session_id)
            await self.redis.hset(ckey, request.key, request.candidate.model_dump_json())
            await self.redis.expire(ckey, ttl)
        return {"session_id": request.session_id, "key": request.key, "ttl_seconds": ttl}

    async def get(self, session_id: str, key: str | None = None) -> Any:
        redis_key = self._key(session_id)
        if key:
            raw = await self.redis.hget(redis_key, key)
            return None if raw is None else json.loads(raw)
        values = await self.redis.hgetall(redis_key)
        return {k: json.loads(v) for k, v in values.items()}

    async def search(self, session_id: str, query: str) -> dict[str, Any]:
        values = await self.get(session_id)
        needle = query.casefold()
        return {key: value for key, value in values.items() if needle in json.dumps(value, ensure_ascii=False).casefold()}

    async def candidates(self, session_id: str, keys: list[str] | None = None) -> dict[str, MemoryCreate]:
        values = await self.redis.hgetall(self._candidates_key(session_id))
        selected = set(keys) if keys else None
        return {
            key: MemoryCreate.model_validate_json(value)
            for key, value in values.items()
            if selected is None or key in selected
        }

    async def clear_candidates(self, session_id: str, keys: list[str]) -> None:
        if keys:
            await self.redis.hdel(self._candidates_key(session_id), *keys)

