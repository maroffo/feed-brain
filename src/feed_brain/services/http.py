# ABOUTME: Shared HTTP GET for feeds and article pages, with a browser User-Agent.
# ABOUTME: Sites that block cloud IPs (403/419/429) get one retry through FETCH_PROXY (e.g. NordVPN SOCKS5).

from urllib.parse import urlsplit

import httpx
import structlog

from feed_brain.config import Settings

log = structlog.get_logger()

BLOCKED_STATUSES = frozenset({403, 419, 429})
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


async def _get(url: str, settings: Settings, proxy: str | None) -> httpx.Response:
    async with httpx.AsyncClient(
        timeout=settings.feed_timeout,
        headers={"User-Agent": settings.feed_user_agent},
        follow_redirects=True,
        proxy=proxy,
    ) as client:
        return await client.get(url)


async def fetch(url: str, settings: Settings) -> httpx.Response:
    """GET `url`; raises httpx.HTTPError on failure (after the proxy retry, if any)."""
    response = await _get(url, settings, None)
    if response.status_code in BLOCKED_STATUSES:
        proxy = proxy_url(settings)
        if proxy:
            log.info("fetch_via_proxy", url=url, status=response.status_code)
            response = await _get(url, settings, proxy)
    response.raise_for_status()
    return response
