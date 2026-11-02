# ABOUTME: Tests for CLI command bodies that touch the database.
# ABOUTME: Run against the in-memory SQLite session factory fixture.

from sqlalchemy import select

from feed_brain.__main__ import _run_reset
from feed_brain.db.models import Article


async def test_reset_clears_triage_fields_including_section(session_factory):
    """`reset` puts triaged articles back to new and clears tier, section and category."""
    async with session_factory() as session:
        session.add(
            Article(
                url="https://x.example/1",
                title="T",
                status="triaged",
                tier="high",
                section="security",
                category="devops_cloud",
                confidence=0.9,
            )
        )
        await session.commit()

    await _run_reset()

    async with session_factory() as session:
        article = (await session.execute(select(Article))).scalar_one()
    assert article.status == "new"
    assert (article.tier, article.section, article.category) == (None, None, None)
