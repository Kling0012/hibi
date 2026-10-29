"""blog_posts authoring columns (P5-B): status + author, type 'post'

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # status: new column, default draft; existing articles stay public.
    op.add_column(
        "blog_posts",
        sa.Column(
            "status", sa.Text(), nullable=False, server_default=sa.text("'draft'")
        ),
    )
    op.execute("UPDATE blog_posts SET status = 'published'")
    op.create_check_constraint(
        "ck_blog_posts_status",
        "blog_posts",
        "status IN ('draft','published','archived')",
    )
    # author: "auto" (built-in LLM) / "me" (the user) / "agent:<name>"
    op.add_column(
        "blog_posts",
        sa.Column(
            "author", sa.Text(), nullable=False, server_default=sa.text("'auto'")
        ),
    )
    op.create_check_constraint(
        "ck_blog_posts_author",
        "blog_posts",
        "author IN ('me','auto') OR author LIKE 'agent:%'",
    )
    # widen the type CHECK with the free-form 'post' kind
    op.drop_constraint("ck_blog_posts_type", "blog_posts", type_="check")
    op.create_check_constraint(
        "ck_blog_posts_type",
        "blog_posts",
        "type IN ('summary','learning','post')",
    )
    op.create_index("ix_blog_posts_status", "blog_posts", ["status"])


def downgrade() -> None:
    op.drop_index("ix_blog_posts_status", table_name="blog_posts")
    op.drop_constraint("ck_blog_posts_type", "blog_posts", type_="check")
    op.create_check_constraint(
        "ck_blog_posts_type", "blog_posts", "type IN ('summary','learning')"
    )
    op.drop_constraint("ck_blog_posts_author", "blog_posts", type_="check")
    op.drop_column("blog_posts", "author")
    op.drop_constraint("ck_blog_posts_status", "blog_posts", type_="check")
    op.drop_column("blog_posts", "status")
