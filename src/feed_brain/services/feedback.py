# ABOUTME: One-click story votes: HMAC-signed links for the email and story lookup in a stored issue.
# ABOUTME: A story is keyed by its first source URL, stable when `bulletin --force` rewrites headlines.

import base64
import hashlib
import hmac
from dataclasses import dataclass
from datetime import date

from feed_brain.config import Settings
from feed_brain.models import BulletinData, SourceRef

VOTES = ("up", "down")
SIGNATURE_BYTES = 16  # 128 bits
MIN_KEY_CHARS = 32


def link_secret(settings: Settings) -> bytes | None:
    """The link-signing key, or None when unset, blank or shorter than MIN_KEY_CHARS."""
    if settings.feedback_key is None:
        return None
    key = settings.feedback_key.get_secret_value().strip()
    return key.encode("utf-8") if len(key) >= MIN_KEY_CHARS else None


def story_key(first_source_url: str) -> str:
    """10 hex chars of sha256: unique per story because article URLs are unique in the DB."""
    return hashlib.sha256(first_source_url.encode("utf-8")).hexdigest()[:10]


def signature(secret: bytes, issue_date: date, key: str, vote: str) -> str:
    """URL-safe HMAC-SHA256 of one vote on one story of one issue."""
    message = f"v1|{issue_date.isoformat()}|{key}|{vote}".encode()
    digest = hmac.new(secret, message, hashlib.sha256).digest()[:SIGNATURE_BYTES]
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def valid_signature(secret: bytes, issue_date: date, key: str, vote: str, sig: str) -> bool:
    """Constant-time check; bytes, because compare_digest rejects non-ASCII str."""
    expected = signature(secret, issue_date, key, vote).encode("ascii")
    return hmac.compare_digest(expected, sig.encode("utf-8"))


class VoteLinks:
    """Callable for the email template: first source URL -> {"up": url, "down": url}."""

    def __init__(self, base_url: str, secret: bytes, issue_date: date) -> None:
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.issue_date = issue_date

    def __call__(self, first_source_url: str) -> dict[str, str]:
        key = story_key(first_source_url)
        day = self.issue_date.isoformat()
        return {
            vote: f"{self.base_url}/v/{day}/{key}/{vote}"
            f"?s={signature(self.secret, self.issue_date, key, vote)}"
            for vote in VOTES
        }


@dataclass(frozen=True)
class StorySnapshot:
    """What a vote records about the story, so later readers need no join."""

    headline: str
    section: str
    sources: list[SourceRef]


def find_story(data: BulletinData, key: str) -> StorySnapshot | None:
    """The top story or 'In breve' item whose first source has this key."""
    for story in data.top_stories:
        if story.sources and story_key(story.sources[0].url) == key:
            return StorySnapshot(story.title, story.section.value, story.sources)
    for block in data.sections:
        for item in block.items:
            if item.sources and story_key(item.sources[0].url) == key:
                return StorySnapshot(item.headline, block.key.value, item.sources)
    return None
