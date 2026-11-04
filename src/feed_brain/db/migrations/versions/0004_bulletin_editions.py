# ABOUTME: Alembic migration: bulletins.edition, one issue per (issue_date, edition) instead of per day.
# ABOUTME: Revision 0004, revises 0003. Existing issues become the 'daily' edition.

"""bulletin editions

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-04 12:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bulletins",
        sa.Column("edition", sa.String(length=30), server_default="daily", nullable=False),
    )
    op.drop_constraint("bulletins_issue_date_key", "bulletins", type_="unique")
    op.create_unique_constraint(
        "uq_bulletins_issue_edition", "bulletins", ["issue_date", "edition"]
    )


def downgrade() -> None:
    # Thematic issues have no place in a one-issue-per-day table; their articles go back to
    # unstamped (articles.bulletin_id is ON DELETE SET NULL).
    op.execute("DELETE FROM bulletins WHERE edition <> 'daily'")
    op.drop_constraint("uq_bulletins_issue_edition", "bulletins", type_="unique")
    op.create_unique_constraint("bulletins_issue_date_key", "bulletins", ["issue_date"])
    op.drop_column("bulletins", "edition")
