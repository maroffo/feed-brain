# ABOUTME: Alembic migration: story_votes (one-click 👍/👎 on bulletin stories).
# ABOUTME: Revision 0003, revises 0002.

"""story votes

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-03 14:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "story_votes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("story_key", sa.String(length=16), nullable=False),
        sa.Column("vote", sa.String(length=8), nullable=False),
        sa.Column("headline", sa.String(length=500), nullable=False),
        sa.Column("section", sa.String(length=50), nullable=False),
        sa.Column("sources_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issue_date", "story_key", name="uq_story_votes_story"),
    )


def downgrade() -> None:
    op.drop_table("story_votes")
