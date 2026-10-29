"""Pydantic request/response schemas."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator

from . import config
from .models import EVENT_KINDS


class DeviceIn(BaseModel):
    name: str = ""
    os: str = ""


class EventIn(BaseModel):
    id: uuid.UUID
    kind: str = Field(pattern="^(activity|screen_summary|note|todo|ai_work|report|away)$")
    started_at: datetime
    ended_at: datetime | None = None
    app: str | None = None
    title: str | None = None
    text: str = ""
    meta: dict = Field(default_factory=dict)
    source: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _away_text(cls, data):
        # Desktop away records have interval/reason metadata but no prose.
        if isinstance(data, dict) and "text" not in data and data.get("kind") != "away":
            raise ValueError("text is required for non-away events")
        return data

    @field_validator("id", mode="before")
    @classmethod
    def _normalize_id(cls, v):
        if isinstance(v, str) and v:
            try:
                uuid.UUID(v)
            except ValueError:
                return uuid.uuid5(uuid.NAMESPACE_URL, "hibi-event:" + v)
        return v

    @field_validator("started_at", "ended_at")
    @classmethod
    def _local_if_naive(cls, v: datetime | None) -> datetime | None:
        # Offset-less timestamps from clients are local wall-clock time, not UTC.
        if v is not None and v.tzinfo is None:
            return v.replace(tzinfo=ZoneInfo(config.TZ_DEFAULT))
        return v


class EventBatchIn(BaseModel):
    device_id: str
    events: list[EventIn] = Field(max_length=500)


class BatchResult(BaseModel):
    accepted: int
    updated: int


class EventOut(BaseModel):
    id: uuid.UUID
    device_id: str
    device_name: str | None = None
    kind: str
    started_at: datetime
    ended_at: datetime | None
    app: str | None
    title: str | None
    text: str
    meta: dict
    source: str | None
    session_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EventsPage(BaseModel):
    events: list[EventOut]
    next_cursor: str | None = None


class DayCounts(BaseModel):
    kind: str
    count: int


class DayResponse(BaseModel):
    date: str
    tz: str
    from_at: datetime
    to_at: datetime
    events: list[EventOut]
    counts: list[DayCounts]


class SearchIn(BaseModel):
    query: str = Field(min_length=1)
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None
    kinds: list[str] | None = None
    device_ids: list[str] | None = None
    k: int = Field(default=20, ge=1, le=200)


class SearchHit(BaseModel):
    event: EventOut
    score: float
    vector_rank: int | None = None
    trgm_rank: int | None = None


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]


class HealthResponse(BaseModel):
    ok: bool
    db: str
    embed_pending: int


class SessionCreateIn(BaseModel):
    title: str = Field(min_length=1)
    note: str | None = None
    device_id: str | None = None
    started_at: datetime | None = None  # default: now


class SessionEndIn(BaseModel):
    ended_at: datetime | None = None  # default: now


class SessionPatchIn(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    note: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    confirmed: bool | None = None


class SessionOut(BaseModel):
    id: uuid.UUID
    device_id: str | None
    title: str
    note: str | None
    started_at: datetime
    ended_at: datetime | None
    status: str
    end_reason: str | None
    end_confidence: float | None
    end_evidence: list[dict]
    confirmed: bool
    duration_minutes: float = 0.0
    away_minutes: float = 0.0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SessionCreateOut(BaseModel):
    session: SessionOut
    superseded: SessionOut | None = None


class SessionsPage(BaseModel):
    sessions: list[SessionOut]


class ActiveSessionOut(BaseModel):
    session: SessionOut | None = None


class SessionTotal(BaseModel):
    session_id: uuid.UUID
    title: str
    status: str
    confirmed: bool
    started_at: datetime
    ended_at: datetime | None
    minutes: float
    away_minutes: float = 0.0


class TimelineItem(BaseModel):
    type: str  # "session" | "event" | "ai_work"
    t: datetime
    event: EventOut | None = None
    session: SessionOut | None = None


class TimelineTodoEntry(BaseModel):
    """One todo time entry on the timeline (GET /v1/timeline)."""

    id: uuid.UUID
    todo_id: uuid.UUID
    todo_title: str
    started_at: datetime
    ended_at: datetime
    minutes: float


class TimelineResponse(BaseModel):
    date: str
    tz: str
    from_at: datetime
    to_at: datetime
    items: list[TimelineItem]
    session_totals: list[SessionTotal]
    total_minutes: float
    todo_entries: list[TimelineTodoEntry] = []


# --- Todos (P6): user/AI todo list with auto time tracking ---


class TodoCreateIn(BaseModel):
    """POST /v1/todos request body."""

    title: str = Field(min_length=1, max_length=200)
    note: str | None = None
    urgency: str = Field(default="medium", pattern="^(high|medium|low)$")
    due_date: date | None = None
    source: str = Field(default="me", pattern="^(me|auto)$")
    evidence: str | None = None
    device_id: str | None = None


class TodoPatchIn(BaseModel):
    """PATCH /v1/todos/{id} request body (only given fields change)."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    note: str | None = None
    urgency: str | None = Field(default=None, pattern="^(high|medium|low)$")
    due_date: date | None = None
    status: str | None = Field(default=None, pattern="^(open|done|archived)$")


class TodoOut(BaseModel):
    id: uuid.UUID
    title: str
    note: str | None
    status: str
    source: str
    urgency: str
    due_date: date | None
    evidence: str | None
    device_id: str | None
    created_at: datetime
    done_at: datetime | None
    tracked_minutes: float = 0.0
    last_tracked_at: datetime | None = None
    tracking: bool = False

    model_config = {"from_attributes": True}


