# ABOUTME: Tests for database models and session management.
# ABOUTME: Verifies table creation, article/feed CRUD, and relationships.

from sqlalchemy import select

from feed_brain.db.models import Article, FeedSource


async def test_create_feed_source(db_session, sample_feed_source):
    """Feed source can be created and retrieved."""
    db_session.add(sample_feed_source)
    await db_session.commit()

    result = await db_session.execute(select(FeedSource))
    feed = result.scalar_one()
    assert feed.name == "Test Feed"
    assert feed.url == "https://example.com/feed.xml"
    assert feed.active is True


async def test_create_article_with_source(db_session, sample_feed_source, sample_article):
    """Article linked to a feed source persists correctly."""
    db_session.add(sample_feed_source)
    await db_session.flush()

    sample_article.source_id = sample_feed_source.id
    db_session.add(sample_article)
    await db_session.commit()

    result = await db_session.execute(select(Article).where(Article.url == sample_article.url))
    article = result.scalar_one()
    assert article.title == "Test Article"
    assert article.source_id == sample_feed_source.id
    assert article.clipping_created is False
    assert article.feedback is None


async def test_unique_url_constraint(db_session):
    """Duplicate article URLs are rejected."""
    import pytest
    from sqlalchemy.exc import IntegrityError

    a1 = Article(url="https://example.com/dup", title="First")
    a2 = Article(url="https://example.com/dup", title="Second")
    db_session.add(a1)
    await db_session.flush()
    db_session.add(a2)

    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_folder_and_section_persist(db_session):
    """FeedSource.folder and Article.section round-trip through the ORM."""
    source = FeedSource(name="Krebs", url="https://krebs.example/feed", folder="0. Security")
    db_session.add(source)
    await db_session.flush()
    source_id = source.id
    db_session.add(
        Article(url="https://krebs.example/a", title="A", source_id=source_id, section="security")
    )
    await db_session.commit()
    db_session.expire_all()

    article = (await db_session.execute(select(Article))).scalar_one()
    assert article.section == "security"
    assert (await db_session.get(FeedSource, source_id)).folder == "0. Security"


async def test_one_issue_per_edition_per_day(db_session):
    """Editions share a date; the same edition twice on one day is rejected."""
    from datetime import date

    import pytest
    from sqlalchemy.exc import IntegrityError

    from feed_brain.db.models import Bulletin

    def issue(edition: str) -> Bulletin:
        fields = {"title": "t", "subtitle": "", "editorial": "", "content_json": "{}"}
        return Bulletin(issue_date=date(2026, 11, 5), edition=edition, **fields)

    db_session.add_all([issue("daily"), issue("engineering")])
    await db_session.commit()
    db_session.add(issue("engineering"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
