"""Realtime (delayed) quote providers.

Tencent is primary because it returns a very wide field set in one request
(price, market cap, shares, trailing P/E, 52-week range) and is reachable from
networks where Yahoo is blocked. Eastmoney and Yahoo are fallbacks.
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.config import settings
from app.http import FetchError, fetch
from app.models import Quote

log = logging.getLogger(__name__)

TENCENT_QUOTE = "https://qt.gtimg.cn/q=us{ticker}"


def _num(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # drop NaN


# --------------------------------------------------------------------------- #
# Tencent — 73 tilde-separated fields, indices mapped from a live sample.
# --------------------------------------------------------------------------- #
def _tencent(ticker: str) -> Quote | None:
    text = fetch(
        TENCENT_QUOTE.format(ticker=ticker),
        headers={"Referer": "https://gu.qq.com/"},
        namespace="quote_tencent",
        ttl=300,
        expect_json=False,
        retries=3,
    )
    if not isinstance(text, str) or "=" not in text:
        return None
    payload = text.split("=", 1)[1].strip().strip('";')
    f = payload.split("~")
    if len(f) < 50:
        return None

    # Tencent reports market cap in 亿 (100 million) units.
    market_cap = _num(f[45])
    if market_cap is not None:
        market_cap *= 1e8

    quote = Quote(
        ticker=ticker,
        name=(f[46] or f[1] or None) if len(f) > 46 else f[1],
        currency=f[35] or "USD",
        price=_num(f[3]),
        market_cap=market_cap,
        shares_outstanding=_num(f[62]),
        trailing_pe=_num(f[39]),
        eps_current_year=_num(f[47]),
        target_mean_price=None,
        source="Tencent",
        as_of=datetime.now(),
    )
    return quote if quote.price else None


# --------------------------------------------------------------------------- #
# Eastmoney
# --------------------------------------------------------------------------- #
_EM_MARKETS = ("105", "106", "107")
_EM_FIELDS = "f43,f57,f58,f116,f117,f162,f167,f168,f169,f170,f60,f46,f44,f45,f47,f48,f84,f85"


def _eastmoney(ticker: str) -> Quote | None:
    for market in _EM_MARKETS:
        try:
            data = fetch(
                "https://push2.eastmoney.com/api/qt/stock/get",
                params={"secid": f"{market}.{ticker}", "fields": _EM_FIELDS},
                headers={"Referer": "https://quote.eastmoney.com/"},
                namespace="quote_em",
                ttl=300,
                expect_json=True,
                impersonate=True,
                retries=2,
            )
        except FetchError:
            continue
        d = (data or {}).get("data")
        if not d:
            continue
        # Prices come back scaled by 100 (or 1000 for sub-1 currencies).
        price = _num(d.get("f43"))
        if price:
            price /= 100
        return Quote(
            ticker=ticker,
            name=d.get("f58"),
            price=price,
            market_cap=_num(d.get("f116")),
            trailing_pe=_num(d.get("f162")) or None,
            source="Eastmoney",
            as_of=datetime.now(),
        )
    return None


# --------------------------------------------------------------------------- #
# Yahoo
# --------------------------------------------------------------------------- #
def _yahoo(ticker: str) -> Quote | None:
    for host in ("query1", "query2"):
        try:
            data = fetch(
                f"https://{host}.finance.yahoo.com/v7/finance/quote",
                params={"symbols": ticker},
                namespace="quote_yahoo",
                ttl=300,
                expect_json=True,
                impersonate=True,
                retries=2,
            )
        except FetchError:
            continue
        try:
            q = data["quoteResponse"]["result"][0]
        except (KeyError, IndexError, TypeError):
            continue
        return Quote(
            ticker=ticker,
            name=q.get("longName") or q.get("shortName"),
            currency=q.get("currency", "USD"),
            price=_num(q.get("regularMarketPrice")),
            market_cap=_num(q.get("marketCap")),
            shares_outstanding=_num(q.get("sharesOutstanding")),
            trailing_pe=_num(q.get("trailingPE")),
            forward_pe=_num(q.get("forwardPE")),
            eps_forward=_num(q.get("epsForward")),
            eps_current_year=_num(q.get("epsCurrentYear")),
            target_mean_price=_num(q.get("targetMeanPrice")),
            beta=_num(q.get("beta")),
            source="Yahoo",
            as_of=datetime.now(),
        )
    return None


_PIPELINE = [_tencent, _eastmoney, _yahoo]


def get_quote(ticker: str, *, allow_yahoo: bool | None = None) -> Quote:
    """Resolve a quote, merging partial provider results."""
    ticker = ticker.upper().strip()
    allow_yahoo = settings.enable_yahoo if allow_yahoo is None else allow_yahoo
    errors: list[str] = []
    best: Quote | None = None

    for provider in _PIPELINE:
        if provider is _yahoo and not allow_yahoo:
            continue
        try:
            result = provider(ticker)
        except Exception as exc:
            errors.append(f"{provider.__name__}: {exc}")
            continue
        if not result:
            errors.append(f"{provider.__name__}: empty")
            continue
        if best is None:
            best = result
        else:
            # Fill only the gaps, preserving provenance of the primary source.
            for field in result.model_fields:
                if getattr(best, field, None) in (None, "", 0) and getattr(result, field, None):
                    setattr(best, field, getattr(result, field))
        if best.price and best.market_cap:
            break

    if best is None:
        raise FetchError(f"no quote for {ticker} ({'; '.join(errors[:3])})")
    return best