class TodoCreateOut(TodoOut):
    # True when source='auto' matched an existing open todo (nothing created).
    deduped: bool = False


class TodosPage(BaseModel):
    todos: list[TodoOut]


class TodoEntryOut(BaseModel):
    id: uuid.UUID
    started_at: datetime
    ended_at: datetime
    minutes: float
    source: str
    confidence: float | None
    reason: str | None


class TodoEntriesPage(BaseModel):
    entries: list[TodoEntryOut]



class DailyStat(BaseModel):
    """One calendar day (in the requested tz) for GET /v1/stats/daily."""

    date: str
    events: int  # all kinds
    mine: int  # kind not in (ai_work, away)
    ai_work: int
    # distinct 5-min buckets of started_at x 5, less effective away time
    active_minutes: int | float
    ai_active_minutes: int  # same for ai_work
    # work_sessions (cancelled excluded) overlapping the day, clipped at
    # day boundaries; active sessions count up to now
    session_minutes: float
    away_minutes: float = 0.0
    first_at: datetime | None
    last_at: datetime | None


class DailyStatsResponse(BaseModel):
    tz: str
    days: list[DailyStat]


class ImporterOut(BaseModel):
    name: str
    enabled: bool = False
    cursor_ts: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    imported_count: int
    updated_at: datetime

    model_config = {"from_attributes": True}


class ImportersPage(BaseModel):
    importers: list[ImporterOut]


class ImporterRunOut(BaseModel):
    ok: bool
    fetched: int
    upserted: int
    pages: int
    cursor_ts: datetime | None


class ReportDailyIn(BaseModel):
    """POST /v1/reports/daily request body."""

    date: date
    tz: str | None = None  # default: TZ_DEFAULT
    force: bool = False


class ReportOut(BaseModel):
    report: EventOut
    # True when this call generated the report; False when an existing one
    # was returned as-is (idempotent re-POST without force).
    generated: bool = False


class BlogGenerateIn(BaseModel):
    """POST /v1/blog/generate request body."""

    date: date
    tz: str | None = None  # default: TZ_DEFAULT
    # Which types to generate; default = both ("summary" and "learning").
    types: list[str] | None = None
    force: bool = False


class BlogPostOut(BaseModel):
    """Full blog post (GET /v1/blog/posts/{slug}, generate responses)."""

    id: uuid.UUID
    slug: str
    date: date
    type: str
    title: str
    excerpt: str
    markdown: str
    tags: list[str]
    source_event_ids: list[uuid.UUID]
    model: str
    status: str
    author: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BlogPostBrief(BaseModel):
    """List item (GET /v1/blog/posts)."""

    slug: str
    date: date
    type: str
    title: str
    excerpt: str
    tags: list[str]
    status: str
    author: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class BlogPostsPage(BaseModel):
    posts: list[BlogPostBrief]
    next_cursor: str | None = None


class BlogPostRef(BaseModel):
    slug: str
    title: str


class BlogPostDetail(BlogPostOut):
    """Full post plus its neighbors (chronological: prev=older, next=newer)."""

    prev: BlogPostRef | None = None
    next: BlogPostRef | None = None


class BlogEntryOut(BaseModel):
    post: BlogPostOut
    generated: bool


class BlogGenerateOut(BaseModel):
    date: str
    tz: str
    summary: BlogEntryOut | None = None
    learning: list[BlogEntryOut] = []


class BlogTagOut(BaseModel):
    tag: str
    count: int


# --- Blog authoring (P5-B): external AIs and the user writing posts ---


class BlogPostCreateIn(BaseModel):
    """POST /v1/blog/posts request body."""

    title: str = Field(min_length=1, max_length=300)
    markdown: str = Field(min_length=1)
    date: date
    type: str = Field(default="post", pattern="^(summary|learning|post)$")
    tags: list[str] | None = None
    # "me" (the user), "auto" (built-in LLM) or "agent:<name>" (an external AI)
    author: str = Field(default="me", pattern="^(me|auto|agent:\\S+)$")
    status: str = Field(default="draft", pattern="^(draft|published)$")
    excerpt: str | None = None


class BlogPostPatchIn(BaseModel):
    """PATCH /v1/blog/posts/{slug} request body (only given fields change)."""

    title: str | None = Field(default=None, min_length=1, max_length=300)
    markdown: str | None = Field(default=None, min_length=1)
    tags: list[str] | None = None
    excerpt: str | None = None
    status: str | None = Field(default=None, pattern="^(draft|published|archived)$")


class BlogRewriteIn(BaseModel):
    """POST /v1/blog/posts/{slug}/rewrite request body."""

    instruction: str | None = None


class BlogRewriteOut(BaseModel):
    """Proposed rewrite (NOT saved; apply it with PATCH)."""

    title: str
    excerpt: str
    markdown: str


# --- OpenAI-compatible embeddings relay (P1-C): PC clients -> this server ---


class EmbeddingsIn(BaseModel):
    """OpenAI-style /v1/embeddings request body.

    input_type is accepted for client convenience ("query" | "document");
    the underlying provider uses no query/document prefix (same as the
    agent-memory gateway), so it does not change the result.
    """

    model: str = ""
    input: str | list[str] = Field(min_length=1)
    input_type: str = Field(default="document", pattern="^(query|document)$")


class EmbeddingItem(BaseModel):
    object: str = "embedding"
    index: int
    embedding: list[float]


class EmbeddingsUsage(BaseModel):
    prompt_tokens: int = 0
    total_tokens: int = 0


class EmbeddingsOut(BaseModel):
    object: str = "list"
    data: list[EmbeddingItem]
    model: str
    usage: EmbeddingsUsage = EmbeddingsUsage()
