"""Backend selection for the shared System One decision contract.

The backend supplies typed judgments only. Callers keep thresholds, fallback,
graph writes and rollout policy in ordinary Python code.
"""
from dataclasses import dataclass

from .decision_batch import ENDPOINT


@dataclass(frozen=True)
class BackendConfig:
    name: str
    endpoint: str
    api_key: str | None
    model: str


def get_decision_backend(settings) -> BackendConfig:
    if settings.memory_decision_backend == "typesafe":
        return BackendConfig("typesafe", ENDPOINT, settings.typesafe_api_key,
                             settings.memory_typesafe_model)
    if not settings.memory_decision_adapter_base_url:
        raise ValueError("MEMORY_DECISION_ADAPTER_BASE_URL is required for adapter backend")
    endpoint = settings.memory_decision_adapter_base_url.rstrip("/")
    if not endpoint.endswith("/v1/systemone"):
        endpoint += "/v1/systemone"
    return BackendConfig(
        "adapter", endpoint, settings.memory_decision_adapter_api_key,
        settings.memory_decision_adapter_model or settings.memory_typesafe_model,
    )
