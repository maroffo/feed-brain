# ABOUTME: FastAPI application factory with Jinja2 templates and database lifespan.
# ABOUTME: Main entry point for the feed-brain web frontend.

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from feed_brain.db.session import close_db, init_db
from feed_brain.models import Category, Tier

logger = structlog.get_logger()

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

# Display labels for enums
TIER_LABELS = {Tier.HIGH: "High", Tier.MEDIUM: "Medium", Tier.LOW: "Low"}
CATEGORY_LABELS = {
    Category.AI_AGENTS: "AI & Agents",
    Category.CLAUDE_CODE: "Claude Code",
    Category.DEVELOPMENT: "Development",
    Category.DEVOPS_CLOUD: "DevOps & Cloud",
    Category.ENGINEERING_MANAGEMENT: "Engineering",
    Category.POLITICS_ECONOMICS: "Politics & Economics",
    Category.MARKETING: "Marketing",
    Category.MEDIA_CULTURE: "Media & Culture",
    Category.HEALTH_SCIENCE: "Health & Science",
}


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Application lifespan context for database setup/teardown."""
    logger.info("app_startup")
    await init_db()
    yield
    logger.info("app_shutdown")
    await close_db()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="feed-brain",
        description="AI-powered personal feed aggregator",
        version="0.1.0",
        lifespan=lifespan,
    )

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["tier_label"] = lambda v: TIER_LABELS.get(v, v or "Unclassified")
    templates.env.filters["category_label"] = lambda v: CATEGORY_LABELS.get(v, v or "Unknown")
    app.state.templates = templates

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    from feed_brain.web.routes import router

    app.include_router(router)

    return app


app = create_app()
