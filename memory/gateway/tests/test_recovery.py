import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid5

import pytest
from schemas.v1.metadata import MemoryRecord
from gateway.app.policy import PolicyError
from gateway.app.recovery import resume_standard


def fixture(status="uncertain", partial=0):
    record = MemoryRecord(type="decision", content="Use AmberDB", scope={"project":"test"}, source={"agent":"codex"}, enrichment_status=status)
    driver = SimpleNamespace(execute_query=AsyncMock(side_effect=[([{"plan":json.dumps({"status":"uncertain"})}],None,None), ([{"id":str(record.memory_id)}],None,None), ([],None,None)]))
    scoped = SimpleNamespace(execute_query=AsyncMock(side_effect=[([{"uuid":"partial"}] if partial else [],None,None),([{"count":0}],None,None)]))
    result = SimpleNamespace(episode=SimpleNamespace(uuid=str(uuid5(record.memory_id,"graphiti-standard-resume"))), nodes=[], edges=[])
    store = SimpleNamespace(ingestor=None,settings=SimpleNamespace(memory_ingestion_mode="graphiti"),get=AsyncMock(return_value=record),add_record_episode=AsyncMock(return_value=result),graphiti=SimpleNamespace(driver=driver,llm_client=SimpleNamespace(node_router=None),_resolve_request_scope=lambda group:(None,scoped,None)))
    return store,record


async def test_recovery_keeps_canonical_id_and_does_not_create_record():
    store,record = fixture()
    result = await resume_standard(store, record.memory_id)
    assert result["status"] == "ready" and result["memory_id"] == str(record.memory_id)
    assert store.add_record_episode.await_args.args[0] is record
    assert store.add_record_episode.await_args.kwargs["episode_uuid"] == str(uuid5(record.memory_id,"graphiti-standard-resume"))
    assert all("CREATE" not in call.args[0] for call in store.graphiti.driver.execute_query.await_args_list)


@pytest.mark.parametrize("status,partial", [("ready",0),("processing_standard",0),("review_required",0),("uncertain",1)])
async def test_recovery_rejects_replay_and_partial_graph(status,partial):
    store,record = fixture(status,partial)
    with pytest.raises(PolicyError):
        await resume_standard(store,record.memory_id)
    store.add_record_episode.assert_not_awaited()


async def test_failed_recovery_preserves_data_and_requires_review():
    store,record = fixture()
    store.add_record_episode.side_effect = RuntimeError("private provider content")
    with pytest.raises(RuntimeError):
        await resume_standard(store,record.memory_id)
    call = store.graphiti.driver.execute_query.await_args
    assert "review_required" in call.args[0]
    assert call.kwargs["error"] == "RuntimeError"


async def test_fixed_uuid_seeds_episode_before_standard_graphiti(monkeypatch):
    from gateway.app.graph_store import GraphStore
    from graphiti_core.nodes import EpisodicNode
    _,record = fixture()
    driver = object()
    events = []
    async def save(episode, target):
        assert target is driver
        assert json.loads(episode.content)["memory_id"] == str(record.memory_id)
        events.append(("seed",episode.uuid))
    async def add(**kwargs):
        events.append(("standard",kwargs["uuid"]))
        assert kwargs["entity_types"]
        assert kwargs["previous_episode_uuids"] == ["older"]
        return "result"
    monkeypatch.setattr(EpisodicNode,"save",save)
    store = GraphStore.__new__(GraphStore)
    store.graphiti = SimpleNamespace(_resolve_request_scope=lambda group:(None,driver,None), add_episode=AsyncMock(side_effect=add), retrieve_episodes=AsyncMock(return_value=[SimpleNamespace(uuid="older")]))
    fixed = str(uuid5(record.memory_id,"graphiti-standard-resume"))
    assert await store.add_record_episode(record,fixed) == "result"
    assert events == [("seed",fixed),("standard",fixed)]


async def test_reviewed_retry_only_accepts_pristine_seed():
    store,record = fixture("review_required")
    seed = {"uuid":str(uuid5(record.memory_id,"graphiti-standard-resume")), "content":json.dumps({"memory_id":str(record.memory_id),"content":record.content}), "edges":[], "links":0}
    scoped = store.graphiti._resolve_request_scope("unused")[1]
    scoped.execute_query.side_effect = [([seed],None,None),([{"count":0}],None,None)]
    assert (await resume_standard(store,record.memory_id,True))["status"] == "ready"
    store,record = fixture("review_required")
    scoped = store.graphiti._resolve_request_scope("unused")[1]
    scoped.execute_query.side_effect = [([],None,None),([{"count":1}],None,None)]
    with pytest.raises(PolicyError,match="facts already"):
        await resume_standard(store,record.memory_id,True)
    store.add_record_episode.assert_not_awaited()
