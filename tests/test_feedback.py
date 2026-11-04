# ABOUTME: Tests for one-click story votes: signed links, story keys, story lookup in a stored issue.
# ABOUTME: Pure functions only; the HTTP service is covered in test_web.py.

from datetime import date

from feed_brain.models import BriefItem, BulletinData, Section, SectionBlock, SourceRef, TopStory
from feed_brain.services.feedback import (
    ESSAY,
    VOTES,
    StoryLinks,
    find_story,
    find_top_story,
    link_secret,
    signature,
    story_key,
    valid_signature,
)

SECRET = b"k" * 32
DAY = date(2026, 11, 3)

ISSUE = BulletinData(
    issue_date=DAY,
    title="t",
    top_stories=[
        TopStory(
            title="Frigoriferi sotto attacco",
            text="x",
            section=Section.SECURITY,
            sources=[
                SourceRef(name="Guardian", title="Fridges", url="https://g/fridges"),
                SourceRef(name="Politico", title="Cold", url="https://p/cold"),
            ],
        )
    ],
    sections=[
        SectionBlock(
            key=Section.POLITICS,
            title="Politica",
            items=[
                BriefItem(
                    headline="Midterm USA",
                    summary="s",
                    sources=[SourceRef(name="Il Post", title="Midterm", url="https://ilpost/m")],
                )
            ],
        )
    ],
    articles_count=3,
)


def test_story_key_is_short_stable_and_depends_on_the_url_only():
    key = story_key("https://g/fridges")
    assert key == story_key("https://g/fridges") and len(key) == 10
    assert key != story_key("https://g/fridges2")


def test_a_signature_checks_out_only_for_the_exact_date_story_and_vote():
    sig = signature(SECRET, DAY, "abc", "up")
    assert valid_signature(SECRET, DAY, "abc", "up", sig)
    assert not valid_signature(SECRET, DAY, "abc", "down", sig)
    assert not valid_signature(SECRET, DAY, "abd", "up", sig)
    assert not valid_signature(SECRET, date(2026, 11, 4), "abc", "up", sig)
    assert not valid_signature(b"x" * 32, DAY, "abc", "up", sig)
    assert not valid_signature(SECRET, DAY, "abc", "up", sig[:-1])
    assert not valid_signature(SECRET, DAY, "abc", "up", "")
    assert not valid_signature(SECRET, DAY, "abc", "up", "è" * 22)  # non-ASCII: refused, no crash


def test_signatures_are_url_safe_and_128_bits():
    sig = signature(SECRET, DAY, "abc", "down")
    assert len(sig) == 22 and all(c.isalnum() or c in "-_" for c in sig)


def test_vote_links_point_at_the_service_with_a_valid_signature():
    links = StoryLinks("https://fb.run.app/", SECRET, DAY).votes("https://g/fridges")
    key = story_key("https://g/fridges")
    for vote in ("up", "down"):
        prefix = f"https://fb.run.app/v/2026-11-03/{key}/{vote}?s="
        assert links[vote].startswith(prefix)
        assert valid_signature(SECRET, DAY, key, vote, links[vote].removeprefix(prefix))


def test_find_story_resolves_top_stories_and_briefs_by_their_first_source():
    top = find_story(ISSUE, story_key("https://g/fridges"))
    assert top is not None and top.headline == "Frigoriferi sotto attacco"
    assert top.section == "security" and [s.name for s in top.sources] == ["Guardian", "Politico"]
    brief = find_story(ISSUE, story_key("https://ilpost/m"))
    assert brief is not None and brief.headline == "Midterm USA" and brief.section == "politics"
    # a second source does not identify the story, an unknown key finds nothing
    assert find_story(ISSUE, story_key("https://p/cold")) is None
    assert find_story(ISSUE, "0000000000") is None


def test_link_secret_needs_a_real_key():
    from pydantic import SecretStr

    from feed_brain.config import Settings

    def secret(value):
        return link_secret(Settings(_env_file=None, feedback_key=value))

    assert secret(None) is None
    assert secret(SecretStr("")) is None and secret(SecretStr(" " * 40)) is None
    assert secret(SecretStr("short")) is None  # under 32 chars
    assert secret(SecretStr(" " + "k" * 32 + "\n")) == b"k" * 32


def test_the_essay_link_has_its_own_signature_that_never_casts_a_vote():
    links = StoryLinks("https://fb.run.app", SECRET, DAY)
    key = story_key("https://g/fridges")
    prefix = f"https://fb.run.app/e/2026-11-03/{key}?s="
    essay = links.essay("https://g/fridges")
    assert essay.startswith(prefix)
    sig = essay.removeprefix(prefix)
    assert valid_signature(SECRET, DAY, key, ESSAY, sig)
    assert ESSAY not in VOTES
    assert not any(valid_signature(SECRET, DAY, key, vote, sig) for vote in VOTES)


def test_find_top_story_ignores_briefs():
    assert (
        find_top_story(ISSUE, story_key("https://g/fridges")).title == "Frigoriferi sotto attacco"
    )
    assert find_top_story(ISSUE, story_key("https://ilpost/m")) is None
