"""Bounded, request-scoped decision execution. No graph writes or evidence pruning.

The cache is process-local and stores only validated answers (not source text).
It is an optimization, never a durable record of identity. All request evidence
and the versioned decision contract participate in the content hash.
"""
import asyncio
from collections import OrderedDict
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import time

import httpx

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
CONTRACT_VERSION = "graphiti-node-score-v2"


class DecisionError(ValueError):
    """A bounded, content-free diagnostic reason safe to aggregate."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def encoded_request(payload):
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def request_key(payload, endpoint=ENDPOINT):
    # Preserve map order: labels/ordering can affect model judgments.
    return hashlib.sha256(CONTRACT_VERSION.encode() + b"\0" + endpoint.encode()
                          + b"\0" + encoded_request(payload)).hexdigest()


@dataclass
class Flight:
    task: asyncio.Task
    waiters: int = 0


class DecisionBatchRunner:
    def __init__(self, settings, metrics, validate, *, endpoint=ENDPOINT, api_key=None,
                 backend_name="typesafe"):
        self.settings, self.metrics, self.validate = settings, metrics, validate
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key if api_key is not None else settings.typesafe_api_key
        self.backend_name = backend_name
        # One runner is owned by the single Graphiti LLM client in a Gateway.
        # This bound spans simultaneous resolve() calls, not just one batch list.
        self.slots = asyncio.Semaphore(settings.memory_jev_max_concurrency)
        self.cache = OrderedDict()
        self.flights = {}

    def cache_enabled(self, payload):
        return (self.settings.memory_jev_cache_max_entries > 0
                and self.settings.memory_jev_cache_ttl_seconds > 0
                and not payload["model"].endswith(("-latest", "-preview")))

    def cached(self, key):
        now = time.monotonic()
        # Bounded sweep: never retain expired answers indefinitely.
        for old_key, (expires, _) in list(self.cache.items()):
            if expires <= now:
                del self.cache[old_key]
                self.metrics["cache_expired"] += 1
        value = self.cache.get(key)
        if value is None:
            return None
        self.cache.move_to_end(key)
        return deepcopy(value[1])

    async def evaluate(self, payload):
        key = request_key(payload, self.endpoint)
        enabled = self.cache_enabled(payload)
        cached = self.cached(key) if enabled else None
        if cached is not None:
            self.metrics["cache_hits"] += 1
            return cached
        flight = self.flights.get(key)
        if flight is None:
            self.metrics["cache_misses"] += 1
            flight = Flight(asyncio.create_task(self.fetch(payload)))
            self.flights[key] = flight
        else:
            self.metrics["coalesced"] += 1
        flight.waiters += 1
        try:
            # A cancelled caller cannot cancel work still needed by another caller.
            answers = await asyncio.shield(flight.task)
            if enabled:
                self.cache[key] = (time.monotonic() + self.settings.memory_jev_cache_ttl_seconds,
                                   deepcopy(answers))
                self.cache.move_to_end(key)
                while len(self.cache) > self.settings.memory_jev_cache_max_entries:
                    self.cache.popitem(last=False)
                    self.metrics["cache_evictions"] += 1
            return deepcopy(answers)
        finally:
            flight.waiters -= 1
            if flight.waiters == 0:
                if self.flights.get(key) is flight:
                    del self.flights[key]
                if not flight.task.done():
                    flight.task.cancel()
                # Drain the owned task before releasing caller admission; no
                # abandoned requests or unobserved task exceptions after timeout.
                with suppress(asyncio.CancelledError, Exception):
                    await flight.task

    async def fetch(self, payload):
        queued = time.monotonic()
        async with self.slots:
            self.metrics["queue_wait_ms"] += (time.monotonic() - queued) * 1000
            started = time.monotonic()
            self.metrics["active_batches"] += 1
            self.metrics["peak_active_batches"] = max(self.metrics["peak_active_batches"],
                                                      self.metrics["active_batches"])
            self.metrics["jev_calls"] += 1
            try:
                async with httpx.AsyncClient(timeout=self.settings.memory_jev_timeout_seconds) as client:
                    response = await client.post(self.endpoint,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=payload)
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict):
                    raise DecisionError("invalid_response")
                answers = result.get("answers")
                if not isinstance(answers, dict) or set(answers) != set(payload["questions"]):
                    raise DecisionError("invalid_response")
                # Validators return the minimal typed contract to cache. They
                # intentionally discard provider extras and source state.
                answers = {key: self.validate(item) for key, item in answers.items()}
                usage = result.get("usage") or {}
                if isinstance(usage, dict):
                    for key in ("input_tokens", "output_tokens"):
                        value = usage.get(key)
                        if type(value) is int and value >= 0:
                            self.metrics[key] += value
                self.metrics[f"backend_{self.backend_name}_calls"] += 1
                return answers
            finally:
                self.metrics["active_batches"] -= 1
                self.metrics["batch_service_ms"] += (time.monotonic() - started) * 1000


async def evaluate_batches(runner, payloads):
    """Keep ordering and cancel/drain remaining work if any batch fails."""
    tasks = [asyncio.create_task(runner.evaluate(payload)) for payload in payloads]
    try:
        return await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
