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
from datetime import date, datetime

from app import cache
from app.config import settings
from app.models import (
    AnalystAction,
    AnalystDetail,
    AnalystView,
    EarningsEstimate,
    EarningsSurprise,
    RatingCounts,
)

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
    """A working crumb token, paired with the session that obtained it.

    The crumb is bound to the session's cookie, so the two must never be reused
    apart: re-priming cookies against a cached crumb yields
    `Invalid Crumb` 401s. The token is therefore cached only in memory and only
    beside the live session — persisting it to disk would survive exactly the
    restart that invalidates it.
    """
    global _crumb_memory
    with _lock:
        if not force and _crumb_memory:
            return _crumb_memory
        session = _get_session()
        # Only prime cookies when the jar is empty. Requesting the home page
        # again would rotate the cookie the crumb is paired with.
        if not session.cookies:
            try:
                session.get(_HOME, timeout=settings.http_timeout)
            except Exception as exc:
                log.debug("yahoo home priming failed: %s", exc)
        try:
            response = session.get(_CRUMB_URL, timeout=settings.http_timeout)
        except Exception as exc:
            log.debug("yahoo crumb request failed: %s", exc)
            return None
        token = (response.text or "").strip() if response.status_code == 200 else ""
        if not token:
            return None
        _crumb_memory = token
        return token


def _fetch_summary(ticker: str, modules: str | None = None) -> dict | None:
    """One quoteSummary call, retried once with a fresh session on 401/403."""
    wanted = modules or _MODULES
    for attempt in (1, 2):
        try:
            session = _get_session()
            token = _crumb()
            if not token:
                return None
            response = session.get(
                _SUMMARY.format(ticker=ticker),
                params={"modules": wanted, "crumb": token},
                timeout=settings.http_timeout,
            )
        except Exception as exc:
            log.debug("yahoo analyst %s failed: %s", ticker, exc)
            if attempt == 2:
                return None
            _reset_session()
            continue
        if response.status_code in (401, 403):
            # Cookie and crumb have gone out of step: rebuild both together.
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


# --------------------------------------------------------------------------- #
# analyst detail: ratings, published actions, earnings surprises and estimates
# --------------------------------------------------------------------------- #
_DETAIL_MODULES = (
    "financialData,recommendationTrend,upgradeDowngradeHistory,"
    "earningsHistory,earningsTrend,calendarEvents"
)
_DETAIL_NS = "analyst_detail"
_DETAIL_TTL = 12 * 3600

# What Yahoo can actually supply. Stated in the payload so the UI never implies
# a longer history than exists: the quarterly surprise module returns four
# quarters, and the public analysis page renders the same four.
HISTORY_LIMITS = (
    "Yahoo publishes four quarters of reported-vs-consensus EPS; longer "
    "histories on other platforms come from paid feeds."
)


def _datestr(value) -> date | None:
    """Yahoo dates arrive as {'fmt': '2025-09-30'} or as an epoch second."""
    if isinstance(value, dict):
        fmt = value.get("fmt")
        if isinstance(fmt, str):
            try:
                return date.fromisoformat(fmt[:10])
            except ValueError:
                pass
        value = value.get("raw")
    if isinstance(value, (int, float)) and value > 0:
        try:
            return datetime.utcfromtimestamp(int(value)).date()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _ratings(payload: dict) -> list[RatingCounts]:
    rows = (payload.get("recommendationTrend") or {}).get("trend") or []
    out: list[RatingCounts] = []
    for row in rows:
        try:
            out.append(RatingCounts(
                period=str(row.get("period") or ""),
                strong_buy=int(row.get("strongBuy") or 0),
                buy=int(row.get("buy") or 0),
                hold=int(row.get("hold") or 0),
                sell=int(row.get("sell") or 0),
                strong_sell=int(row.get("strongSell") or 0),
            ))
        except (TypeError, ValueError):
            continue
    return out


def _actions(payload: dict, limit: int = 400) -> list[AnalystAction]:
    """Published rating/target changes, newest first.

    Every row keeps its own grade change and target movement; summarising them
    into a single sentiment number would discard the firm-level detail this
    section exists to show.
    """
    rows = (payload.get("upgradeDowngradeHistory") or {}).get("history") or []
    out: list[AnalystAction] = []
    for row in rows:
        when = _datestr(row.get("epochGradeDate"))
        if when is None:
            continue
        out.append(AnalystAction(
            date=when,
            firm=str(row.get("firm") or "—"),
            action=(row.get("action") or None),
            from_grade=(row.get("fromGrade") or None) or None,
            to_grade=(row.get("toGrade") or None) or None,
            price_target_action=(row.get("priceTargetAction") or None) or None,
            price_target=_number(row.get("currentPriceTarget")),
            prior_price_target=_number(row.get("priorPriceTarget")),
        ))
    out.sort(key=lambda a: a.date, reverse=True)
    return out[:limit]


