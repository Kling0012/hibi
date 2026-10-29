from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


class MemoryType(StrEnum):
    decision = "decision"
    requirement = "requirement"
    constraint = "constraint"
    preference = "preference"
    procedure = "procedure"
    solution = "solution"
    stable_fact = "stable_fact"
    project_configuration = "project_configuration"
    experiment = "experiment"
    result = "result"
    important_artifact = "important_artifact"
    issue = "issue"
    cause = "cause"
    observation = "observation"


class Visibility(StrEnum):
    global_ = "global"
    user = "user"
    project = "project"
    agent_private = "agent_private"


class Scope(BaseModel):
    user: str | None = None
    project: str | None = None
    agent: str | None = None


class Source(BaseModel):
    agent: str
    session: str | None = None
    uri: str | None = None


class MemoryCreate(BaseModel):
    type: MemoryType
    content: str = Field(min_length=1, max_length=12_000)
    scope: Scope
    source: Source
    visibility: Visibility = Visibility.project
    valid_from: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    refs: list[str] = Field(default_factory=list, max_length=50)
    tags: list[str] = Field(default_factory=list, max_length=50)
    name: str | None = Field(default=None, max_length=200)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("content")
    @classmethod
    def content_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value.strip()


class MemoryRecord(MemoryCreate):
    memory_id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    valid_to: datetime | None = None
    schema_version: int = 1
    episode_uuid: str | None = None
    supersedes: UUID | None = None
    superseded_by: UUID | None = None
    enrichment_status: str = "ready"
    enrichment_error: str | None = None


class MemorySearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)
    user: str
    project: str | None = None
    agent: str
    types: list[MemoryType] = Field(default_factory=list)
    include_history: bool = False
    limit: int = Field(default=10, ge=1, le=50)


class MemoryListRequest(BaseModel):
    since: datetime | None = None
    until: datetime | None = None
    field: Literal["created_at", "valid_from"] = "created_at"
    types: list[MemoryType] = Field(default_factory=list)
    agent: str | None = Field(default=None, min_length=1, max_length=80)
    limit: int = Field(default=200, ge=1, le=1000)
    cursor: str | None = Field(default=None, max_length=512)


class SessionPutRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=200)
    key: str = Field(min_length=1, max_length=200)
    value: Any
    ttl_seconds: int | None = Field(default=None, ge=60, le=604_800)
    candidate: MemoryCreate | None = None


class SessionCommitRequest(BaseModel):
    session_id: str
    keys: list[str] | None = None
    clear_committed: bool = True
