# ABOUTME: Tests for the shared HTTP GET helper and its proxy fallback for datacenter-blocked sites.
# ABOUTME: The low-level _get is faked to record which URL went through which proxy, UA and timeout.

import httpx
import pytest
from pydantic import SecretStr

from feed_brain.config import Settings
from feed_brain.services import http

FEED_UA = "feed-agent/1.0"
PAGE_UA = "page-agent/1.0"
PROXY = "socks5://user:pass@proxy.example:1080"


def make_settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        feed_user_agent=FEED_UA,
        page_user_agent=PAGE_UA,
        feed_timeout=30,
        page_timeout=15,
        **overrides,
    )


@pytest.fixture
def calls(monkeypatch):
    """Records (url, proxy, user_agent) and timeouts; `outcomes` maps proxy -> status or exception."""
    log: list[tuple[str, str | None, str]] = []
    timeouts: list[httpx.Timeout] = []
    outcomes: dict[str | None, int | Exception] = {}

    async def fake_get(url, proxy, user_agent, timeout):
        log.append((url, proxy, user_agent))
        timeouts.append(timeout)
        outcome = outcomes.get(proxy, 200)
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome, request=httpx.Request("GET", url))

    monkeypatch.setattr(http, "_get", fake_get)
    return log, outcomes, timeouts


async def test_direct_success_never_touches_the_proxy(calls):
    log, _, _ = calls
    settings = make_settings(fetch_proxy=SecretStr(PROXY))
    response = await http.fetch("https://ok.example/rss", settings, kind="feed")
    assert response.status_code == 200
    assert log == [("https://ok.example/rss", None, FEED_UA)]


async def test_kind_selects_user_agent_and_timeouts(calls):
    """Feeds: bot UA, 30 s reads. Pages: browser UA, 15 s reads. Both connect within 10 s."""
    log, _, timeouts = calls
    settings = make_settings()
    await http.fetch("https://ok.example/rss", settings, kind="feed")
    await http.fetch("https://ok.example/article", settings, kind="page")
    assert [ua for _, _, ua in log] == [FEED_UA, PAGE_UA]
    assert [(t.read, t.connect) for t in timeouts] == [(30, 10), (15, 10)]


@pytest.mark.parametrize("status", [403, 419, 429])
async def test_blocked_status_retries_once_via_proxy(calls, status):
    log, outcomes, _ = calls
    outcomes[None] = status
    settings = make_settings(fetch_proxy=SecretStr(PROXY))
    response = await http.fetch("https://blocked.example/rss", settings, kind="feed")
    assert response.status_code == 200
    assert log == [
        ("https://blocked.example/rss", None, FEED_UA),
        ("https://blocked.example/rss", PROXY, FEED_UA),
    ]


async def test_blocked_without_proxy_raises(calls):
    _, outcomes, _ = calls
    outcomes[None] = 403
    with pytest.raises(httpx.HTTPStatusError):
        await http.fetch("https://blocked.example/rss", make_settings(), kind="feed")


async def test_other_errors_are_not_retried(calls):
    log, outcomes, _ = calls
    outcomes[None] = 404
    settings = make_settings(fetch_proxy=SecretStr(PROXY))
    with pytest.raises(httpx.HTTPStatusError):
        await http.fetch("https://gone.example/rss", settings, kind="feed")
    assert len(log) == 1


async def test_proxy_also_blocked_raises_after_two_calls(calls):
    log, outcomes, _ = calls
    outcomes.update({None: 403, PROXY: 403})
    settings = make_settings(fetch_proxy=SecretStr(PROXY))
    with pytest.raises(httpx.HTTPStatusError):
        await http.fetch("https://blocked.example/rss", settings, kind="feed")
    assert len(log) == 2


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("refused"),
        httpx.ConnectTimeout("no SYN-ACK"),
        httpx.ReadTimeout("slow"),
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
    ],
)
async def test_transport_error_retries_once_via_proxy(calls, error):
    log, outcomes, _ = calls
    outcomes[None] = error
    settings = make_settings(fetch_proxy=SecretStr(PROXY))
    response = await http.fetch("https://flaky.example/page", settings, kind="page")
    assert response.status_code == 200
    assert log == [
        ("https://flaky.example/page", None, PAGE_UA),
        ("https://flaky.example/page", PROXY, PAGE_UA),
    ]


async def test_transport_error_without_proxy_raises(calls):
    log, outcomes, _ = calls
    outcomes[None] = httpx.ConnectError("refused")
    with pytest.raises(httpx.ConnectError):
        await http.fetch("https://flaky.example/rss", make_settings(), kind="feed")
    assert len(log) == 1


async def test_transport_error_through_proxy_too_raises(calls):
    log, outcomes, _ = calls
    outcomes.update({None: httpx.ReadTimeout("slow"), PROXY: httpx.ReadTimeout("slow")})
    settings = make_settings(fetch_proxy=SecretStr(PROXY))
    with pytest.raises(httpx.ReadTimeout):
        await http.fetch("https://flaky.example/rss", settings, kind="feed")
    assert len(log) == 2


async def test_unfixable_transport_errors_are_not_retried(calls):
    """A bad scheme fails the same way through a proxy, so it is raised at once."""
    log, outcomes, _ = calls
    outcomes[None] = httpx.UnsupportedProtocol("feed:// is not http")
    settings = make_settings(fetch_proxy=SecretStr(PROXY))
    with pytest.raises(httpx.UnsupportedProtocol):
        await http.fetch("feed://example/rss", settings, kind="feed")
    assert len(log) == 1


def test_proxy_url_strips_and_rejects_bad_values(capsys):
    assert http.proxy_url(make_settings(fetch_proxy=SecretStr(f"{PROXY}\n"))) == PROXY
    for bad in (
        "user:secretpass@host:1080",  # no scheme
        "socks5://user:secretpass@:1080",  # no host
        "socks5://user:secretpass@host:abc",  # port httpx would reject with InvalidURL
    ):
        assert http.proxy_url(make_settings(fetch_proxy=SecretStr(bad))) is None, bad
    assert "secretpass" not in capsys.readouterr().out
    assert http.proxy_url(make_settings()) is None


async def test_get_sends_the_given_user_agent(monkeypatch):
    """The real _get puts the caller's User-Agent on the request, not a global default."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["User-Agent"])
        return httpx.Response(200)

    real_client = httpx.AsyncClient

    def client_with_mock_transport(**kwargs):
        kwargs.pop("proxy", None)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(http.httpx, "AsyncClient", client_with_mock_transport)
    await http._get("https://ok.example/rss", None, FEED_UA, httpx.Timeout(5))
    assert seen == [FEED_UA]


async def test_socks_proxy_client_can_be_built():
    """httpx[socks] is installed: a SOCKS5 proxy URL with credentials is accepted."""
    async with httpx.AsyncClient(proxy=PROXY) as client:
        assert client is not None
