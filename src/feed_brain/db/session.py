# ABOUTME: Database engine, async session factory, and migration.
# ABOUTME: Manages SQLAlchemy async engine lifecycle, table creation, and schema migration.

import structlog
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from feed_brain.config import get_settings
from feed_brain.db.models import Base

log = structlog.get_logger()

_engine = None
_session_factory = None


def _set_sqlite_pragmas(dbapi_conn, _connection_record):
    """Enable WAL mode and busy timeout for concurrent access."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def get_engine():
    """Get or create the async engine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database_url, echo=False)
        event.listen(_engine.sync_engine, "connect", _set_sqlite_pragmas)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or create the session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def init_db() -> None:
    """Create all tables if they don't exist."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("database_initialized", url=get_settings().database_url)


async def migrate_db() -> None:
    """Run idempotent ALTER TABLE migrations for new columns.

    Safe to run multiple times; skips columns that already exist.
    Each ALTER TABLE runs in its own transaction to avoid invalidation.
    """
    engine = get_engine()
    migrations = [
        ("articles", "status", "TEXT DEFAULT 'new'"),
        ("articles", "deep_summary", "TEXT"),
        ("articles", "deep_insights", "TEXT"),
        ("articles", "integrated_at", "DATETIME"),
        ("articles", "integration_target", "TEXT"),
    ]

    for table, column, col_type in migrations:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                log.info("migration_added_column", table=table, column=column)
        except Exception:
            # Column already exists
            pass

    # Backfill status for existing articles
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE articles SET status = 'triaged' "
                "WHERE status IS NULL AND classified_at IS NOT NULL"
            )
        )
        await conn.execute(text("UPDATE articles SET status = 'new' WHERE status IS NULL"))

    log.info("migration_complete")


async def close_db() -> None:
    """Dispose of the engine."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        log.info("database_closed")
