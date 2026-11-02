# ABOUTME: Programmatic Alembic entry point used by the `migrate` CLI command and the job.
# ABOUTME: Builds the Alembic config from the package path so it works from any cwd or install.

from pathlib import Path

import structlog
from alembic import command
from alembic.config import Config

log = structlog.get_logger()

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config() -> Config:
    """Alembic config pointing at the packaged migrations; URL comes from Settings in env.py."""
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return cfg


def upgrade_head() -> None:
    """Apply all pending migrations. Synchronous: call it outside a running event loop."""
    command.upgrade(alembic_config(), "head")
    log.info("migrations_applied", target="head")
