"""CBOE official delayed option chain, with Greeks.

`cdn.cboe.com/api/global/delayed_quotes/options/{ticker}.json` returns the whole
chain for one ticker in a single request — about 3,600 contracts for AAPL, 13,000
for SPY — and every contract carries `iv`, `delta`, `gamma`, `vega`, `theta` and
`rho`, plus a real bid/ask. The feed also publishes `iv30`, a 30-day implied
volatility for the underlying.

That is a categorical improvement over the Yahoo chain this app used before, which
reports placeholder IV (clipped to 1e-5 or 0.5, quantised to sixteenths) with
bid and ask at zero, and no Greeks at all. The app had to *invert* a volatility
from last-traded prices to say anything about IV; with this feed the number is
published, and a real dealer-gamma figure becomes possible because gamma is
reported rather than assumed.

**Compliance.** Cboe's Use of Content policy requires prior written approval and a
licence for commercial use, and this data is delayed. The tier is recorded on the
snapshot and surfaced in the UI and the README rather than left implicit. Yahoo's
terms are no more permissive (personal use only), so this is a quality upgrade at
the same tier, not a new exposure — but it is a real constraint and is stated.

Two parsing details that matter:

* contracts are keyed by OCC/OSI symbol, `ROOT + YYMMDD + C|P + strike*1000`, and
  the root may contain digits for adjusted contracts after a split (`NVDA1`), so
  the parse anchors on the fixed-width tail rather than on a letters-only root;
* greeks arrive as 0.0 rather than null for deep in/out-of-the-money strikes
  where the model does not produce one, so "zero" is not treated as "measured".
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

from app import cache
from app.config import settings
from app.http import fetch
from app.models import OptionsChain, OptionsContract, OptionsExpiry

log = logging.getLogger(__name__)

NS = "options_cboe"
TTL = 15 * 60

BASE = "https://cdn.cboe.com/api/global/delayed_quotes/options/{ticker}.json"
SOURCE = "CBOE official delayed quotes (cdn.cboe.com)"
# Cboe requires prior written approval for commercial use or redistribution.
COMPLIANCE_TIER = "C — delayed; Cboe requires prior approval for commercial use"

# ROOT may contain digits (adjusted contracts after a split), so the tail is
# anchored: 6 date digits + 1 C/P + 8 strike digits = 15 fixed characters.
_OSI = re.compile(
    r"^(?P<root>[A-Z][A-Z0-9]*?)(?P<y>\d{2})(?P<m>\d{2})(?P<d>\d{2})"
    r"(?P<cp>[CP])(?P<strike>\d{8})$"
)


def parse_osi(symbol: str) -> dict | None:
    """OCC/OSI symbol to expiry, kind and strike. None when unparseable."""
    match = _OSI.match(str(symbol or "").upper())
    if not match:
        return None
    g = match.groupdict()
    try:
        expiry = date(2000 + int(g["y"]), int(g["m"]), int(g["d"]))
    except ValueError:
        return None
    strike = int(g["strike"]) / 1000.0
    if strike <= 0:
        return None
    return {
        "expiry": expiry,
        "kind": "call" if g["cp"] == "C" else "put",
        "strike": strike,
    }


def _number(value) -> float | None:
    """A reported number, or None. Zero stays zero but is flagged by the caller.

    The feed writes 0.0 where a Greek is not meaningful rather than null, so this
    preserves 0.0 and lets the analytics decide — treating it as missing here would
    discard a legitimate zero delta on a deep strike.
    """
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def fetch_chain(ticker: str, *, allow_network: bool = True,
                max_expiries: int = 10) -> OptionsChain | None:
    """The whole CBOE chain, grouped by expiry, nearest first.

    One request. `max_expiries` bounds what is kept because the analytics only
    need the front months and the ~30-day reference, and carrying 25 expiries
    through every computation costs time for nothing.
    """
    if not allow_network:
        return None
    ticker = ticker.upper().strip()
    key = f"{ticker}:{max_expiries}"
    hit = cache.get(NS, key, TTL)
    if hit:
        try:
            return OptionsChain.model_validate(hit)
        except Exception:  # pragma: no cover - a stale cache shape is not fatal
            pass

    try:
        payload = fetch(
            BASE.format(ticker=ticker),
            headers={"User-Agent": settings.sec_user_agent or "FinanceRanker/0.1"},
            namespace=NS,
            ttl=TTL,
            expect_json=True,
            retries=2,
        )
    except Exception as exc:  # noqa: BLE001 - the caller falls back to Yahoo
        log.debug("cboe fetch failed for %s: %s", ticker, exc)
        return None

    data = (payload or {}).get("data") or {}
    rows = data.get("options") or []
    if not rows:
        return None

    spot = _number(data.get("current_price")) or _number(data.get("close"))
    # `iv30` is the underlying's 30-day implied volatility, published directly.
    iv30 = _number(data.get("iv30"))
    # CBOE reports iv30 in percent (12.424 = 12.4%).
    iv30 = (iv30 / 100.0) if iv30 and iv30 > 1.5 else iv30

    by_expiry: dict[date, list[OptionsContract]] = {}
    unparsed = 0
    for row in rows:
        meta = parse_osi(row.get("option"))
        if meta is None:
            unparsed += 1
            continue
        contract = OptionsContract(
            kind=meta["kind"],
            strike=meta["strike"],
            expiration=meta["expiry"],
            volume=_number(row.get("volume")),
            open_interest=_number(row.get("open_interest")),
            last_price=_number(row.get("last_trade_price")) or _number(row.get("theo")),
            bid=_number(row.get("bid")),
            ask=_number(row.get("ask")),
            # Reported, not inverted. This is the whole reason for the source.
            implied_volatility=_number(row.get("iv")),
            delta=_number(row.get("delta")),
            gamma=_number(row.get("gamma")),
            vega=_number(row.get("vega")),
            theta=_number(row.get("theta")),
            rho=_number(row.get("rho")),
            in_the_money=bool(row.get("delta") is not None and abs(_number(row.get("delta")) or 0) > 0.5),
            contract_symbol=str(row.get("option") or ""),
        )
        by_expiry.setdefault(meta["expiry"], []).append(contract)

    if not by_expiry:
        return None

    today = date.today()
    # Only expiries that have not passed, nearest first.
    ordered = sorted(e for e in by_expiry if e >= today)[:max_expiries]
    expiries = [
        OptionsExpiry(expiration=expiry, calls=sorted(
            (c for c in by_expiry[expiry] if c.kind == "call"), key=lambda c: c.strike),
            puts=sorted(
                (c for c in by_expiry[expiry] if c.kind == "put"), key=lambda c: c.strike))
        for expiry in ordered
    ]
    if not expiries:
        return None

    chain = OptionsChain(
        ticker=ticker,
        currency="USD",
        spot=spot,
        as_of=datetime.now(),
        source=SOURCE,
        expiries_available=len(by_expiry),
        expiries=expiries,
        iv30=iv30,
        compliance=COMPLIANCE_TIER,
        has_greeks=True,
    )
    if unparsed:
        log.debug("cboe %s: %d symbols did not parse", ticker, unparsed)
    cache.put(NS, key, chain.model_dump(mode="json"))
    return chain
