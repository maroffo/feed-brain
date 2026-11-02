# ABOUTME: Tests for Settings-derived values.
# ABOUTME: Verifies the Postgres URLs built from DB_* settings.

from pydantic import SecretStr

from feed_brain.config import Settings


def test_database_url_encodes_password():
    """Special characters in the password are URL-encoded in the asyncpg URL."""
    settings = Settings(
        db_host="10.0.0.5",
        db_port=5432,
        db_name="feedbrain",
        db_user="feedbrain",
        db_password=SecretStr("p@ss/w:rd"),
        _env_file=None,
    )
    assert settings.database_url == (
        "postgresql+asyncpg://feedbrain:p%40ss%2Fw%3Ard@10.0.0.5:5432/feedbrain"
    )


def test_database_url_without_password():
    """A missing password yields an empty password segment, not 'None'."""
    settings = Settings(db_password=None, _env_file=None)
    assert ":@" in settings.database_url
    assert "None" not in settings.database_url


def test_fetch_defaults_split_feed_and_page_user_agents():
    """Feeds get an honest bot UA (Akamai rejects a browser UA on a non-browser TLS stack);
    article pages keep a browser UA and the shorter page timeout; feeds get 30 s for slow
    multi-MB feeds."""
    settings = Settings(_env_file=None)
    assert settings.feed_user_agent.startswith("feed-brain/")
    assert "Mozilla" not in settings.feed_user_agent
    assert settings.page_user_agent.startswith("Mozilla/5.0")
    assert settings.feed_timeout == 30
    assert settings.page_timeout == 15
