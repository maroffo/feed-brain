# ABOUTME: Centralized configuration using Pydantic Settings.
# ABOUTME: Loads all settings from environment variables and .env file.

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Anthropic (deep analysis)
    anthropic_api_key: SecretStr | None = None
    analyzer_model: str = "claude-sonnet-4-6"

    # Ollama (triage)
    ollama_model: str = "llama3.2:3b"
    ollama_host: str = "http://localhost:11434"

    # Database
    db_path: Path = Path("./feed_brain.db")

    # Feed fetching
    feed_timeout: int = 15
    feed_user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Safari/605.1.15"
    )
    max_articles_per_feed: int = 15
    article_max_age_hours: int = 48
    fetch_concurrency: int = 10

    # Second Brain (Obsidian vault)
    vault_path: Path = Path(
        "/Users/maroffo/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain"
    )

    # Integration thresholds
    auto_threshold: float = 0.8
    interactive_threshold: float = 0.6

    # Logging
    log_level: str = "INFO"

    @property
    def database_url(self) -> str:
        """Build async SQLite connection URL."""
        return f"sqlite+aiosqlite:///{self.db_path}"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
