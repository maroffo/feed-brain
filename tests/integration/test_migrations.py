# ABOUTME: Postgres integration test: Alembic upgrade/downgrade on a scratch database.
# ABOUTME: Skipped unless the docker compose Postgres is reachable (docker compose up -d db).

import asyncio
import os
import uuid

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from feed_brain.config import get_settings
from feed_brain.db.migrate import alembic_config
from feed_brain.db.models import Base

pytestmark = pytest.mark.integration


def _admin_url() -> str:
    url = make_url(get_settings().database_url).set(drivername="postgresql+psycopg")
    return url.render_as_string(hide_password=False)


def _pg_available() -> bool:
    try:
        engine = create_engine(_admin_url())
        with engine.connect():
            pass
        engine.dispose()
        return True
    except Exception:
        return False


@pytest.fixture
def scratch_db(monkeypatch):
    """Create an empty database, point settings at it, drop it afterwards."""
    if not _pg_available():
        if os.environ.get("FEED_BRAIN_REQUIRE_PG") == "1":
            pytest.fail("Postgres required but not reachable")
        pytest.skip("Postgres not reachable: `docker compose up -d db` and DB_PASSWORD=feedbrain")

    name = f"feedbrain_test_{uuid.uuid4().hex[:8]}"
    admin = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    monkeypatch.setenv("DB_NAME", name)
    get_settings.cache_clear()
    sync_url = make_url(_admin_url()).set(database=name).render_as_string(hide_password=False)
    try:
        yield sync_url
    finally:
        monkeypatch.delenv("DB_NAME", raising=False)
        get_settings.cache_clear()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def test_upgrade_head_matches_models(scratch_db):
    """`alembic upgrade head` builds a schema with no drift from the ORM models."""
    command.upgrade(alembic_config(), "head")

    engine = create_engine(scratch_db)
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        assert {"feed_sources", "articles", "alembic_version"} <= tables
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []


def test_downgrade_base_drops_everything(scratch_db):
    """Downgrading to base leaves only the alembic_version table."""
    cfg = alembic_config()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    engine = create_engine(scratch_db)
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
    engine.dispose()
    assert tables <= {"alembic_version"}


@pytest.mark.usefixtures("scratch_db")
def test_async_engine_roundtrip_with_aware_datetimes():
    """asyncpg accepts timezone-aware datetimes on the migrated schema."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from feed_brain.db.models import Article, FeedSource
    from feed_brain.db.session import close_db, get_session_factory

    command.upgrade(alembic_config(), "head")

    async def _roundtrip() -> Article:
        try:
            async with get_session_factory()() as session:
                source = FeedSource(name="Krebs", url="https://krebs.example/feed", folder="Sec")
                session.add(source)
                await session.flush()
                session.add(
                    Article(
                        url="https://krebs.example/a",
                        title="A",
                        source_id=source.id,
                        published_date=datetime(2026, 11, 1, 8, 0, tzinfo=UTC),
                        section="security",
                    )
                )
                await session.commit()
            # Fresh session: values come from Postgres, not the identity map.
            async with get_session_factory()() as session:
                return (await session.execute(select(Article))).scalar_one()
        finally:
            await close_db()

    article = asyncio.run(_roundtrip())
    assert article.section == "security"
    assert article.published_date == datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
