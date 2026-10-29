"""importer_state table (P3-A: agent-memory import bookkeeping)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-12
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "importer_state",
        sa.Column("name", sa.Text, primary_key=True),
        # Max created_at fetched so far (inclusive); NULL = never ran.
        sa.Column("cursor_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column(
            "imported_count", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("importer_state")
