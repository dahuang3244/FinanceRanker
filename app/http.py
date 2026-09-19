"""Shared HTTP layer with retry, backoff, caching and TLS impersonation.

Why this exists: the free endpoints we depend on (Sina, Tencent, Eastmoney, SEC)
all behave differently under load. Some close the connection when the TLS
fingerprint looks like python-requests; some throttle by IP. One hardened client
means providers stay small and readable.
"""

from __future__ import annotations

import logging
import time

import requests

from app import cache
from app.config import settings

log = logging.getLogger(__name__)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    "Connection": "keep-alive",
}

_session: requests.Session | None = None


def _session_for(use_proxy: bool) -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update(_BROWSER_HEADERS)
        adapter = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=16)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        _session = s
    return _session


class FetchError(RuntimeError):
    """Raised when every retry for a request failed."""


def fetch(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int | None = None,
    namespace: str | None = None,
    cache_key: str | None = None,
    ttl: int = 0,
    expect_json: bool = False,
    impersonate: bool = False,
    retries: int | None = None,
):
    """GET `url` with retries + optional caching.

    Returns the decoded JSON when `expect_json`, else the response text.
    Raises FetchError when all attempts fail.
    """
    timeout = timeout or settings.http_timeout
    retries = retries if retries is not None else settings.max_retries
    key = cache_key or url + ("?" + repr(sorted(params.items())) if params else "")

    if namespace and ttl:
        hit = cache.get(namespace, key, ttl)
        if hit is not None:
            return hit

    merged = dict(headers or {})
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            if impersonate:
                # curl_cffi presents a real Chrome TLS fingerprint; some sources
                # (notably Eastmoney's push2 hosts) reject the default one.
                from curl_cffi import requests as cffi

                resp = cffi.get(
                    url,
                    params=params,
                    headers={**_BROWSER_HEADERS, **merged},
                    timeout=timeout,
                    impersonate="chrome",
                    proxies=settings.proxies(),
                )
            else:
                resp = _session_for(True).get(
                    url,
                    params=params,
                    headers=merged or None,
                    timeout=timeout,
                    proxies=settings.proxies(),
                )

            if resp.status_code == 200:
                if expect_json:
                    try:
                        value = resp.json()
                    except ValueError as exc:
                        # Some endpoints wrap JSON in a JS callback.
                        value = _loose_json(resp.text)
                        if value is None:
                            raise FetchError(f"invalid JSON from {url}") from exc
                else:
                    value = resp.text
                if namespace and ttl:
                    cache.put(namespace, key, value)
                if settings.request_delay:
                    time.sleep(settings.request_delay)
                return value

            if resp.status_code in (403, 429, 500, 502, 503, 504):
                last_error = FetchError(f"HTTP {resp.status_code} from {url}")
            else:
                raise FetchError(f"HTTP {resp.status_code} from {url}")

        except FetchError:
            raise
        except Exception as exc:  # network / TLS / parse
            last_error = exc

        if attempt < retries:
            delay = settings.retry_base_delay * (2 ** (attempt - 1))
            log.debug("retry %s/%s for %s in %.1fs (%s)", attempt, retries, url, delay, last_error)
            time.sleep(delay)

    raise FetchError(f"all {retries} attempts failed for {url}: {last_error}")


def _loose_json(text: str):
    """Extract a JSON payload from a JSONP/JS-callback wrapper."""
    import json
    import re

    stripped = text.strip()
    for pattern in (r"x\((\[.*\])\)", r"\((\[.*\])\)", r"(\{.*\})", r"(\[.*\])"):
        m = re.search(pattern, stripped, re.S)
        if not m:
            continue
        try:
            return json.loads(m.group(1))
        except ValueError:
            continue
    return None
