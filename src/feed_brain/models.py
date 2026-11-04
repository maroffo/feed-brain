# ABOUTME: Pydantic schemas for data validation and serialization.
# ABOUTME: Defines tiers, bulletin sections and editions, vault categories, triage and deep-analysis outputs.

from dataclasses import dataclass
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
    """Bulletin sections, derived from the OPML folders (plan Appendix A).

    The main issue carries DAILY_AREAS; every other section has its thematic edition
    (EDITIONS). Each section belongs to exactly one edition.
    """

    SECURITY = "security"
    THREAT_RADAR = "threat_radar"
    AI = "ai"
    AI_DEV = "ai_dev"
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
    Section.AI_DEV: "AI per sviluppatori",
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

# Daily issue reading order: areas first to last, sections in order inside each area.
# Top stories are grouped by area; 'In breve' follows DAILY_SECTIONS.
DAILY_AREAS: tuple[tuple[Section, ...], ...] = (
    (Section.POLITICS, Section.ECONOMY),
    (Section.CULTURE, Section.SPORT),
    (Section.SECURITY, Section.AI, Section.TECH),
)
DAILY_SECTIONS: tuple[Section, ...] = tuple(section for area in DAILY_AREAS for section in area)


@dataclass(frozen=True)
class Edition:
    """One email of the morning: the main issue or a thematic one, built by the same pipeline."""

    key: str
    title: str  # kicker, subject and page title
    areas: tuple[tuple[Section, ...], ...]  # reading order: top stories by area, briefs by section
    top_stories: int | None = None  # None: settings.top_stories
    essay_min_sources: int = 1  # single-source technical stories get the notes, not an essay

    @property
    def sections(self) -> tuple[Section, ...]:
        return tuple(section for area in self.areas for section in area)

    @property
    def is_daily(self) -> bool:
        return self.key == DAILY_EDITION

    def stem(self, day: date) -> str:
        """Page and file name: the date for the main issue, date-key for a thematic one."""
        return day.isoformat() if self.is_daily else f"{day.isoformat()}-{self.key}"

    def note_name(self, day: date) -> str:
        """Vault note name in Digests/."""
        base = f"feed-brain {day.isoformat()}"
        return base if self.is_daily else f"{base} {self.title}"


DAILY_EDITION = "daily"
EDITIONS: dict[str, Edition] = {
    edition.key: edition
    for edition in (
        Edition(DAILY_EDITION, "Bollettino", DAILY_AREAS),
        Edition(
            "engineering",
            "Ingegneria",
            ((Section.CLOUD_DEVOPS, Section.SOFTWARE, Section.MANAGEMENT),),
            top_stories=3,
            essay_min_sources=2,
        ),
        Edition(
            "ai_dev",
            "AI per sviluppatori",
            ((Section.AI_DEV,),),
            top_stories=3,
            essay_min_sources=2,
        ),
        Edition("research", "Ricerca", ((Section.RESEARCH,),), top_stories=3, essay_min_sources=2),
        Edition(
            "threat_radar",
            "Radar minacce",
            ((Section.THREAT_RADAR,),),
            top_stories=3,
            essay_min_sources=2,
        ),
    )
}


def edition_for(key: str) -> Edition:
    """The edition of a stored issue; an unknown key (edition since removed) reads as daily."""
    return EDITIONS.get(key, EDITIONS[DAILY_EDITION])


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


class StoryEssay(BaseModel):
    """Opus essay behind a top story, served privately by the vote service."""

    paragraphs: list[str]
    sources_read: int  # sources that went in with their full text (the rest: summary only)


class TopStory(BaseModel):
    title: str
    text: str
    section: Section
    sources: list[SourceRef]
    analysis: DeepAnalysis | None = None
    lead_url: str = ""  # article chosen as the story lead (vault entry candidate)
    essay: StoryEssay | None = None


class BriefItem(BaseModel):
    headline: str
    summary: str
    sources: list[SourceRef]


class SectionBlock(BaseModel):
    key: Section
    title: str
    items: list[BriefItem]
    omitted: int = 0  # stories beyond the per-section cap (kept out of the email)


class BulletinData(BaseModel):
    """Self-contained issue of one edition: everything render and weekly need, no DB joins."""

    issue_date: date
    edition: str = DAILY_EDITION  # issues stored before editions are all daily
    title: str
    key_points: list[str] = Field(default_factory=list)  # 'Il punto': one line per fact
    top_stories: list[TopStory]
    sections: list[SectionBlock]
    articles_count: int
    # The first issues opened with a subtitle and a prose 'Il punto' instead of key points.
    subtitle: str = ""
    editorial: str = ""


class WrittenStory(BaseModel):
    index: int
    title: str
    text: str


class BulletinDraft(BaseModel):
    """Gemini writer output for the top of the bulletin."""

    title: str
    key_points: list[str]
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
