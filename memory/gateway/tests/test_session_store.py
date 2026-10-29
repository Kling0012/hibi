import fakeredis.aioredis

from gateway.app.session_store import SessionStore
from schemas.v1.metadata import SessionPutRequest


async def test_session_ttl_and_candidate_round_trip():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    store = SessionStore(redis, 3600)
    request = SessionPutRequest(
        session_id="s1", key="decision", value={"status": "working"},
        candidate={
            "type": "decision", "content": "Use FalkorDB",
            "scope": {"project": "all"},
            "source": {"agent": "codex", "session": "s1"},
        },
    )
    await store.put(request)
    assert await store.get("s1", "decision") == {"status": "working"}
    candidates = await store.candidates("s1")
    assert candidates["decision"].content == "Use FalkorDB"
    assert await redis.ttl("memory:session:s1") > 0

