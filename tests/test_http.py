# ABOUTME: Tests for the shared HTTP GET helper and its proxy fallback for datacenter-blocked sites.
# ABOUTME: The low-level _get is faked to record which URL went through which proxy.

import httpx
import pytest
from pydantic import SecretStr

from feed_brain.config import Settings
from feed_brain.services import http


@pytest.fixture
def calls(monkeypatch):
    log: list[tuple[str, str | None]] = []
    statuses: dict[str | None, int] = {}

    async def fake_get(url, settings, proxy):  # noqa: ARG001
        log.append((url, proxy))
        return httpx.Response(statuses.get(proxy, 200), request=httpx.Request("GET", url))

    monkeypatch.setattr(http, "_get", fake_get)
    return log, statuses


PROXY = "socks5://user:pass@proxy.example:1080"


async def test_direct_success_never_touches_the_proxy(calls):
    log, _ = calls
    settings = Settings(_env_file=None, fetch_proxy=SecretStr(PROXY))
    response = await http.fetch("https://ok.example/rss", settings)
    assert response.status_code == 200
    assert log == [("https://ok.example/rss", None)]


@pytest.mark.parametrize("status", [403, 419, 429])
async def test_blocked_status_retries_once_via_proxy(calls, status):
    log, statuses = calls
    statuses[None] = status
    settings = Settings(_env_file=None, fetch_proxy=SecretStr(PROXY))
    response = await http.fetch("https://blocked.example/rss", settings)
    assert response.status_code == 200
    assert log == [("https://blocked.example/rss", None), ("https://blocked.example/rss", PROXY)]


async def test_blocked_without_proxy_raises(calls):
    _, statuses = calls
    statuses[None] = 403
    with pytest.raises(httpx.HTTPStatusError):
        await http.fetch("https://blocked.example/rss", Settings(_env_file=None))


async def test_other_errors_are_not_retried(calls):
    log, statuses = calls
    statuses[None] = 404
    settings = Settings(_env_file=None, fetch_proxy=SecretStr(PROXY))
    with pytest.raises(httpx.HTTPStatusError):
        await http.fetch("https://gone.example/rss", settings)
    assert len(log) == 1


async def test_proxy_also_blocked_raises_after_two_calls(calls):
    log, statuses = calls
    statuses.update({None: 403, PROXY: 403})
    settings = Settings(_env_file=None, fetch_proxy=SecretStr(PROXY))
    with pytest.raises(httpx.HTTPStatusError):
        await http.fetch("https://blocked.example/rss", settings)
    assert len(log) == 2


def test_proxy_url_strips_and_rejects_bad_values(capsys):
    ok = Settings(_env_file=None, fetch_proxy=SecretStr(f"{PROXY}\n"))
    assert http.proxy_url(ok) == PROXY
    bad = Settings(_env_file=None, fetch_proxy=SecretStr("user:secretpass@host:1080"))
    assert http.proxy_url(bad) is None
    assert "secretpass" not in capsys.readouterr().out
    assert http.proxy_url(Settings(_env_file=None)) is None


async def test_socks_proxy_client_can_be_built():
    """httpx[socks] is installed: a SOCKS5 proxy URL with credentials is accepted."""
    async with httpx.AsyncClient(proxy=PROXY) as client:
        assert client is not None
