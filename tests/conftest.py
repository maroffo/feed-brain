# ABOUTME: Shared test fixtures for feed-brain.
# ABOUTME: Provides async DB session, test client, and sample data factories.

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from feed_brain.db.models import Article, Base, FeedSource


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    """In-memory SQLite async session for tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def sample_feed_source() -> FeedSource:
    """Sample feed source for tests."""
    return FeedSource(name="Test Feed", url="https://example.com/feed.xml", feed_type="rss")


@pytest.fixture
def sample_article() -> Article:
    """Sample article for tests."""
    return Article(
        url="https://example.com/article-1",
        title="Test Article",
        author="Test Author",
        content="This is test article content about AI agents and development.",
    )
