# ABOUTME: Alembic migration: bulletins and weekly digests.
# ABOUTME: Revision 0002, revises 0001.

"""bulletins and weekly digests

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-02 15:19:10.155701+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_NAME = "fk_articles_bulletin_id_bulletins"


def upgrade() -> None:
    op.create_table(
        "bulletins",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("subtitle", sa.String(length=500), nullable=False),
        sa.Column("editorial", sa.Text(), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("articles_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issue_date"),
    )
    op.create_table(
        "weekly_digests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("subtitle", sa.String(length=500), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("week_start"),
    )
    op.add_column("articles", sa.Column("bulletin_id", sa.Integer(), nullable=True))
    op.create_index("ix_articles_bulletin_id", "articles", ["bulletin_id"], unique=False)
    op.create_foreign_key(
        FK_NAME, "articles", "bulletins", ["bulletin_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint(FK_NAME, "articles", type_="foreignkey")
    op.drop_index("ix_articles_bulletin_id", table_name="articles")
    op.drop_column("articles", "bulletin_id")
    op.drop_table("weekly_digests")
    op.drop_table("bulletins")
