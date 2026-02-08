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
