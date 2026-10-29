"""work_sessions table + events.session_id (P2-B)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-12
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        # Device the session was declared on (no FK: may be an unknown device).
        sa.Column("device_id", sa.Text, nullable=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status", sa.Text, nullable=False, server_default=sa.text("'active'")
        ),
        sa.Column("end_reason", sa.Text, nullable=True),
        sa.Column("end_confidence", sa.Float, nullable=True),
        sa.Column(
            "end_evidence",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "confirmed", sa.Boolean, nullable=False, server_default=sa.text("false")
        ),
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
            "status IN ('active','ended','cancelled')",
            name="ck_work_sessions_status",
        ),
        sa.CheckConstraint(
            "end_reason IS NULL OR end_reason IN "
            "('manual','ai_detected','idle','superseded')",
            name="ck_work_sessions_end_reason",
        ),
    )
    op.create_index(
        "ix_work_sessions_status_started", "work_sessions", ["status", "started_at"]
    )
    op.add_column(
        "events", sa.Column("session_id", UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_events_session",
        "events",
        "work_sessions",
        ["session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_events_session", "events", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_events_session", table_name="events")
    op.drop_constraint("fk_events_session", "events", type_="foreignkey")
    op.drop_column("events", "session_id")
    op.drop_index("ix_work_sessions_status_started", table_name="work_sessions")
    op.drop_table("work_sessions")
