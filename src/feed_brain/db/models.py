# ABOUTME: SQLAlchemy ORM models for articles and feed sources.
# ABOUTME: Defines FeedSource and Article tables with indexes and relationships.

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class FeedSource(Base):
    __tablename__ = "feed_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(2048), unique=True)
    feed_type: Mapped[str] = mapped_column(String(20), default="rss")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    articles: Mapped[list["Article"]] = relationship(back_populates="source")


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        Index("ix_articles_tier", "tier"),
        Index("ix_articles_feedback", "feedback"),
        Index("ix_articles_fetched_at", "fetched_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), unique=True)
    title: Mapped[str] = mapped_column(String(500))
    author: Mapped[str | None] = mapped_column(String(255))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("feed_sources.id"))
    content: Mapped[str | None] = mapped_column(Text)
    published_date: Mapped[datetime | None] = mapped_column(DateTime)

    # AI classification
    summary: Mapped[str | None] = mapped_column(Text)
    tier: Mapped[str | None] = mapped_column(String(20))
    category: Mapped[str | None] = mapped_column(String(50))
    reason: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)

    # Feedback
    feedback: Mapped[str | None] = mapped_column(String(20))
    clipping_created: Mapped[bool] = mapped_column(Boolean, default=False)
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Timestamps
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    classified_at: Mapped[datetime | None] = mapped_column(DateTime)

    source: Mapped[FeedSource | None] = relationship(back_populates="articles")
