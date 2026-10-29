import hashlib
import re

from schemas.v1.metadata import MemoryCreate, Scope, Visibility


class PolicyError(ValueError):
    pass


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip()).strip("-").lower()
    if not cleaned:
        cleaned = hashlib.sha256(value.encode()).hexdigest()[:16]
    return cleaned[:80]


def group_id(scope: Scope, visibility: Visibility) -> str:
    if visibility == Visibility.global_:
        return "global"
    if visibility == Visibility.user:
        if not scope.user:
            raise PolicyError("user visibility requires scope.user")
        return f"user__{_slug(scope.user)}"
    if visibility == Visibility.project:
        if not scope.project:
            raise PolicyError("project visibility requires scope.project")
        suffix = f"__{_slug(scope.agent)}" if scope.agent else "__shared"
        return f"project__{_slug(scope.project)}{suffix}"
    if not scope.agent:
        raise PolicyError("agent_private visibility requires scope.agent")
    return f"agent__{_slug(scope.agent)}__private"


def readable_groups(user: str, project: str | None, agent: str) -> list[str]:
    groups = ["global", f"user__{_slug(user)}", f"agent__{_slug(agent)}__private"]
    if project:
        groups.extend([
            f"project__{_slug(project)}__shared",
            f"project__{_slug(project)}__{_slug(agent)}",
        ])
    return groups


def validate_write(memory: MemoryCreate, actor_agent: str | None = None) -> str:
    if actor_agent and memory.visibility == Visibility.agent_private:
        if memory.scope.agent != actor_agent:
            raise PolicyError("an agent may only write its own private scope")
    return group_id(memory.scope, memory.visibility)


PROMOTABLE_TYPES = {
    "decision", "requirement", "constraint", "preference", "procedure", "solution",
    "stable_fact", "project_configuration", "experiment", "result",
    "important_artifact", "issue", "cause",
}


def should_promote(memory: MemoryCreate) -> bool:
    return memory.type.value in PROMOTABLE_TYPES

