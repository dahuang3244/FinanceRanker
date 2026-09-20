"""Yahoo analyst consensus (target price, dispersion, recommendation).

Yahoo's `quoteSummary` endpoint requires a cookie *and* a matching "crumb"
token, plus a Chrome TLS fingerprint. Plain requests get a 401; `v7/quote` and
`v6/quote` are gone. So this provider keeps one impersonated session, obtains a
crumb once, and reuses it — and treats every failure as "no analyst data"
rather than an error, because nothing else in a peer row depends on it.

Why it is worth the trouble: analyst targets were the one figure that used to be
blank for *every* ticker, which made the valuation block unusable for
comparison even though the rest of the row was complete.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from app import cache
from app.config import settings
from app.models import AnalystView

log = logging.getLogger(__name__)

_NS = "analyst_yahoo"
_TTL = 12 * 3600
_CRUMB_NS = "analyst_yahoo_crumb"
_CRUMB_TTL = 3 * 3600

_HOME = "https://finance.yahoo.com"
_CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
_SUMMARY = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
_MODULES = "financialData,defaultKeyStatistics"

_session = None
_crumb_memory: str | None = None
# One refresh fetches many tickers in a thread pool; the session and its crumb
# are process-wide state, so handshakes are serialised.
_lock = threading.Lock()


def _number(value) -> float | None:
    """Yahoo wraps numbers as {"raw": .., "fmt": ..}; accept both shapes."""
    if isinstance(value, dict):
        value = value.get("raw")
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _get_session():
    global _session
    if _session is None:
        from curl_cffi import requests as cffi

        _session = cffi.Session(impersonate="chrome")
    return _session


def _reset_session() -> None:
    global _session, _crumb_memory
    with _lock:
        try:
            if _session is not None:
                _session.close()
        except Exception:  # pragma: no cover - closing must never raise
            pass
        _session = None
        _crumb_memory = None


def _crumb(*, force: bool = False) -> str | None:
    """A working crumb token, cached in memory then on disk."""
    global _crumb_memory
    with _lock:
        if not force:
            if _crumb_memory:
                return _crumb_memory
            hit = cache.get(_CRUMB_NS, "crumb", _CRUMB_TTL)
            if hit:
                _crumb_memory = hit
                return hit
        session = _get_session()
        session.get(_HOME, timeout=settings.http_timeout)
        response = session.get(_CRUMB_URL, timeout=settings.http_timeout)
        token = (response.text or "").strip() if response.status_code == 200 else ""
        if not token:
            return None
        _crumb_memory = token
        cache.put(_CRUMB_NS, "crumb", token)
        return token


def _fetch_summary(ticker: str) -> dict | None:
    """One quoteSummary call, retried once with a fresh session on 401/403."""
    for attempt in (1, 2):
        token = _crumb(force=attempt == 2)
        if not token:
            return None
        try:
            session = _get_session()
            response = session.get(
                _SUMMARY.format(ticker=ticker),
                params={"modules": _MODULES, "crumb": token},
                timeout=settings.http_timeout,
            )
        except Exception as exc:
            log.debug("yahoo analyst %s failed: %s", ticker, exc)
            if attempt == 2:
                return None
            _reset_session()
            continue
        if response.status_code in (401, 403):
            # The crumb is stale: drop it and try once more with a new one.
            _reset_session()
            if attempt == 2:
                return None
            continue
        if response.status_code != 200:
            return None
        try:
            result = (response.json().get("quoteSummary") or {}).get("result") or [{}]
        except ValueError:
            return None
        return result[0] if result else None
    return None


def get_analyst_view(ticker: str, *, allow_yahoo: bool = True) -> AnalystView | None:
    """Analyst consensus for `ticker`, or None when Yahoo has nothing to say."""
    if not allow_yahoo:
        return None
    ticker = ticker.upper().strip()
    cached = cache.get(_NS, ticker, _TTL)
    if cached:
        try:
            return AnalystView.model_validate(cached)
        except Exception:  # pragma: no cover - a stale cache shape is not fatal
            pass

    payload = _fetch_summary(ticker)
    if payload is None:
        return None
    data = payload.get("financialData") or {}
    stats = payload.get("defaultKeyStatistics") or {}
    view = AnalystView(
        ticker=ticker,
        target_mean=_number(data.get("targetMeanPrice")),
        target_median=_number(data.get("targetMedianPrice")),
        target_high=_number(data.get("targetHighPrice")),
        target_low=_number(data.get("targetLowPrice")),
        analyst_count=_number(data.get("numberOfAnalystOpinions")),
        recommendation=(data.get("recommendationKey") or None),
        forward_pe=_number(stats.get("forwardPE")),
        yahoo_beta=_number(stats.get("beta")),
        source="Yahoo quoteSummary (crumb-authenticated)",
        as_of=datetime.now(),
    )
    if view.target_mean is None and view.forward_pe is None and view.yahoo_beta is None:
        return None
    cache.put(_NS, ticker, view.model_dump(mode="json"))
    return view
