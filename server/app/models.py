"""SQLAlchemy engine/session and table models."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from pgvector.sqlalchemy import Vector

from . import config


class Base(DeclarativeBase):
    pass


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    os: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


EVENT_KINDS = (
    "activity",
    "screen_summary",
    "note",
    "todo",
    "ai_work",
    "report",
    "away",
)

# Kinds that events:batch auto-links to the work session active at their time.
AUTO_LINK_KINDS = ("activity", "screen_summary")

SESSION_STATUSES = ("active", "ended", "cancelled")
SESSION_END_REASONS = ("manual", "ai_detected", "idle", "superseded", "away")


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('activity','screen_summary','note','todo','ai_work','report','away')",
            name="ck_events_kind",
        ),
        Index("ix_events_started_at", "started_at"),
        Index("ix_events_device_started", "device_id", "started_at"),
        Index("ix_events_kind", "kind"),
        Index("ix_events_session", "session_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    device_id: Mapped[str] = mapped_column(
        Text, ForeignKey("devices.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    app: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    source: Mapped[str | None] = mapped_column(Text)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class WorkSession(Base):
    """A declared work session ("これから○○をする") whose end is judged later."""

    __tablename__ = "work_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','ended','cancelled')",
            name="ck_work_sessions_status",
        ),
        CheckConstraint(
            "end_reason IS NULL OR end_reason IN "
            "('manual','ai_detected','idle','superseded','away')",
            name="ck_work_sessions_end_reason",
        ),
        Index("ix_work_sessions_status_started", "status", "started_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Device the session was declared on (plain text; may not be a known device).
    device_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    end_reason: Mapped[str | None] = mapped_column(Text)
    end_confidence: Mapped[float | None] = mapped_column(Float)
    end_evidence: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=sa_text("'[]'::jsonb")
    )
    confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


TODO_STATUSES = ("open", "done", "archived")
TODO_SOURCES = ("me", "auto")
TODO_URGENCIES = ("high", "medium", "low")


class Todo(Base):
    """A todo: added by the user ('me') or found by an AI from screen
    records ('auto'). Same list, same shape either way."""

    __tablename__ = "todos"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open','done','archived')", name="ck_todos_status"
        ),
        CheckConstraint("source IN ('me','auto')", name="ck_todos_source"),
        CheckConstraint(
            "urgency IN ('high','medium','low')", name="ck_todos_urgency"
        ),
        Index("ix_todos_status", "status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="open", server_default=sa_text("'open'")
    )
    source: Mapped[str] = mapped_column(
        Text, nullable=False, default="me", server_default=sa_text("'me'")
    )
    urgency: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="medium",
        server_default=sa_text("'medium'"),
    )
    due_date: Mapped[date | None] = mapped_column(Date)
    # AI-found evidence, e.g. "14:02 画面タイトル"
    evidence: Mapped[str | None] = mapped_column(Text)
    # Device the todo was added on (plain text; may not be a known device).
    device_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TodoTimeEntry(Base):
    """Time spent on a todo. source='auto' entries come from the judge
    matching screen records; 'me' is a manual addition."""

    __tablename__ = "todo_time_entries"
    __table_args__ = (
        CheckConstraint(
            "source IN ('auto','me')", name="ck_todo_time_entries_source"
        ),
        Index("ix_todo_time_entries_todo_started", "todo_id", "started_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    todo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("todos.id", ondelete="CASCADE"),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(
        Text, nullable=False, default="auto", server_default=sa_text("'auto'")
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(Text)
    # Event ids the entry was matched against (strings, chronological).
    event_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=sa_text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EventEmbedding(Base):
    __tablename__ = "event_embeddings"
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="CASCADE"),
        primary_key=True,
    )
    model: Mapped[str] = mapped_column(Text, nullable=False)
    # Dimension is fixed by EMBED_DIM at migration time.
    embedding = mapped_column(Vector(config.EMBED_DIM), nullable=False)
    embedded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ImporterState(Base):
    """Cursor/bookkeeping for a background importer (agent-memory, P3-A)."""

    __tablename__ = "importer_state"
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    # Max created_at fetched so far (inclusive); NULL = never ran.
    cursor_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    imported_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa_text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


BLOG_POST_TYPES = ("summary", "learning", "post")
# Publication lifecycle: drafts are only visible with an explicit status
# filter; the public list/reader serves published posts; DELETE archives
# (no physical delete).
BLOG_POST_STATUSES = ("draft", "published", "archived")


class BlogPost(Base):
    """A blog article. id = uuid5(date+type+topic) for generated posts,
    uuid4 for API/MCP-created ones."""

    __tablename__ = "blog_posts"
    __table_args__ = (
        CheckConstraint(
            "type IN ('summary','learning','post')", name="ck_blog_posts_type"
        ),
        CheckConstraint(
            "status IN ('draft','published','archived')",
            name="ck_blog_posts_status",
        ),
        CheckConstraint(
            "author IN ('me','auto') OR author LIKE 'agent:%'",
            name="ck_blog_posts_author",
        ),
        Index("ix_blog_posts_date", "date"),
        Index("ix_blog_posts_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list] = mapped_column(
        ARRAY(Text), nullable=False, server_default=sa_text("'{}'::text[]")
    )
    source_event_ids: Mapped[list] = mapped_column(
        ARRAY(UUID(as_uuid=True)),
        nullable=False,
        server_default=sa_text("'{}'::uuid[]"),
    )
    model: Mapped[str] = mapped_column(Text, nullable=False)
    # draft (existing rows default to published via the migration) /
    # published / archived
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="draft", server_default=sa_text("'draft'")
    )
    # "auto" = built-in LLM, "me" = the user, "agent:<name>" = an external AI
    author: Mapped[str] = mapped_column(
        Text, nullable=False, default="auto", server_default=sa_text("'auto'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
