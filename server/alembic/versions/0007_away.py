"""Accept away events and work-session end reasons.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-13
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_events_kind", "events", type_="check")
    op.create_check_constraint(
        "ck_events_kind", "events",
        "kind IN ('activity','screen_summary','note','todo','ai_work','report','away')",
    )
    op.drop_constraint("ck_work_sessions_end_reason", "work_sessions", type_="check")
    op.create_check_constraint(
        "ck_work_sessions_end_reason", "work_sessions",
        "end_reason IS NULL OR end_reason IN "
        "('manual','ai_detected','idle','superseded','away')",
    )


def downgrade() -> None:
    # Existing away rows are preserved. PostgreSQL enforces NOT VALID checks
    # on new writes while allowing the older application schema to be restored.
    op.drop_constraint("ck_events_kind", "events", type_="check")
    op.execute("ALTER TABLE events ADD CONSTRAINT ck_events_kind CHECK "
               "(kind IN ('activity','screen_summary','note','todo','ai_work','report')) NOT VALID")
    op.drop_constraint("ck_work_sessions_end_reason", "work_sessions", type_="check")
    op.execute("ALTER TABLE work_sessions ADD CONSTRAINT ck_work_sessions_end_reason CHECK "
               "(end_reason IS NULL OR end_reason IN "
               "('manual','ai_detected','idle','superseded')) NOT VALID")
