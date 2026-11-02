# ABOUTME: SQLAlchemy ORM models for the feed-brain database.
# ABOUTME: FeedSource, Article, Bulletin (daily issue) and WeeklyDigest tables.

from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

RSS_FEED_TYPE = "rss"  # imported from the OPML, downloaded by the fetcher
EMAIL_FEED_TYPE = "email"  # created by the newsletter inbox; paid content


class Base(DeclarativeBase):
    pass


class FeedSource(Base):
    __tablename__ = "feed_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(2048), unique=True)
    feed_type: Mapped[str] = mapped_column(String(20), default=RSS_FEED_TYPE)
    folder: Mapped[str | None] = mapped_column(String(255))  # OPML folder, triage hint
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    articles: Mapped[list["Article"]] = relationship(back_populates="source")


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        Index("ix_articles_tier", "tier"),
        Index("ix_articles_status", "status"),
        Index("ix_articles_fetched_at", "fetched_at"),
        Index("ix_articles_section", "section"),
        Index("ix_articles_bulletin_id", "bulletin_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), unique=True)
    title: Mapped[str] = mapped_column(String(500))
    author: Mapped[str | None] = mapped_column(String(255))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("feed_sources.id"))
    content: Mapped[str | None] = mapped_column(Text)
    published_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Pipeline status
    status: Mapped[str] = mapped_column(String(20), default="new")

    # Triage
    summary: Mapped[str | None] = mapped_column(Text)
    tier: Mapped[str | None] = mapped_column(String(20))
    section: Mapped[str | None] = mapped_column(String(30))  # bulletin section
    category: Mapped[str | None] = mapped_column(String(50))  # vault topic routing
    reason: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)

    # Deep analysis (Anthropic)
    deep_summary: Mapped[str | None] = mapped_column(Text)
    deep_insights: Mapped[str | None] = mapped_column(Text)  # JSON array
    money_quote: Mapped[str | None] = mapped_column(Text)
    actionables: Mapped[str | None] = mapped_column(Text)  # JSON array

    # Bulletin that covered this article (stamped so it is never covered twice)
    bulletin_id: Mapped[int | None] = mapped_column(ForeignKey("bulletins.id", ondelete="SET NULL"))

    # Integration
    integrated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    integration_target: Mapped[str | None] = mapped_column(String(500))

    # Legacy feedback (kept for data compat)
    feedback: Mapped[str | None] = mapped_column(String(20))
    clipping_created: Mapped[bool] = mapped_column(Boolean, default=False)
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Timestamps
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source: Mapped[FeedSource | None] = relationship(back_populates="articles")


class Bulletin(Base):
    """One daily issue. content_json is a self-contained BulletinData for rendering."""

    __tablename__ = "bulletins"

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_date: Mapped[date] = mapped_column(Date, unique=True)
    title: Mapped[str] = mapped_column(String(300))
    subtitle: Mapped[str] = mapped_column(String(500))
    editorial: Mapped[str] = mapped_column(Text)
    content_json: Mapped[str] = mapped_column(Text)
    articles_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WeeklyDigest(Base):
    """Sunday recap of one ISO week (week_start is that Monday)."""

    __tablename__ = "weekly_digests"

    id: Mapped[int] = mapped_column(primary_key=True)
    week_start: Mapped[date] = mapped_column(Date, unique=True)
    title: Mapped[str] = mapped_column(String(300))
    subtitle: Mapped[str] = mapped_column(String(500))
    content_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