def _earnings_history(payload: dict) -> list[EarningsSurprise]:
    rows = (payload.get("earningsHistory") or {}).get("history") or []
    out: list[EarningsSurprise] = []
    for row in rows:
        out.append(EarningsSurprise(
            period=str(row.get("period") or ""),
            quarter_end=_datestr(row.get("quarter")),
            eps_actual=_number(row.get("epsActual")),
            eps_estimate=_number(row.get("epsEstimate")),
            eps_difference=_number(row.get("epsDifference")),
            surprise_pct=_number(row.get("surprisePercent")),
            currency=(row.get("currency") or None),
        ))
    # Oldest first: the chart reads left to right in time.
    out = [e for e in out if e.quarter_end is not None]
    out.sort(key=lambda e: e.quarter_end)
    return out


def _estimates(payload: dict) -> list[EarningsEstimate]:
    rows = (payload.get("earningsTrend") or {}).get("trend") or []
    out: list[EarningsEstimate] = []
    for row in rows:
        estimate = row.get("earningsEstimate") or {}
        revenue = row.get("revenueEstimate") or {}
        count = _number((estimate.get("numberOfAnalysts") or {}))
        out.append(EarningsEstimate(
            period=str(row.get("period") or ""),
            end_date=(row.get("endDate") or None),
            eps_avg=_number(estimate.get("avg")),
            eps_low=_number(estimate.get("low")),
            eps_high=_number(estimate.get("high")),
            eps_year_ago=_number(estimate.get("yearAgoEps")),
            analyst_count=int(count) if count is not None else None,
            growth=_number(estimate.get("growth")) if estimate.get("growth") is not None else _number(row.get("growth")),
            revenue_avg=_number(revenue.get("avg")),
        ))
    return out


def _next_earnings(payload: dict) -> str | None:
    earnings = (payload.get("calendarEvents") or {}).get("earnings") or {}
    stamps = [d.get("raw") for d in (earnings.get("earningsDate") or [])]
    dates = []
    for stamp in stamps:
        parsed = _datestr(stamp)
        if parsed:
            dates.append(parsed)
    today = date.today()
    upcoming = sorted(d for d in dates if d >= today)
    return upcoming[0].isoformat() if upcoming else None


def get_analyst_detail(ticker: str, *, allow_yahoo: bool = True) -> AnalystDetail | None:
    """Ratings, published actions, earnings surprises and forward estimates.

    One quoteSummary call carries all five modules, so this is a single request
    rather than one per section.
    """
    if not allow_yahoo:
        return None
    ticker = ticker.upper().strip()
    cached = cache.get(_DETAIL_NS, ticker, _DETAIL_TTL)
    if cached:
        try:
            return AnalystDetail.model_validate(cached)
        except Exception:  # pragma: no cover - a stale cache shape is not fatal
            pass

    payload = _fetch_summary(ticker, modules=_DETAIL_MODULES)
    if payload is None:
        return None
    data = payload.get("financialData") or {}
    history = _earnings_history(payload)
    ratings = _ratings(payload)
    estimates = _estimates(payload)
    actions = _actions(payload)
    detail = AnalystDetail(
        ticker=ticker,
        target_mean=_number(data.get("targetMeanPrice")),
        target_median=_number(data.get("targetMedianPrice")),
        target_high=_number(data.get("targetHighPrice")),
        target_low=_number(data.get("targetLowPrice")),
        recommendation=(data.get("recommendationKey") or None),
        recommendation_mean=_number(data.get("recommendationMean")),
        current_price=_number(data.get("currentPrice")),
        ratings=ratings,
        actions=actions,
        earnings_history=history,
        estimates=estimates,
        next_earnings_date=_next_earnings(payload),
        source="Yahoo quoteSummary (crumb-authenticated)",
        as_of=datetime.now(),
        notes=[HISTORY_LIMITS],
    )
    if not (ratings or actions or history or estimates) and detail.target_mean is None:
        return None
    cache.put(_DETAIL_NS, ticker, detail.model_dump(mode="json"))
    return detail
