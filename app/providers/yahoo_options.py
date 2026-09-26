"""Yahoo option-chain provider.

Two properties of this endpoint shape the whole module:

* it requires the crumb-authenticated session, and answers 401 without one — the
  same cookie/crumb pairing the analyst provider relies on;
* it returns **one expiry per request** even though it lists every available
  expiry, so a useful view needs several calls. They are issued together and
  cached, because the chain changes slowly relative to a page load.

The `impliedVolatility` field is parsed but not trusted: most values are
placeholder data (1e-5 or exactly 0.5) with bid and ask reported as zero. It is
carried through so a caller can see the raw input, never as a basis for a metric.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app import cache
from app.config import settings
from app.models import OptionsChain, OptionsContract, OptionsExpiry
from app.providers.yahoo_analyst import _crumb, _get_session, _number

log = logging.getLogger(__name__)

NS = "options_yahoo"
TTL = 30 * 60

# A chain can list 30+ expiries; the nearest handful carry nearly all the
# meaningful open interest, and each costs a request.
MAX_EXPIRIES = 6
# "Near month" is the front expiry plus this window. It was 35 days, which with
# only six expiries fetched covered every one of them — so the near-month PCR
# equalled the whole-chain PCR to the last digit and told the reader nothing. A
# week keeps the measure meaning what its name says: the nearest expiry and any
# dated within days of it.
NEAR_MONTH_DAYS = 7
# The horizon volatility is measured at. One extra expiry is fetched near this
# point so a monthly reading is actually available on names whose listed expiries
# jump from a weekly to a monthly date.
REFERENCE_TARGET_DAYS = 30
_HOSTS = ("query1", "query2")


def _contracts(rows: list[dict], kind: str) -> list[OptionsContract]:
    out: list[OptionsContract] = []
    for row in rows or []:
        strike = _number(row.get("strike"))
        if strike is None:
            continue
        stamp = row.get("expiration")
        try:
            expiration = datetime.utcfromtimestamp(int(stamp)).date() if stamp else None
        except (TypeError, ValueError, OverflowError, OSError):
            expiration = None
        if expiration is None:
            continue
        out.append(OptionsContract(
            kind=kind,
            strike=strike,
            expiration=expiration,
            volume=_number(row.get("volume")),
            open_interest=_number(row.get("openInterest")),
            last_price=_number(row.get("lastPrice")),
            change=_number(row.get("change")),
            bid=_number(row.get("bid")),
            ask=_number(row.get("ask")),
            # Kept for inspection only; see the module docstring.
            implied_volatility=_number(row.get("impliedVolatility")),
            in_the_money=bool(row.get("inTheMoney")),
            contract_symbol=row.get("contractSymbol") or "",
        ))
    return out


def _one_expiry(ticker: str, stamp: int | None) -> tuple[int | None, dict] | None:
    """Fetch a single expiry's chain. `None` stamp asks for the front month."""
    params: dict[str, Any] = {}
    token = _crumb()
    if token:
        params["crumb"] = token
    if stamp is not None:
        params["date"] = int(stamp)
    last_error: Exception | None = None
    for host in _HOSTS:
        try:
            session = _get_session()
            resp = session.get(
                f"https://{host}.finance.yahoo.com/v7/finance/options/{ticker}",
                params=params,
                timeout=settings.http_timeout,
            )
        except Exception as exc:      # network / TLS
            last_error = exc
            continue
        if resp.status_code in (401, 403):
            # Cookie and crumb have drifted apart; rebuild both and let the
            # caller retry rather than returning a half-empty chain.
            from app.providers.yahoo_analyst import _reset_session

            _reset_session()
            last_error = RuntimeError(f"HTTP {resp.status_code}")
            continue
        if resp.status_code != 200:
            last_error = RuntimeError(f"HTTP {resp.status_code}")
            continue
        try:
            payload = resp.json()
        except ValueError:
            last_error = RuntimeError("invalid JSON")
            continue
        results = (payload.get("optionChain") or {}).get("result") or []
        if not results:
            return None
        return stamp, results[0]
    if last_error:
        log.debug("options %s expiry=%s failed: %s", ticker, stamp, last_error)
    return None


def fetch_chain(
    ticker: str,
    *,
    expiries: int = MAX_EXPIRIES,
    allow_yahoo: bool = True,
) -> OptionsChain | None:
    """The nearest `expiries` option chains, or None when there is no market."""
    if not allow_yahoo:
        return None
    ticker = ticker.upper().strip()
    key = f"{ticker}:{expiries}"
    hit = cache.get(NS, key, TTL)
    if hit:
        try:
            return OptionsChain.model_validate(hit)
        except Exception:  # pragma: no cover - a stale cache shape is not fatal
            pass

    # The first request also tells us which expiries exist.
    first = _one_expiry(ticker, None)
    if first is None:
        return None
    _, head = first
    available = head.get("expirationDates") or []
    quote = head.get("quote") or {}
    spot = _number(quote.get("regularMarketPrice"))

    # `options` on the front-month response may already carry several expiries;
    # use whatever it returned and only fetch the ones still missing.
    chains: dict[int, dict] = {}
    for entry in head.get("options") or []:
        stamp = entry.get("expirationDate")
        if stamp:
            chains[int(stamp)] = entry

    wanted = [int(s) for s in available[:expiries]]

    # Volatility is measured from an expiry about a month out, and the nearest few
    # weeklies do not contain one. Taking only `available[:expiries]` left AAPL,
    # SPY and NVDA measuring vol off a 7-12 day expiry while the screen called it a
    # monthly figure. The expiry nearest the target is fetched too, so the promise
    # and the data agree. Added *alongside* the near expiries, not instead of them,
    # because the near ones drive flow and concentration.
    target_date = date.today() + timedelta(days=REFERENCE_TARGET_DAYS)
    reference_stamp: int | None = None
    if available:
        reference_stamp = int(min(
            available,
            key=lambda s: abs((datetime.utcfromtimestamp(s).date() - target_date).days),
        ))
        if reference_stamp not in wanted:
            wanted.append(reference_stamp)

    missing = [s for s in wanted if s not in chains]
    if missing:
        with ThreadPoolExecutor(max_workers=min(4, len(missing))) as pool:
            for result in pool.map(lambda s: _one_expiry(ticker, s), missing):
                if result is None:
                    continue
                stamp, payload = result
                for entry in payload.get("options") or []:
                    got = entry.get("expirationDate")
                    if got:
                        chains[int(got)] = entry

    built: list[OptionsExpiry] = []
    # Chronological, so the front month stays first for flow and concentration.
    for stamp in sorted(chains):
        if stamp not in wanted:
            continue
        entry = chains.get(stamp)
        if not entry:
            continue
        calls = _contracts(entry.get("calls") or [], "call")
        puts = _contracts(entry.get("puts") or [], "put")
        if not calls and not puts:
            continue
        built.append(OptionsExpiry(
            expiration=datetime.utcfromtimestamp(stamp).date(),
            calls=calls,
            puts=puts,
        ))
    if not built:
        return None

    chain = OptionsChain(
        ticker=ticker,
        currency=(quote.get("currency") or "USD"),
        spot=spot,
        as_of=datetime.now(),
        source="Yahoo options chain (crumb-authenticated)",
        expiries_available=len(available),
        expiries=built,
    )
    cache.put(NS, key, chain.model_dump(mode="json"))
    return chain
