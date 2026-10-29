import logging
import time
from asyncio import CancelledError
from collections.abc import Callable, Iterable

from graphiti_core.embedder.client import EmbedderClient
from openai import APIConnectionError, APITimeoutError, RateLimitError


logger = logging.getLogger(__name__)


_ERROR_CLASSES = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "APIStatusError",
        "BadRequestError",
        "AuthenticationError",
        "PermissionDeniedError",
        "NotFoundError",
        "ConflictError",
        "UnprocessableEntityError",
        "InternalServerError",
        "APIResponseValidationError",
        "TimeoutError",
        "ConnectionError",
        "ValueError",
        "TypeError",
        "CancelledError",
    }
)


def _error_details(error: BaseException, elapsed_seconds: float) -> dict:
    """Only bounded labels and numeric status; never retain exception contents."""
    name = type(error).__name__
    try:
        status = getattr(error, "status_code", None)
    except Exception:
        status = None
    if type(status) is not int or not 100 <= status <= 599:
        status = None
    return {
        "class": name if name in _ERROR_CLASSES else "other",
        "status": status,
        "elapsed_seconds": max(0.0, elapsed_seconds),
    }


class MetricsEmbedder(EmbedderClient):
    """Observe one provider without retrying, transforming inputs, or hiding errors.

    Calls count completed delegate operations, not HTTP attempts or embedded texts.
    Elapsed time includes waits/retries inside the delegate. Failures include
    cancellations, also counted separately. Snapshots exclude in-flight durations.
    """

    def __init__(
        self,
        delegate: EmbedderClient,
        *,
        provider: str,
        clock: Callable[[], float] = time.monotonic,
    ):
        if provider not in {"cloudflare", "local"}:
            raise ValueError("embedding metrics provider must be cloudflare or local")
        self.delegate = delegate
        self.provider = provider
        self.clock = clock
        self.calls = 0
        self.create_calls = 0
        self.batch_calls = 0
        self.active_calls = 0
        self.failures = 0
        self.cancelled = 0
        self.seconds_total = 0.0
        self.seconds_max = 0.0
        self.error_classes: dict[str, int] = {}
        self.http_statuses: dict[str, int] = {}
        self.last_error: dict | None = None

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    async def _call(self, method: str, *args):
        started = self.clock()
        self.active_calls += 1
        error = None
        try:
            return await getattr(self.delegate, method)(*args)
        except BaseException as caught:
            error = caught
            raise
        finally:
            elapsed = max(0.0, self.clock() - started)
            self.active_calls -= 1
            self.calls += 1
            if method == "create":
                self.create_calls += 1
            else:
                self.batch_calls += 1
            self.seconds_total += elapsed
            self.seconds_max = max(self.seconds_max, elapsed)
            if error is not None:
                self.failures += 1
                self.cancelled += isinstance(error, CancelledError)
                details = _error_details(error, elapsed)
                self.last_error = details
                label = details["class"]
                self.error_classes[label] = self.error_classes.get(label, 0) + 1
                status = str(details["status"]) if details["status"] is not None else "none"
                self.http_statuses[status] = self.http_statuses.get(status, 0) + 1

    def snapshot(self) -> dict:
        """Return independent aggregates; last_error is historical, not health."""
        return {
            "kind": "provider",
            "provider": self.provider,
            "calls": self.calls,
            "create_calls": self.create_calls,
            "batch_calls": self.batch_calls,
            "active_calls": self.active_calls,
            "failures": self.failures,
            "cancelled": self.cancelled,
            "seconds_total": self.seconds_total,
            "seconds_max": self.seconds_max,
            "error_classes": dict(self.error_classes),
            "http_statuses": dict(self.http_statuses),
            "last_error": dict(self.last_error) if self.last_error is not None else None,
        }

    async def create(
        self, input_data: str | list[str] | Iterable[int] | Iterable[Iterable[int]]
    ) -> list[float]:
        return await self._call("create", input_data)

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        return await self._call("create_batch", input_data_list)


def should_fallback(error: Exception) -> bool:
    """Return true only for quota, rate-limit, and transient provider failures."""
    if isinstance(error, (APIConnectionError, APITimeoutError, RateLimitError)):
        return True
    status_code = getattr(error, "status_code", None)
    return status_code == 429 or (isinstance(status_code, int) and status_code >= 500)


class FailoverEmbedder(EmbedderClient):
    """Use a primary embedder, opening a short local-fallback circuit on transient errors."""

    def __init__(
        self,
        primary: EmbedderClient,
        fallback: EmbedderClient,
        *,
        cooldown_seconds: float = 300,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.primary = primary
        self.fallback = fallback
        self.cooldown_seconds = cooldown_seconds
        self.clock = clock
        self.fallback_until = 0.0
        self.primary_failures = 0
        self.fallback_uses = 0
        self.cooldown_uses = 0
        self.last_primary_error: dict | None = None

    def snapshot(self) -> dict:
        """Routing counts are attempted uses; provider counts are completions.

        The historical primary error survives a subsequent fallback error or
        successful recovery. No exception messages or arbitrary delegate state
        are exposed. Uninstrumented providers have a null snapshot.
        """
        remaining = max(0.0, self.fallback_until - self.clock())
        return {
            "kind": "failover",
            "primary": self.primary.snapshot() if isinstance(self.primary, MetricsEmbedder) else None,
            "fallback": self.fallback.snapshot() if isinstance(self.fallback, MetricsEmbedder) else None,
            "primary_failures": self.primary_failures,
            "fallback_uses": self.fallback_uses,
            "cooldown_uses": self.cooldown_uses,
            "circuit_open": remaining > 0.0,
            "cooldown_remaining_seconds": remaining,
            "last_primary_error": (
                dict(self.last_primary_error) if self.last_primary_error is not None else None
            ),
        }

    async def _call(self, method: str, *args):
        now = self.clock()
        if now < self.fallback_until:
            self.fallback_uses += 1
            self.cooldown_uses += 1
            return await getattr(self.fallback, method)(*args)

        try:
            return await getattr(self.primary, method)(*args)
        except BaseException as error:
            self.primary_failures += 1
            self.last_primary_error = _error_details(error, self.clock() - now)
            if not isinstance(error, Exception) or not should_fallback(error):
                raise
            self.fallback_until = now + self.cooldown_seconds
            logger.warning(
                "embedding primary unavailable; using local fallback for %.0f seconds "
                "(error=%s status=%s)",
                self.cooldown_seconds,
                self.last_primary_error["class"],
                self.last_primary_error["status"],
            )
            self.fallback_uses += 1
            return await getattr(self.fallback, method)(*args)

    async def create(
        self, input_data: str | list[str] | Iterable[int] | Iterable[Iterable[int]]
    ) -> list[float]:
        return await self._call("create", input_data)

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        return await self._call("create_batch", input_data_list)
