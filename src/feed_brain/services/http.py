# ABOUTME: Shared HTTP GET for feeds and pages; the kind picks User-Agent and timeouts.
# ABOUTME: Blocked (403/419/429) or network-failed requests get one retry through FETCH_PROXY.

from typing import Literal
from urllib.parse import urlsplit

import httpx
import structlog

from feed_brain.config import Settings

log = structlog.get_logger()

type FetchKind = Literal["feed", "page"]

BLOCKED_STATUSES = frozenset({403, 419, 429})
# Refused, reset, timed out or disconnected: some sites drop cloud IPs instead of answering 403.
# Excludes UnsupportedProtocol and ProxyError, which a proxy retry cannot fix.
RETRYABLE_ERRORS = (httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError)
# Hosts that drop cloud IPs never answer the SYN: fail fast instead of waiting a full read timeout.
CONNECT_TIMEOUT = 10.0
PROXY_SCHEMES = frozenset({"socks5", "socks5h", "http", "https"})


def proxy_url(settings: Settings) -> str | None:
    """FETCH_PROXY if usable; a malformed value is reported without echoing its credentials."""
    if settings.fetch_proxy is None:
        return None
    proxy = settings.fetch_proxy.get_secret_value().strip()
    parts = urlsplit(proxy)
    try:
        valid = parts.scheme in PROXY_SCHEMES and bool(parts.hostname) and parts.port != 0
    except ValueError:  # non-numeric or out-of-range port, which httpx rejects with InvalidURL
        valid = False
    if not valid:
        log.error("fetch_proxy_invalid", hint="expected socks5://user:pass@host:port")
        return None
    return proxy


def _profile(settings: Settings, kind: FetchKind) -> tuple[str, httpx.Timeout]:
    """User-Agent and timeouts for a kind of download."""
    if kind == "feed":
        return settings.feed_user_agent, httpx.Timeout(
            settings.feed_timeout, connect=CONNECT_TIMEOUT
        )
    return settings.page_user_agent, httpx.Timeout(settings.page_timeout, connect=CONNECT_TIMEOUT)


async def _get(
    url: str, proxy: str | None, user_agent: str, timeout: httpx.Timeout
) -> httpx.Response:
    async with httpx.AsyncClient(
        timeout=timeout,
        headers={"User-Agent": user_agent},
        follow_redirects=True,
        proxy=proxy,
    ) as client:
        return await client.get(url)


async def fetch(url: str, settings: Settings, *, kind: FetchKind) -> httpx.Response:
    """GET `url`; raises httpx.HTTPError on failure (after the proxy retry, if any)."""
    user_agent, timeout = _profile(settings, kind)
    try:
        response = await _get(url, None, user_agent, timeout)
    except RETRYABLE_ERRORS as e:
        proxy = proxy_url(settings)
        if proxy is None:
            raise
        log.info("fetch_via_proxy", url=url, reason=type(e).__name__)
        response = await _get(url, proxy, user_agent, timeout)
    else:
        if response.status_code in BLOCKED_STATUSES and (proxy := proxy_url(settings)):
            log.info("fetch_via_proxy", url=url, status=response.status_code)
            response = await _get(url, proxy, user_agent, timeout)
    response.raise_for_status()
    return response
