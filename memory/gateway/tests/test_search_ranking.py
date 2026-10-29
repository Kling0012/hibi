"""Canonical search mapping regressions; no database or model calls are performed."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import NAMESPACE_URL, uuid5

import pytest

from gateway.app.graph_store import GraphStore
from gateway.app.policy import group_id, readable_groups
from schemas.v1.metadata import MemoryRecord, MemorySearchRequest


ACTOR = {"user": "default", "project": "all", "agent": "codex"}
REFERENCE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def record(label, *, project="all", kind="stable_fact", valid_to=None, valid_from=REFERENCE):
    return MemoryRecord(
        memory_id=uuid5(NAMESPACE_URL, f"urn:search-ranking:{label}"),
        type=kind, content=f"Synthetic evidence {label}", name=label,
        scope={"user": "default", "project": project, "agent": None},
        source={"agent": "codex", "session": "search-ranking-test"},
        visibility="project", valid_from=valid_from, valid_to=valid_to,
        created_at=valid_from, episode_uuid=f"episode-{label}",
    )


def edge(label, records, *, invalid_at=None, valid_at=REFERENCE):
    return SimpleNamespace(
        fact=f"Matched synthetic fact {label}",
        episodes=[item.episode_uuid for item in records],
        valid_at=valid_at, invalid_at=invalid_at,
    )


def search_store(records_in_storage_order, ranked_edges):
    """Model only the relevant read-query filters, including the old premature LIMIT.

    This fake preserves storage order deliberately: an unordered Cypher lookup
    cannot be assumed to return rows in the rank of the supplied episode IDs.
    Real record serialization is not under test here; output uses MemoryRecord.
    """
    async def query(statement, **params):
        rows = [item for item in records_in_storage_order
                if item.episode_uuid in params.get("episodes", [])
                and group_id(item.scope, item.visibility) in params["groups"]]
        if "r.valid_to = ''" in statement:
            rows = [item for item in rows if item.valid_to is None]
        if "r.type IN $types" in statement:
            rows = [item for item in rows if item.type.value in params["types"]]
        if "LIMIT $limit" in statement:
            rows = rows[:params["limit"]]
        return rows, None, None

    store = GraphStore.__new__(GraphStore)
    store.graphiti = SimpleNamespace(
        search=AsyncMock(return_value=ranked_edges),
        driver=SimpleNamespace(execute_query=AsyncMock(side_effect=query)),
    )
    store._record_from_row = lambda row: row
    return store


def request(**kwargs):
    return MemorySearchRequest(query="synthetic query", **ACTOR, **kwargs)


def names(results):
    return [item["memory"]["name"] for item in results]


async def test_storage_order_cannot_drop_top_ranked_canonical_memories():
    records = [record(str(i)) for i in range(1, 7)]
    ranked = [edge(str(i), [records[i - 1]]) for i in (5, 4, 6, 3, 1, 2)]
    store = search_store(records, ranked)

    results = await store.search(request(limit=3))

    assert names(results) == ["5", "4", "6"]
    assert [item["matched_fact"] for item in results] == [ranked[i].fact for i in range(3)]
    store.graphiti.search.assert_awaited_once_with(
        "synthetic query", group_ids=readable_groups(**ACTOR), num_results=9,
    )


async def test_seen_first_provenance_does_not_hide_an_unseen_source():
    first, second = record("first"), record("second")
    ranked = [edge("first-only", [first]), edge("shared", [first, second])]
    store = search_store([first, second], ranked)

    results = await store.search(request(limit=3))

    assert names(results) == ["first", "second"]
    assert results[1]["matched_fact"] == ranked[1].fact


async def test_provenance_order_is_not_replaced_by_unconditional_newest_preference():
    older = record("older", valid_from=REFERENCE)
    newer = record("newer", valid_from=REFERENCE + timedelta(days=10))
    store = search_store([newer, older], [edge("shared", [older, newer])])

    results = await store.search(request(limit=1))

    assert names(results) == ["older"]


async def test_missing_first_provenance_can_use_the_available_source():
    missing, available = record("missing"), record("available")
    store = search_store([available], [edge("shared", [missing, available])])

    assert names(await store.search(request(limit=3))) == ["available"]


async def test_scope_and_type_filters_are_preserved_before_provenance_selection():
    foreign = record("foreign", project="other-project")
    wrong_type = record("wrong-type", kind="preference")
    eligible = record("eligible")
    store = search_store(
        [foreign, wrong_type, eligible], [edge("shared", [foreign, wrong_type, eligible])],
    )

    results = await store.search(request(limit=3, types=["stable_fact"]))

    assert names(results) == ["eligible"]
    call = store.graphiti.driver.execute_query.await_args
    assert "r.group_id IN $groups" in call.args[0]
    assert "r.type IN $types" in call.args[0]
    assert call.kwargs["groups"] == readable_groups(**ACTOR)
    assert call.kwargs["types"] == ["stable_fact"]


@pytest.mark.parametrize("include_history", [False, True])
async def test_canonical_history_filter_is_preserved(include_history):
    closed = record("closed", valid_to=REFERENCE + timedelta(days=1))
    current = record("current")
    store = search_store([closed, current], [edge("closed", [closed]), edge("current", [current])])

    results = await store.search(request(limit=3, include_history=include_history))

    assert names(results) == (["closed", "current"] if include_history else ["current"])
    query = store.graphiti.driver.execute_query.await_args.args[0]
    assert ("r.valid_to = ''" in query) is (not include_history)
    if include_history:
        assert results[0]["fact_valid_to"] == closed.valid_to.isoformat()


@pytest.mark.parametrize("include_history", [False, True])
async def test_mapping_fix_does_not_add_a_new_fact_invalidation_policy(include_history):
    old, current = record("old-port"), record("current-port")
    boundary = datetime(2000, 1, 1, tzinfo=timezone.utc)
    ranked = [edge("old-port", [old], invalid_at=boundary), edge("current-port", [current])]
    store = search_store([old, current], ranked)

    results = await store.search(request(limit=3, include_history=include_history))

    # This isolated fix preserves the old production behavior. Edge-level
    # invalidation policy remains a separate change because it caused
    # regressions in the P1 evaluation.
    assert names(results) == ["old-port", "current-port"]
    assert results[0]["fact_valid_to"] == boundary.isoformat()


async def test_future_invalidation_does_not_hide_a_current_fact():
    current = record("still-current")
    future = datetime(2999, 1, 1, tzinfo=timezone.utc)
    store = search_store([current], [edge("future-boundary", [current], invalid_at=future)])

    results = await store.search(request(limit=3))

    assert names(results) == ["still-current"]
    assert results[0]["fact_valid_to"] == future.isoformat()


async def test_future_valid_from_behavior_is_not_changed_by_mapping_bugfix():
    scheduled = record("scheduled")
    future = datetime(2999, 1, 1, tzinfo=timezone.utc)
    store = search_store([scheduled], [edge("scheduled", [scheduled], valid_at=future)])

    results = await store.search(request(limit=3))

    assert names(results) == ["scheduled"]
    assert results[0]["fact_valid_from"] == future.isoformat()


async def test_deduplication_keeps_the_first_ranked_fact_and_output_contract():
    one, two = record("one"), record("two")
    ranked = [edge("one-best", [one]), edge("one-again", [one]), edge("two", [two])]
    store = search_store([one, two], ranked)

    results = await store.search(request(limit=3))

    assert names(results) == ["one", "two"]
    assert results[0]["matched_fact"] == ranked[0].fact
    assert set(results[0]) == {"memory", "enrichment_status", "matched_fact", "fact_valid_from", "fact_valid_to"}
    assert MemoryRecord.model_validate(results[0]["memory"]) == one


async def test_no_edges_do_not_trigger_a_canonical_lookup():
    store = search_store([], [])

    assert await store.search(request(limit=3)) == []
    store.graphiti.driver.execute_query.assert_not_awaited()
