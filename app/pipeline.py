"""Pipeline orchestration: fetch -> compute -> score.

This is the single place that knows how a peer set becomes ranked rows, so the
API, the CLI and the scheduler all behave identically.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Callable, Iterable

from app.config import settings
from app.health import yahoo_usable
from app.engine import metrics, scoring
from app.models import MetricRow, PriceHistory
from app.providers import fundamentals as fund_provider
from app.providers import prices as price_provider
from app.providers import quotes as quote_provider
from app.providers.prices import PriceBasis

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, str, int, int], None]

BENCHMARK = "SPY"


def _noop(ticker: str, status: str, done: int, total: int) -> None:
    return None


def validate_ticker(ticker: str) -> str:
    """Normalise and sanity-check a user-supplied symbol."""
    symbol = ticker.strip().upper()
    if not symbol:
        raise ValueError("empty ticker")
    if len(symbol) > 12:
        raise ValueError(f"ticker too long: {symbol}")
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-^")
    if not set(symbol) <= allowed:
        raise ValueError(f"invalid characters in ticker: {symbol}")
    return symbol


def get_benchmark(basis: PriceBasis | None = None) -> PriceHistory | None:
    """SPY is optional: without it beta is simply left blank."""
    try:
        return price_provider.get_price_history(
            BENCHMARK, allow_yahoo=yahoo_usable(), basis=basis
        )
    except Exception as exc:
        log.warning("benchmark %s unavailable, beta will be blank: %s", BENCHMARK, exc)
        return None


def get_price_basis() -> PriceBasis:
    """Pick the price basis for this run (adjusted when Yahoo can supply SPY)."""
    try:
        return price_provider.resolve_basis(BENCHMARK, allow_yahoo=yahoo_usable())
    except Exception as exc:  # never let basis discovery break a refresh
        log.debug("price basis probe failed: %s", exc)
        return PriceBasis(adjusted=False, source="Sina")


def fetch_one(
    ticker: str,
    benchmark: PriceHistory | None = None,
    basis: PriceBasis | None = None,
) -> tuple[MetricRow | None, str | None]:
    """Fetch and compute a single ticker. Returns (row, error)."""
    started = time.perf_counter()
    try:
        history = price_provider.get_price_history(
            ticker, allow_yahoo=yahoo_usable(), basis=basis
        )
    except Exception as exc:
        return None, f"price history unavailable: {exc}"

    try:
        quote = quote_provider.get_quote(ticker, allow_yahoo=yahoo_usable())
    except Exception as exc:
        # A quote failure is recoverable: price + name can come from history alone.
        log.info("quote failed for %s (%s); continuing with price history", ticker, exc)
        from app.models import Quote

        quote = Quote(
            ticker=ticker,
            price=history.last(),
            currency=history.currency,
            source=f"{history.source} (quote fallback)",
            as_of=datetime.now(),
        )

    try:
        fund = fund_provider.get_fundamentals(ticker, allow_yahoo=yahoo_usable())
    except Exception as exc:
        return None, f"fundamentals unavailable: {exc}"

    # Analyst consensus is enrichment: it fills the target-price and upside
    # columns when Yahoo is reachable and is simply absent otherwise.
    analyst = None
    try:
        from app.providers.yahoo_analyst import get_analyst_view

        analyst = get_analyst_view(ticker, allow_yahoo=yahoo_usable())
    except Exception as exc:
        log.debug("analyst consensus unavailable for %s: %s", ticker, exc)

    # Translate a depositary receipt's filing currency into USD before the
    # cross-sectional comparison, for any receipt whose ADS ratio is known. This was
    # TSM-only, which is why NVO — filing in DKK and trading in USD — had its price
    # multiples withheld and left blank while Yahoo publishes them.
    #
    # A receipt whose ratio is unknown is deliberately left untranslated: the ratio
    # cannot be inferred, and guessing it would misstate every multiple by that factor
    # while looking plausible.
    if (fund.currency or "USD").upper() != "USD" and (quote.currency or "USD").upper() == "USD":
        from app.providers.adr import _ADS_RATIO, normalize_depositary
        from app.providers.fx import usd_per

        if ticker.upper() in _ADS_RATIO:
            fx = usd_per((fund.currency.upper(),), allow_yahoo=yahoo_usable())
            if fx is not None:
                try:
                    translated = normalize_depositary(fund, quote, *fx)
                    if translated is not None:
                        fund = translated
                except ValueError as exc:
                    log.warning("%s ADR normalization unavailable: %s", ticker, exc)
            else:
                log.info("%s: no FX rate for %s, price multiples withheld",
                         ticker, fund.currency)

    try:
        row = metrics.compute_row(
            ticker, history=history, quote=quote, fund=fund, benchmark=benchmark,
            analyst=analyst,
        )
    except Exception as exc:
        log.exception("metric computation failed for %s", ticker)
        return None, f"metric computation failed: {exc}"

    row.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return row, None


def build_rows(
    tickers: Iterable[str],
    *,
    progress: ProgressFn | None = None,
    benchmark: PriceHistory | None = None,
    max_workers: int | None = None,
    basis: PriceBasis | None = None,
    strategy: str | None = None,
) -> tuple[list[MetricRow], dict[str, str]]:
    """Fetch, compute and score a peer set."""
    progress = progress or _noop
    symbols: list[str] = []
    for raw in tickers:
        try:
            symbol = validate_ticker(raw)
        except ValueError as exc:
            log.warning("skipping %r: %s", raw, exc)
            continue
        if symbol not in symbols:
            symbols.append(symbol)

    total = len(symbols)
    if total == 0:
        return [], {}

    # Resolve the price basis before any ticker is fetched so the stock and the
    # benchmark are always measured on the same convention.
    if basis is None:
        basis = get_price_basis()
    if benchmark is None:
        progress("__benchmark__", "fetching SPY benchmark", 0, total)
        benchmark = get_benchmark(basis)

    rows: list[MetricRow] = []
    errors: dict[str, str] = {}
    done = 0
    workers = max_workers or max(1, min(settings.max_concurrency, total))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_one, s, benchmark, basis): s for s in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            done += 1
            try:
                row, error = future.result()
            except Exception as exc:  # defensive: never let one peer kill the run
                row, error = None, f"unexpected failure: {exc}"
            if row is not None:
                rows.append(row)
                progress(symbol, row.status, done, total)
            else:
                errors[symbol] = error or "unknown error"
                progress(symbol, f"failed: {error}", done, total)

    scoring.score_peers(rows, strategy=strategy)
    rows.sort(key=lambda r: (r.rank is None, r.rank or 999, r.ticker))
    return rows, errors
