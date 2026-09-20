"""Price-history providers (daily bars).

Provider order is deliberate. Sina's US daily endpoint is the most reliable from
networks where Yahoo is blocked and Eastmoney's `push2` load-balancers are
flaky; Eastmoney and Yahoo remain as fallbacks, and `akshare` is used as a
higher-level facade when it can reach its upstream.

Price basis matters as much as availability. Sina returns split-adjusted closes
that are *not* dividend-adjusted, so a raw Sina return understates a dividend
payer's total return. Yahoo's `adjclose` adjusts for both. Because stock returns
are compared against a benchmark, the stock and the benchmark must come from the
same basis — mixing them manufactures or destroys excess return equal to the
missing dividend yield. `resolve_basis()` picks one basis up front and every
call then honours it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime

from app.http import FetchError, fetch
from app.models import PriceHistory, PricePoint

log = logging.getLogger(__name__)

SINA_US_DAILY = (
    "https://stock.finance.sina.com.cn/usstock/api/jsonp.php/x/"
    "US_MinKService.getDailyK?symbol={symbol}"
)
EASTMONEY_KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# How much history to keep: enough for a 5-year CAGR plus the 1-year window.
HISTORY_DAYS = 6 * 366
YAHOO_RANGE = "6y"


@dataclass(frozen=True)
class PriceBasis:
    """The price convention every row in one refresh is computed on.

    `adjusted` means dividends *and* splits are applied, so the closes are a
    total-return series. `source` is the provider that supplied it, recorded so
    a report can state which basis its returns are quoted on.
    """

    adjusted: bool
    source: str

    @property
    def label(self) -> str:
        return "total-return (dividend & split adjusted)" if self.adjusted else "split-adjusted price"


def resolve_basis(benchmark_ticker: str = "SPY", *, allow_yahoo: bool = False) -> PriceBasis:
    """Choose the price basis for a run by asking for the benchmark first.

    Trying the benchmark first is what keeps the choice honest: if Yahoo cannot
    deliver SPY on an adjusted basis, every stock is measured on the Sina basis
    instead of silently comparing an adjusted stock against a raw benchmark.

    A basis is a *promise about arithmetic*, so it comes with a guarantee: every
    series fetched under it is checked against the promise (`used_adjusted`
    must agree). A provider that answers on the wrong convention is skipped
    rather than mixed in.
    """
    if allow_yahoo:
        try:
            spy = _yahoo(benchmark_ticker)
        except Exception as exc:  # a probe failure is never fatal
            log.debug("benchmark basis probe failed for %s: %s", benchmark_ticker, exc)
            spy = None
        if spy is not None and spy.used_adjusted and len(spy.points) > 260:
            return PriceBasis(adjusted=True, source="Yahoo")
    return PriceBasis(adjusted=False, source="Sina")


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# Sina
# --------------------------------------------------------------------------- #
def _sina(ticker: str) -> PriceHistory | None:
    """Sina returns the full daily history since listing as a JSONP array."""
    text = fetch(
        SINA_US_DAILY.format(symbol=ticker),
        headers={"Referer": "https://finance.sina.com.cn/"},
        namespace="price_sina",
        ttl=6 * 3600,
        expect_json=False,
    )
    from app.http import _loose_json

    rows = _loose_json(text) if isinstance(text, str) else text
    if not isinstance(rows, list) or not rows:
        return None
    points: list[PricePoint] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        d = _parse_date(str(row.get("d", "")))
        try:
            close = float(row.get("c"))
        except (TypeError, ValueError):
            continue
        if d and close > 0:
            points.append(PricePoint(d=d, close=close))
    if len(points) < 2:
        return None
    points.sort(key=lambda p: p.d)
    return PriceHistory(ticker=ticker, points=points, source="Sina")


# --------------------------------------------------------------------------- #
# Eastmoney (needs the security id: 105/106/107 = NASDAQ/NYSE/AMEX)
# --------------------------------------------------------------------------- #
_EM_MARKETS = ("105", "106", "107")


def _eastmoney(ticker: str) -> PriceHistory | None:
    for market in _EM_MARKETS:
        for host in ("https://push2his.eastmoney.com", "https://63.push2his.eastmoney.com"):
            try:
                data = fetch(
                    f"{host}/api/qt/stock/kline/get",
                    params={
                        "secid": f"{market}.{ticker}",
                        "klt": "101",       # daily
                        "fqt": "1",         # forward-adjusted
                        "beg": "19900101",
                        "end": "20500101",
                        "fields1": "f1,f2,f3,f4,f5,f6",
                        "fields2": "f51,f52,f53,f54,f55,f56",
                    },
                    headers={"Referer": "https://quote.eastmoney.com/"},
                    namespace="price_em",
                    ttl=6 * 3600,
                    expect_json=True,
                    impersonate=True,
                    retries=2,
                )
            except FetchError:
                continue
            rows = (data or {}).get("data", {}).get("klines") or []
            points: list[PricePoint] = []
            for line in rows:
                parts = str(line).split(",")
                if len(parts) < 3:
                    continue
                d = _parse_date(parts[0])
                try:
                    close = float(parts[2])   # f53 = close
                except ValueError:
                    continue
                if d and close > 0:
                    points.append(PricePoint(d=d, close=close))
            if len(points) >= 2:
                points.sort(key=lambda p: p.d)
                return PriceHistory(ticker=ticker, points=points, source="Eastmoney")
    return None


# --------------------------------------------------------------------------- #
# akshare facade
# --------------------------------------------------------------------------- #
def _akshare_sina(ticker: str) -> PriceHistory | None:
    """The akshare Sina daily endpoint retains full split-adjusted US history.

    It forwards Sina's convention, which adjusts for splits but not dividends,
    so the result is reported as unadjusted.
    """
    try:
        from app.providers.akshare_us import get_daily
        df = get_daily(ticker)
    except Exception as exc:
        log.debug("akshare Sina daily %s failed: %s", ticker, exc)
        return None
    points: list[PricePoint] = []
    for _, row in df.iterrows():
        raw_date = row.get("date")
        d = raw_date.date() if hasattr(raw_date, "date") else _parse_date(str(raw_date))
        try:
            close = float(row.get("close"))
        except (TypeError, ValueError):
            continue
        if d and close > 0:
            points.append(PricePoint(d=d, close=close))
    if len(points) < 2:
        return None
    points.sort(key=lambda p: p.d)
    return PriceHistory(ticker=ticker, points=points, source="akshare/Sina",
                        used_adjusted=False)


def _akshare(ticker: str) -> PriceHistory | None:
    try:
        import akshare as ak
    except ImportError:
        return None
    for market in _EM_MARKETS:
        try:
            df = ak.stock_us_hist(
                symbol=f"{market}.{ticker}",
                period="daily",
                start_date="19900101",
                end_date=date.today().strftime("%Y%m%d"),
                adjust="qfq",
            )
        except Exception as exc:  # akshare raises a wide variety of errors
            log.debug("akshare hist %s.%s failed: %s", market, ticker, exc)
            continue
        if df is None or df.empty:
            continue
        points: list[PricePoint] = []
        for _, row in df.iterrows():
            d = _parse_date(str(row.get("日期")))
            try:
                close = float(row.get("收盘"))
            except (TypeError, ValueError):
                continue
            if d and close > 0:
                points.append(PricePoint(d=d, close=close))
        if len(points) >= 2:
            points.sort(key=lambda p: p.d)
            return PriceHistory(ticker=ticker, points=points, source="akshare/Eastmoney")
    return None


# --------------------------------------------------------------------------- #
# Yahoo (only useful behind a proxy / overseas host)
# --------------------------------------------------------------------------- #
def _yahoo(ticker: str) -> PriceHistory | None:
    for host in ("query1", "query2"):
        try:
            data = fetch(
                YAHOO_CHART.format(symbol=ticker).replace("query1", host),
                params={
                    "range": YAHOO_RANGE,
                    "interval": "1d",
                    "includeAdjustedClose": "true",
                    # Ask for the corporate actions explicitly: without them a
                    # split shows up as a one-day -90% "return".
                    "events": "div,splits",
                },
                namespace="price_yahoo",
                ttl=6 * 3600,
                expect_json=True,
                impersonate=True,
                retries=2,
            )
        except FetchError as exc:
            log.debug("yahoo chart %s failed: %s", ticker, exc)
            continue
        try:
            result = data["chart"]["result"][0]
            stamps = result["timestamp"]
            ind = result["indicators"]
            adjclose = (ind.get("adjclose") or [{}])[0].get("adjclose")
            raw = ind["quote"][0]["close"]
        except (KeyError, IndexError, TypeError):
            continue
        # Prefer the adjusted series. Only fall back to raw closes when Yahoo
        # returned no adjusted column at all, and say so via `used_adjusted`.
        adjusted = bool(adjclose) and any(c is not None for c in adjclose)
        closes = adjclose if adjusted else raw
        points: list[PricePoint] = []
        for ts, close in zip(stamps, closes):
            if close is None:
                continue
            d = datetime.utcfromtimestamp(ts).date()
            if close > 0:
                points.append(PricePoint(d=d, close=float(close)))
        if len(points) >= 2:
            points.sort(key=lambda p: p.d)
            return PriceHistory(
                ticker=ticker,
                currency=(result.get("meta") or {}).get("currency", "USD"),
                points=points,
                source="Yahoo",
                used_adjusted=adjusted,
            )
    return None


# --------------------------------------------------------------------------- #
# provider preference order (Sina first: it is the most widely reachable)
# --------------------------------------------------------------------------- #
_PIPELINE = [_sina, _akshare_sina, _akshare, _eastmoney, _yahoo]

# Which price convention each provider actually reports. `True` means the series
# is a total-return series (dividends and splits applied). Sina is split-adjusted
# only; the akshare-endorsed series is treated as unadjusted because its
# upstream forwards Sina's convention and cannot be verified as adjusted.
_ADJUSTED_PROVIDERS = frozenset({"_yahoo"})


def _pipeline_for(basis: PriceBasis | None, allow_yahoo: bool) -> list:
    """Providers that can honour `basis`, in preference order."""
    out = []
    for provider in _PIPELINE:
        name = provider.__name__
        if name == "_yahoo" and not allow_yahoo:
            continue
        if basis is not None and basis.adjusted and name not in _ADJUSTED_PROVIDERS:
            continue
        out.append(provider)
    return out


def _matches_basis(result: PriceHistory, basis: PriceBasis | None) -> bool:
    """Whether a provider's answer honours the run's price convention."""
    if basis is None:
        return True
    return bool(result.used_adjusted) == basis.adjusted


def get_price_history(
    ticker: str,
    *,
    allow_yahoo: bool = False,
    basis: PriceBasis | None = None,
) -> PriceHistory:
    """Resolve daily history for `ticker`, trying each provider in order.

    When `basis.adjusted` is set only providers that adjust for dividends are
    tried, so the returned series is directly comparable with a benchmark that
    was resolved on the same basis.
    """
    ticker = ticker.upper().strip()
    errors: list[str] = []
    partial: PriceHistory | None = None
    candidates = _pipeline_for(basis, allow_yahoo)
    if not candidates:
        raise FetchError(
            f"no provider can supply the required price basis "
            f"({'adjusted' if basis and basis.adjusted else 'any'}) for {ticker}"
        )
    for provider in candidates:
        try:
            result = provider(ticker)
        except Exception as exc:
            errors.append(f"{provider.__name__}: {exc}")
            continue
        if result and len(result.points) >= 2 and _matches_basis(result, basis):
            # Keep ~6 years: enough for 5-year CAGRs plus the 1-year window.
            result.points = result.points[-(HISTORY_DAYS):]
            result.points.sort(key=lambda p: p.d)
            span = (result.points[-1].d - result.points[0].d).days
            freshness = (date.today() - result.points[-1].d).days
            # A short or stale first response must not hide the later 3M/52W
            # series. Keep it only as a last-resort partial history.
            if 0 <= freshness <= 10 and span >= 365 and len(result.points[-365:]) >= 180:
                return result
            if partial is None or (result.points[-1].d, span) > (
                partial.points[-1].d,
                (partial.points[-1].d - partial.points[0].d).days,
            ):
                partial = result
            errors.append(f"{provider.__name__}: short/stale ({span}d, {freshness}d old)")
            continue
        if result:
            # Right length, wrong price convention: refuse it rather than
            # silently break the benchmark comparison.
            errors.append(
                f"{provider.__name__}: price basis mismatch "
                f"(wanted adjusted={basis.adjusted if basis else 'any'}, got {result.used_adjusted})"
            )
            continue
        errors.append(f"{provider.__name__}: no data")
    if partial is not None:
        return partial
    raise FetchError(f"no price history for {ticker} ({'; '.join(errors[:3])})")
