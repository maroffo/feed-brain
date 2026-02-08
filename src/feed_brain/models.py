# ABOUTME: Pydantic schemas for data validation and serialization.
# ABOUTME: Defines classification output, article data, and feed source schemas.

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class Tier(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Category(StrEnum):
    AI_AGENTS = "ai_agents"
    CLAUDE_CODE = "claude_code"
    DEVELOPMENT = "development"
    DEVOPS_CLOUD = "devops_cloud"
    ENGINEERING_MANAGEMENT = "engineering_management"
    POLITICS_ECONOMICS = "politics_economics"
    MARKETING = "marketing"
    MEDIA_CULTURE = "media_culture"
    HEALTH_SCIENCE = "health_science"


class Feedback(StrEnum):
    APPROVED = "approved"
    SKIPPED = "skipped"


class ClassificationResult(BaseModel):
    """Output from the Haiku classifier."""

    tier: Tier
    category: Category
    summary: str
    reason: str
    confidence: float


class FeedSourceCreate(BaseModel):
    """Schema for creating a new feed source."""

    name: str
    url: str


class ArticleView(BaseModel):
    """Article data for template rendering."""

    id: int
    url: str
    title: str
    author: str | None
    source_name: str | None
    content: str | None
    published_date: datetime | None
    summary: str | None
    tier: Tier | None
    category: Category | None
    reason: str | None
    confidence: float | None
    feedback: Feedback | None
    clipping_created: bool
    fetched_at: datetime
    classified_at: datetime | None
