# ABOUTME: Tests for the shared HTTP GET helper and its proxy fallback for datacenter-blocked sites.
# ABOUTME: The low-level _get is faked to record which URL went through which proxy and User-Agent.

import httpx
import pytest
from pydantic import SecretStr

from feed_brain.config import Settings
from feed_brain.services import http

UA = "test-agent/1.0"


@pytest.fixture
def calls(monkeypatch):
    """Records (url, proxy, user_agent); `outcomes` maps proxy -> status code or exception."""
    log: list[tuple[str, str | None, str]] = []
    outcomes: dict[str | None, int | Exception] = {}

    async def fake_get(url, settings, proxy, user_agent):  # noqa: ARG001
        log.append((url, proxy, user_agent))
        outcome = outcomes.get(proxy, 200)
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome, request=httpx.Request("GET", url))

    monkeypatch.setattr(http, "_get", fake_get)
    return log, outcomes


PROXY = "socks5://user:pass@proxy.example:1080"


async def test_direct_success_never_touches_the_proxy(calls):
    log, _ = calls
    settings = Settings(_env_file=None, fetch_proxy=SecretStr(PROXY))
    response = await http.fetch("https://ok.example/rss", settings, user_agent=UA)
    assert response.status_code == 200
    assert log == [("https://ok.example/rss", None, UA)]


@pytest.mark.parametrize("status", [403, 419, 429])
async def test_blocked_status_retries_once_via_proxy(calls, status):
    log, outcomes = calls
    outcomes[None] = status
    settings = Settings(_env_file=None, fetch_proxy=SecretStr(PROXY))
    response = await http.fetch("https://blocked.example/rss", settings, user_agent=UA)
    assert response.status_code == 200
    assert log == [
        ("https://blocked.example/rss", None, UA),
        ("https://blocked.example/rss", PROXY, UA),
    ]


async def test_blocked_without_proxy_raises(calls):
    _, outcomes = calls
    outcomes[None] = 403
    with pytest.raises(httpx.HTTPStatusError):
        await http.fetch("https://blocked.example/rss", Settings(_env_file=None), user_agent=UA)


async def test_other_errors_are_not_retried(calls):
    log, outcomes = calls
    outcomes[None] = 404
    settings = Settings(_env_file=None, fetch_proxy=SecretStr(PROXY))
    with pytest.raises(httpx.HTTPStatusError):
        await http.fetch("https://gone.example/rss", settings, user_agent=UA)
    assert len(log) == 1


async def test_proxy_also_blocked_raises_after_two_calls(calls):
    log, outcomes = calls
    outcomes.update({None: 403, PROXY: 403})
    settings = Settings(_env_file=None, fetch_proxy=SecretStr(PROXY))
    with pytest.raises(httpx.HTTPStatusError):
        await http.fetch("https://blocked.example/rss", settings, user_agent=UA)
    assert len(log) == 2


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
    ],
)
async def test_transport_error_retries_once_via_proxy(calls, error):
    log, outcomes = calls
    outcomes[None] = error
    settings = Settings(_env_file=None, fetch_proxy=SecretStr(PROXY))
    response = await http.fetch("https://flaky.example/rss", settings, user_agent=UA)
    assert response.status_code == 200
    assert log == [
        ("https://flaky.example/rss", None, UA),
        ("https://flaky.example/rss", PROXY, UA),
    ]


async def test_transport_error_without_proxy_raises(calls):
    log, outcomes = calls
    outcomes[None] = httpx.ConnectError("refused")
    with pytest.raises(httpx.ConnectError):
        await http.fetch("https://flaky.example/rss", Settings(_env_file=None), user_agent=UA)
    assert len(log) == 1


async def test_transport_error_through_proxy_too_raises(calls):
    log, outcomes = calls
    outcomes.update({None: httpx.ReadTimeout("slow"), PROXY: httpx.ReadTimeout("slow")})
    settings = Settings(_env_file=None, fetch_proxy=SecretStr(PROXY))
    with pytest.raises(httpx.ReadTimeout):
        await http.fetch("https://flaky.example/rss", settings, user_agent=UA)
    assert len(log) == 2


def test_proxy_url_strips_and_rejects_bad_values(capsys):
    ok = Settings(_env_file=None, fetch_proxy=SecretStr(f"{PROXY}\n"))
    assert http.proxy_url(ok) == PROXY
    bad = Settings(_env_file=None, fetch_proxy=SecretStr("user:secretpass@host:1080"))
    assert http.proxy_url(bad) is None
    assert "secretpass" not in capsys.readouterr().out
    assert http.proxy_url(Settings(_env_file=None)) is None


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
    await http._get("https://ok.example/rss", Settings(_env_file=None), None, UA)
    assert seen == [UA]


async def test_socks_proxy_client_can_be_built():
    """httpx[socks] is installed: a SOCKS5 proxy URL with credentials is accepted."""
    async with httpx.AsyncClient(proxy=PROXY) as client:
        assert client is not None
