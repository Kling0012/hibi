import pytest

from gateway.app.policy import PolicyError, group_id, readable_groups, should_promote
from schemas.v1.metadata import MemoryCreate, Scope, Source, Visibility


def test_scope_groups_are_isolated():
    assert group_id(Scope(user="u", project="ALL"), Visibility.project) == "project__all__shared"
    assert group_id(Scope(user="u", agent="codex"), Visibility.agent_private) == "agent__codex__private"
    assert readable_groups("u", "ALL", "codex") == [
        "global", "user__u", "agent__codex__private",
        "project__all__shared", "project__all__codex",
    ]


def test_missing_scope_is_rejected():
    with pytest.raises(PolicyError):
        group_id(Scope(user="u"), Visibility.project)


def test_transient_observation_does_not_auto_promote():
    memory = MemoryCreate(
        type="observation", content="temporary output", scope={"project": "all"},
        source=Source(agent="codex"),
    )
    assert not should_promote(memory)

