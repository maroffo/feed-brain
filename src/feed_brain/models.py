# ABOUTME: Pydantic schemas for data validation and serialization.
# ABOUTME: Defines tiers, bulletin sections, vault categories, triage and deep-analysis outputs.

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


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


class Section(StrEnum):
    """Bulletin sections, derived from the OPML folders (plan Appendix A)."""

    SECURITY = "security"
    THREAT_RADAR = "threat_radar"
    AI = "ai"
    TECH = "tech"
    CLOUD_DEVOPS = "cloud_devops"
    SOFTWARE = "software"
    MANAGEMENT = "management"
    RESEARCH = "research"
    POLITICS = "politics"
    ECONOMY = "economy"
    CULTURE = "culture"
    SPORT = "sport"


SECTION_TITLES: dict[Section, str] = {
    Section.SECURITY: "Sicurezza",
    Section.THREAT_RADAR: "Radar minacce",
    Section.AI: "AI e agenti",
    Section.TECH: "Tecnologia",
    Section.CLOUD_DEVOPS: "Cloud e DevOps",
    Section.SOFTWARE: "Ingegneria del software",
    Section.MANAGEMENT: "Management e agile",
    Section.RESEARCH: "Ricerca",
    Section.POLITICS: "Politica, Europa e giustizia",
    Section.ECONOMY: "Economia",
    Section.CULTURE: "Cultura, idee e blog",
    Section.SPORT: "Sport",
}


class ArticleStatus(StrEnum):
    NEW = "new"
    TRIAGED = "triaged"
    ANALYZED = "analyzed"
    INTEGRATED = "integrated"
    ERROR = "error"


class TriageResult(BaseModel):
    """Flat output from Gemini triage."""

    tier: Tier
    section: Section
    category: Category
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    reason: str


class DeepAnalysis(BaseModel):
    """Output from Anthropic deep analysis."""

    summary: str
    insights: list[str]
    money_quote: str
    actionables: list[str]


class SourceRef(BaseModel):
    """One article backing a story, as cited in the bulletin."""

    name: str
    title: str
    url: str
    paid: bool = False  # newsletter inbox: the public page shows title and link only


class TopStory(BaseModel):
    title: str
    text: str
    section: Section
    sources: list[SourceRef]
    analysis: DeepAnalysis | None = None
    lead_url: str = ""  # article chosen as the story lead (vault entry candidate)


class BriefItem(BaseModel):
    headline: str
    summary: str
    sources: list[SourceRef]


class SectionBlock(BaseModel):
    key: Section
    title: str
    items: list[BriefItem]
    omitted: int = 0  # stories beyond the per-section cap (kept out of the email)


class RadarItem(BaseModel):
    title: str
    url: str
    source: str


class BulletinData(BaseModel):
    """Self-contained daily issue: everything render and weekly need, no DB joins."""

    issue_date: date
    title: str
    subtitle: str
    editorial: str
    top_stories: list[TopStory]
    sections: list[SectionBlock]
    radar: list[RadarItem]
    articles_count: int


class WrittenStory(BaseModel):
    index: int
    title: str
    text: str


class BulletinDraft(BaseModel):
    """Gemini writer output for the top of the bulletin."""

    title: str
    subtitle: str
    editorial: str
    top_stories: list[WrittenStory]


class WeeklyArc(BaseModel):
    """Writer output: one narrative arc, pointing at the week's numbered stories."""

    title: str
    text: str
    story_indices: list[int]


class WeeklyDraft(BaseModel):
    """Gemini writer output for the Sunday recap."""

    title: str
    subtitle: str
    intro: str
    arcs: list[WeeklyArc]
    reflection: str


class WeeklyArcData(BaseModel):
    title: str
    text: str
    links: list[SourceRef]


class BulletinRef(BaseModel):
    issue_date: date
    title: str


class WeeklyData(BaseModel):
    """Self-contained weekly recap for rendering."""

    week_start: date
    week_end: date
    title: str
    subtitle: str
    intro: str
    arcs: list[WeeklyArcData]
    reflection: str
    bulletins: list[BulletinRef]
    stories_count: int


class FeedSourceCreate(BaseModel):
    """Schema for creating a new feed source."""

    name: str
    url: str


# Maps categories to Second Brain topic filenames (without .md extension)
CATEGORY_VAULT_MAP: dict[Category, str] = {
    Category.AI_AGENTS: "Second Brain - AI Agents and Tools",
    Category.CLAUDE_CODE: "Second Brain - Claude Code",
    Category.DEVELOPMENT: "Second Brain - Development",
    Category.DEVOPS_CLOUD: "Second Brain - DevOps and Cloud",
    Category.ENGINEERING_MANAGEMENT: "Second Brain - Engineering Management",
    Category.POLITICS_ECONOMICS: "Second Brain - Politics and Economics",
    Category.MARKETING: "Second Brain - Marketing",
    Category.MEDIA_CULTURE: "Second Brain - Media and Culture",
    Category.HEALTH_SCIENCE: "Second Brain - Health and Science",
}
