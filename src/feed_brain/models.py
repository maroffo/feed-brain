# ABOUTME: Pydantic schemas for data validation and serialization.
# ABOUTME: Defines triage output, deep analysis, category mapping, and article status.

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


class ArticleStatus(StrEnum):
    NEW = "new"
    TRIAGED = "triaged"
    ANALYZED = "analyzed"
    INTEGRATED = "integrated"
    ERROR = "error"


class TriageResult(BaseModel):
    """Flat output from Ollama triage."""

    tier: Tier
    category: Category
    confidence: float
    summary: str
    reason: str


class DeepAnalysis(BaseModel):
    """Output from Anthropic deep analysis."""

    summary: str
    insights: list[str]
    money_quote: str
    actionables: list[str]


class FeedSourceCreate(BaseModel):
    """Schema for creating a new feed source."""

    name: str
    url: str


# Maps categories to vault subfolder paths
CATEGORY_VAULT_MAP: dict[Category, str] = {
    Category.AI_AGENTS: "Resources/AI Agents",
    Category.CLAUDE_CODE: "Resources/Claude Code",
    Category.DEVELOPMENT: "Resources/Development",
    Category.DEVOPS_CLOUD: "Resources/DevOps",
    Category.ENGINEERING_MANAGEMENT: "Resources/Engineering Management",
    Category.POLITICS_ECONOMICS: "Resources/Politics & Economics",
    Category.MARKETING: "Resources/Marketing",
    Category.MEDIA_CULTURE: "Resources/Media & Culture",
    Category.HEALTH_SCIENCE: "Resources/Health & Science",
}
