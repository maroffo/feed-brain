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
