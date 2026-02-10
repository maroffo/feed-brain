# ABOUTME: FastAPI route handlers for the feed-brain web UI.
# ABOUTME: Serves feed list, article detail, feedback, fetch trigger, and feed management.

import json

import structlog
from fastapi import APIRouter, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from feed_brain.db.models import Article, FeedSource
from feed_brain.db.session import get_session_factory
from feed_brain.models import ArticleView, Feedback

log = structlog.get_logger()
router = APIRouter()


def _article_to_view(article: Article) -> ArticleView:
    """Convert ORM article to view model."""
    return ArticleView(
        id=article.id,
        url=article.url,
        title=article.title,
        author=article.author,
        source_name=article.source.name if article.source else None,
        content=article.content,
        published_date=article.published_date,
        summary=article.summary,
        tier=article.tier,
        category=article.category,
        reason=article.reason,
        confidence=article.confidence,
        money_quote=article.money_quote,
        actionables=json.loads(article.actionables) if article.actionables else [],
        feedback=article.feedback,
        clipping_created=article.clipping_created,
        fetched_at=article.fetched_at,
        classified_at=article.classified_at,
    )


@router.get("/", response_class=HTMLResponse)
async def feed_list(
    request: Request,
    tier: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
):
    """Feed list page with article cards, optionally filtered by tier."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        query = (
            select(Article).options(joinedload(Article.source)).order_by(Article.fetched_at.desc())
        )
        if tier:
            query = query.where(Article.tier == tier)
        query = query.offset((page - 1) * per_page).limit(per_page)
        result = await session.execute(query)
        articles = [_article_to_view(a) for a in result.scalars().all()]

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "feed.html",
        {"request": request, "articles": articles, "tier_filter": tier},
    )


@router.get("/article/{article_id}", response_class=HTMLResponse)
async def article_detail(request: Request, article_id: int, tier: str | None = Query(None)):
    """Article detail page with full content and next/prev navigation."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(Article).options(joinedload(Article.source)).where(Article.id == article_id)
        )
        article = result.scalar_one_or_none()
        if not article:
            raise HTTPException(status_code=404, detail="Article not found")
        view = _article_to_view(article)

        # Find prev/next articles in the same tier context
        base_query = select(Article.id).order_by(Article.fetched_at.desc())
        if tier:
            base_query = base_query.where(Article.tier == tier)

        ids_result = await session.execute(base_query)
        article_ids = [row[0] for row in ids_result.all()]

        prev_id = None
        next_id = None
        if article_id in article_ids:
            idx = article_ids.index(article_id)
            if idx > 0:
                prev_id = article_ids[idx - 1]
            if idx < len(article_ids) - 1:
                next_id = article_ids[idx + 1]

    tier_param = f"?tier={tier}" if tier else ""
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "article.html",
        {
            "request": request,
            "article": view,
            "prev_id": prev_id,
            "next_id": next_id,
            "tier_filter": tier,
            "tier_param": tier_param,
        },
    )


@router.post("/article/{article_id}/feedback", response_class=HTMLResponse)
async def article_feedback(request: Request, article_id: int, feedback: str = Query(...)):  # noqa: ARG001
    """Record feedback (approved/skipped) and optionally create clipping."""
    feedback_enum = Feedback(feedback)
    session_factory = get_session_factory()

    async with session_factory() as session:
        result = await session.execute(select(Article).where(Article.id == article_id))
        article = result.scalar_one_or_none()
        if not article:
            raise HTTPException(status_code=404, detail="Article not found")

        article.feedback = feedback_enum.value

        if feedback_enum == Feedback.APPROVED:
            from datetime import UTC, datetime

            from feed_brain.services.clipping import create_clipping

            created = await create_clipping(article)
            article.clipping_created = created
            article.feedback_at = datetime.now(UTC)
        else:
            from datetime import UTC, datetime

            article.feedback_at = datetime.now(UTC)

        await session.commit()

        # Return partial HTML for htmx swap
        if feedback_enum == Feedback.APPROVED and article.clipping_created:
            return HTMLResponse('<span class="feedback-done">Clipping created</span>')
        elif feedback_enum == Feedback.APPROVED:
            return HTMLResponse('<span class="feedback-done">Approved (clipping failed)</span>')
        else:
            return HTMLResponse('<span class="feedback-done">Skipped</span>')


