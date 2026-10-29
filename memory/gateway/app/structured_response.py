"""Validate completed provider output before Graphiti receives a JSON object.

This boundary owns only JSON/schema repair, not transport retries or factual
correction. The supplied ``create`` callable must have SDK retries disabled if
the caller requires a hard limit of two HTTP attempts. Provider failures and
cancellation propagate unchanged; they never trigger a format-repair request.
"""

import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ValidationError


logger = logging.getLogger(__name__)

FailureReason = Literal[
    "invalid_json", "wrong_root", "schema_mismatch", "incomplete_response", "refusal"
]

FORMAT_REPAIR_INSTRUCTION = (
    "The previous completion did not satisfy the required JSON response format. "
    "Return exactly one JSON object containing data that matches the supplied schema, "
    "not the JSON Schema definition itself. Include every required field with its "
    "declared JSON value type. Do not invent facts to fill fields. Empty lists, empty "
    "strings, and null are allowed only where the schema allows them and the input "
    "supports them. Do not add explanations or wrap the object in an array."
)

METRIC_KEYS = frozenset({
    "calls", "format_repairs", "rejections", "transport_failures",
    "reason_invalid_json", "reason_wrong_root", "reason_schema_mismatch",
    "reason_incomplete_response", "reason_refusal",
})


def _count(metrics: dict[str, int] | None, key: str) -> None:
    if metrics is not None:
        # Internal fixed keys only: never derive labels from provider text,
        # exception messages, response fields, schemas, or user input.
        assert key in METRIC_KEYS
        metrics[key] += 1


class StructuredResponseError(ValueError):
    """Content-free failure, intentionally not a Graphiti-retryable JSON error."""

    def __init__(self, reason: FailureReason):
        self.reason = reason
        super().__init__(f"structured response rejected: {reason}")


def _reject_non_json_number(value: str) -> None:
    # Python accepts NaN/Infinity by default, but those are not JSON values.
    raise ValueError("non-JSON number")


def _parse_and_validate(
    content: str,
    response_model: type[BaseModel] | None,
    strip_code_fences: Callable[[str], str],
) -> tuple[dict[str, Any] | None, FailureReason | None]:
    # Do not retain decoder/ValidationError objects: they can contain the entire
    # provider response, including arbitrary sensitive input and field names.
    try:
        result = json.loads(strip_code_fences(content), parse_constant=_reject_non_json_number)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return None, "invalid_json"
    if not isinstance(result, dict):
        return None, "wrong_root"
    if response_model is not None:
        # Models with only optional fields can accept a schema echo because
        # Pydantic ignores unknown keys by default. Reject the exact requested
        # schema only; do not guess from field names such as type/properties.
        if result == response_model.model_json_schema():
            return None, "schema_mismatch"
        try:
            response_model.model_validate(result, strict=True)
        except ValidationError:
            return None, "schema_mismatch"
    # Validation is a gate, not a rewrite: do not insert model defaults, adopt
    # coercions, remove provider fields, or fabricate missing required values.
    return result, None


async def generate_validated_json(
    create: Callable[..., Awaitable[Any]],
    *,
    messages: Sequence[Mapping[str, Any]],
    response_model: type[BaseModel] | None,
    request_kwargs: Mapping[str, Any],
    strip_code_fences: Callable[[str], str],
    metrics: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Make at most two calls, repairing only a completed output's format.

    ``response_model`` is the actual Graphiti/Pydantic contract, not a parallel
    schema. Strict validation rejects type coercions; schema-defined optional
    fields remain optional. An absent model still requires a JSON object.
    Incomplete/refused responses fail closed without repair. No failed answer or
    validation detail is put into logs, exceptions, or the second prompt.

    Optional counters contain fixed labels only. ``calls`` counts invocations of
    ``create``, including cancellation; ``format_repairs`` counts repair attempts;
    ``reason_*`` counts each invalid response; ``rejections`` counts terminal
    output rejections, not recovered responses. ``transport_failures`` counts
    provider-request exceptions, including unsupported parameters, not cancellation.
    """
    if "messages" in request_kwargs:
        raise ValueError("messages must be supplied separately")
    if metrics is not None:
        for key in METRIC_KEYS:
            metrics.setdefault(key, 0)
    original_messages = deepcopy(list(messages))
    original_kwargs = deepcopy(dict(request_kwargs))
    for attempt in range(2):
        request_messages = deepcopy(original_messages)
        if attempt:
            request_messages.append({"role": "system", "content": FORMAT_REPAIR_INSTRUCTION})
            _count(metrics, "format_repairs")
        # Keep this await outside the format-error handling. No transport retry,
        # compatibility downgrade, refusal bypass, or cancellation swallowing.
        _count(metrics, "calls")
        try:
            response = await create(messages=request_messages, **deepcopy(original_kwargs))
        except Exception:
            _count(metrics, "transport_failures")
            raise
        choices = getattr(response, "choices", None)
        if not isinstance(choices, list) or not choices:
            _count(metrics, "reason_incomplete_response")
            _count(metrics, "rejections")
            raise StructuredResponseError("incomplete_response")
        choice = choices[0]
        message = getattr(choice, "message", None)
        if getattr(message, "refusal", None):
            _count(metrics, "reason_refusal")
            _count(metrics, "rejections")
            raise StructuredResponseError("refusal")
        if getattr(choice, "finish_reason", None) != "stop":
            _count(metrics, "reason_incomplete_response")
            _count(metrics, "rejections")
            raise StructuredResponseError("incomplete_response")
        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            _count(metrics, "reason_incomplete_response")
            _count(metrics, "rejections")
            raise StructuredResponseError("incomplete_response")
        result, reason = _parse_and_validate(content, response_model, strip_code_fences)
        if reason is None:
            assert result is not None
            return result
        _count(metrics, "reason_" + reason)
        if attempt:
            _count(metrics, "rejections")
            raise StructuredResponseError(reason)
        logger.warning("structured response format repair reason=%s", reason)
    raise AssertionError("unreachable structured-response attempt state")
