"""initial schema: devices, events, event_embeddings

Revision ID: 0001
Revises:
Create Date: 2026-10-12
"""
import os

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The embedding dimension is fixed at migration time from EMBED_DIM.
    dim = int(os.environ.get("EMBED_DIM", "1024"))

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "devices",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False, server_default=""),
        sa.Column("os", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "device_id", sa.Text, sa.ForeignKey("devices.id"), nullable=False
        ),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("app", sa.Text, nullable=True),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column(
            "meta", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("source", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "kind IN ('activity','screen_summary','note','todo','ai_work','report')",
            name="ck_events_kind",
        ),
    )
    op.create_index("ix_events_started_at", "events", ["started_at"])
    op.create_index("ix_events_device_started", "events", ["device_id", "started_at"])
    op.create_index("ix_events_kind", "events", ["kind"])
    op.create_index(
        "ix_events_text_trgm",
        "events",
        [sa.text("text gin_trgm_ops")],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_events_meta_gin", "events", ["meta"], postgresql_using="gin"
    )

    op.create_table(
        "event_embeddings",
        sa.Column(
            "event_id",
            UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("embedding", Vector(dim), nullable=False),
        sa.Column(
            "embedded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("event_embeddings")
    op.drop_index("ix_events_meta_gin", table_name="events")
    op.drop_index("ix_events_text_trgm", table_name="events")
    op.drop_index("ix_events_kind", table_name="events")
    op.drop_index("ix_events_device_started", table_name="events")
    op.drop_index("ix_events_started_at", table_name="events")
    op.drop_table("events")
    op.drop_table("devices")