@router.post("/fetch")
async def trigger_fetch():
    """Trigger feed fetch + classification."""
    from feed_brain.services.classifier import classify_unclassified
    from feed_brain.services.fetcher import fetch_all_feeds

    new_articles = await fetch_all_feeds()
    classified = await classify_unclassified()
    log.info("fetch_triggered", new_articles=new_articles, classified=classified)
    return {"new_articles": new_articles, "classified": classified}


@router.get("/feeds", response_class=HTMLResponse)
async def feeds_page(request: Request):
    """Feed source management page."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(FeedSource).where(FeedSource.active.is_(True)).order_by(FeedSource.name)
        )
        sources = result.scalars().all()

    templates = request.app.state.templates
    return templates.TemplateResponse("feeds.html", {"request": request, "sources": sources})


@router.post("/feeds", response_class=HTMLResponse)
async def add_feed(request: Request, name: str = Form(...), url: str = Form(...)):
    """Add a new RSS feed source."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        # Check for duplicate URL among active feeds
        existing = await session.execute(
            select(FeedSource).where(FeedSource.url == url, FeedSource.active.is_(True))
        )
        if existing.scalar_one_or_none() is not None:
            return HTMLResponse("<p>Feed URL already exists.</p>", status_code=409)

        source = FeedSource(name=name, url=url, feed_type="rss")
        session.add(source)
        await session.commit()

    return await _render_feed_list(request)


@router.delete("/feeds/{feed_id}", response_class=HTMLResponse)
async def delete_feed(feed_id: int):
    """Remove a feed source (hard delete, nullifies article references)."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(select(FeedSource).where(FeedSource.id == feed_id))
        source = result.scalar_one_or_none()
        if not source:
            raise HTTPException(status_code=404, detail="Feed not found")

        # Nullify article references before deleting
        await session.execute(
            Article.__table__.update().where(Article.source_id == feed_id).values(source_id=None)
        )
        await session.delete(source)
        await session.commit()

    return HTMLResponse("")


@router.put("/feeds/{feed_id}", response_class=HTMLResponse)
async def edit_feed(request: Request, feed_id: int, name: str = Form(...), url: str = Form(...)):
    """Update an existing feed source."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(select(FeedSource).where(FeedSource.id == feed_id))
        source = result.scalar_one_or_none()
        if not source:
            raise HTTPException(status_code=404, detail="Feed not found")

        # Check URL uniqueness (excluding this feed)
        if url != source.url:
            dup = await session.execute(
                select(FeedSource.id).where(
                    FeedSource.url == url, FeedSource.id != feed_id, FeedSource.active.is_(True)
                )
            )
            if dup.scalar_one_or_none() is not None:
                return HTMLResponse("<p>Feed URL already exists.</p>", status_code=409)

        source.name = name
        source.url = url
        await session.commit()

    return await _render_feed_list(request)


@router.post("/feeds/import", response_class=HTMLResponse)
async def import_opml(request: Request, opml_file: UploadFile):
    """Import feeds from an OPML file."""
    from feed_brain.services.opml import parse_opml

    content = await opml_file.read()
    feeds = parse_opml(content.decode("utf-8"))

    session_factory = get_session_factory()
    imported = 0
    async with session_factory() as session:
        for feed in feeds:
            existing = await session.execute(
                select(FeedSource.id).where(FeedSource.url == feed["url"])
            )
            if existing.scalar_one_or_none() is not None:
                continue

            source = FeedSource(name=feed["name"], url=feed["url"], feed_type="rss")
            session.add(source)
            imported += 1

        await session.commit()

    log.info("opml_imported", imported=imported, total=len(feeds))
    return await _render_feed_list(request)


async def _render_feed_list(request: Request) -> HTMLResponse:
    """Re-render the feed list partial for htmx swaps."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(FeedSource).where(FeedSource.active.is_(True)).order_by(FeedSource.name)
        )
        sources = result.scalars().all()

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "partials/feed_list_table.html", {"request": request, "sources": sources}
    )
