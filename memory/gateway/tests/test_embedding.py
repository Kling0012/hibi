from unittest.mock import AsyncMock

import pytest

from gateway.app.embedding import FailoverEmbedder, should_fallback


class StatusError(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code


def test_only_transient_status_errors_trigger_fallback():
    assert should_fallback(StatusError(429))
    assert should_fallback(StatusError(503))
    assert not should_fallback(StatusError(400))
    assert not should_fallback(StatusError(401))
    assert not should_fallback(StatusError(403))


async def test_rate_limit_opens_fallback_circuit_for_subsequent_requests():
    now = [100.0]
    primary = type("Primary", (), {})()
    fallback = type("Fallback", (), {})()
    primary.create = AsyncMock(side_effect=[StatusError(429), [3.0]])
    fallback.create = AsyncMock(return_value=[2.0])
    embedder = FailoverEmbedder(
        primary,
        fallback,
        cooldown_seconds=60,
        clock=lambda: now[0],
    )

    assert await embedder.create("first") == [2.0]
    assert await embedder.create("second") == [2.0]
    assert primary.create.await_count == 1

    now[0] = 161.0
    assert await embedder.create("third") == [3.0]
    assert primary.create.await_count == 2


async def test_permanent_primary_error_is_not_hidden_by_fallback():
    primary = type("Primary", (), {})()
    fallback = type("Fallback", (), {})()
    primary.create_batch = AsyncMock(side_effect=StatusError(401))
    fallback.create_batch = AsyncMock(return_value=[[2.0]])
    embedder = FailoverEmbedder(primary, fallback)

    with pytest.raises(StatusError):
        await embedder.create_batch(["text"])
    fallback.create_batch.assert_not_awaited()
