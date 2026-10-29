"""todos + todo_time_entries (P6): todo list with auto time tracking

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "todos",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column(
            "status", sa.Text, nullable=False, server_default=sa.text("'open'")
        ),
        sa.Column(
            "source", sa.Text, nullable=False, server_default=sa.text("'me'")
        ),
        sa.Column(
            "urgency", sa.Text, nullable=False, server_default=sa.text("'medium'")
        ),
        sa.Column("due_date", sa.Date, nullable=True),
        sa.Column("evidence", sa.Text, nullable=True),
        # Device the todo was added on (no FK: may be an unknown device).
        sa.Column("device_id", sa.Text, nullable=True),
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
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('open','done','archived')", name="ck_todos_status"
        ),
        sa.CheckConstraint("source IN ('me','auto')", name="ck_todos_source"),
        sa.CheckConstraint(
            "urgency IN ('high','medium','low')", name="ck_todos_urgency"
        ),
    )
    op.create_index("ix_todos_status", "todos", ["status"])

    op.create_table(
        "todo_time_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "todo_id",
            UUID(as_uuid=True),
            sa.ForeignKey("todos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "source", sa.Text, nullable=False, server_default=sa.text("'auto'")
        ),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column(
            "event_ids",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "source IN ('auto','me')", name="ck_todo_time_entries_source"
        ),
    )
    op.create_index(
        "ix_todo_time_entries_todo_started", "todo_time_entries", ["todo_id", "started_at"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_todo_time_entries_todo_started", table_name="todo_time_entries"
    )
    op.drop_table("todo_time_entries")
    op.drop_index("ix_todos_status", table_name="todos")
    op.drop_table("todos")
