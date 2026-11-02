# ABOUTME: Shared HTTP GET for feeds and article pages; the caller picks the User-Agent.
# ABOUTME: Blocked (403/419/429) or network-failed requests get one retry through FETCH_PROXY.

from urllib.parse import urlsplit

import httpx
import structlog

from feed_brain.config import Settings

log = structlog.get_logger()

BLOCKED_STATUSES = frozenset({403, 419, 429})
# Refused, reset, timed out or disconnected: some sites drop cloud IPs instead of answering 403.
# Excludes UnsupportedProtocol and ProxyError, which a proxy retry cannot fix.
RETRYABLE_ERRORS = (httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError)
PROXY_SCHEMES = frozenset({"socks5", "socks5h", "http", "https"})


def proxy_url(settings: Settings) -> str | None:
    """FETCH_PROXY if usable; a malformed value is reported without echoing its credentials."""
    if settings.fetch_proxy is None:
        return None
    proxy = settings.fetch_proxy.get_secret_value().strip()
    if urlsplit(proxy).scheme not in PROXY_SCHEMES:
        log.error("fetch_proxy_invalid", hint="expected socks5://user:pass@host:port")
        return None
    return proxy


async def _get(url: str, settings: Settings, proxy: str | None, user_agent: str) -> httpx.Response:
    async with httpx.AsyncClient(
        timeout=settings.feed_timeout,
        headers={"User-Agent": user_agent},
        follow_redirects=True,
        proxy=proxy,
    ) as client:
        return await client.get(url)


async def fetch(url: str, settings: Settings, *, user_agent: str) -> httpx.Response:
    """GET `url`; raises httpx.HTTPError on failure (after the proxy retry, if any)."""
    try:
        response = await _get(url, settings, None, user_agent)
    except RETRYABLE_ERRORS as e:
        proxy = proxy_url(settings)
        if proxy is None:
            raise
        log.info("fetch_via_proxy", url=url, reason=type(e).__name__)
        response = await _get(url, settings, proxy, user_agent)
    else:
        if response.status_code in BLOCKED_STATUSES and (proxy := proxy_url(settings)):
            log.info("fetch_via_proxy", url=url, status=response.status_code)
            response = await _get(url, settings, proxy, user_agent)
    response.raise_for_status()
    return response
