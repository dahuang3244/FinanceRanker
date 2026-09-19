"""Price-history providers (daily bars).

Provider order is deliberate. Sina's US daily endpoint is the most reliable from
networks where Yahoo is blocked and Eastmoney's `push2` load-balancers are
flaky; Eastmoney and Yahoo remain as fallbacks, and `akshare` is used as a
higher-level facade when it can reach its upstream.
"""

from __future__ import annotations

import logging
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
                params={"range": "2y", "interval": "1d", "includeAdjustedClose": "true"},
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
            closes = (ind.get("adjclose") or [{}])[0].get("adjclose") or ind["quote"][0]["close"]
        except (KeyError, IndexError, TypeError):
            continue
        points: list[PricePoint] = []
        for ts, close in zip(stamps, closes):
            if close is None:
                continue
            d = datetime.utcfromtimestamp(ts).date()
            if close > 0:
                points.append(PricePoint(d=d, close=float(close)))
        if len(points) >= 2:
            return PriceHistory(
                ticker=ticker,
                currency=(result.get("meta") or {}).get("currency", "USD"),
                points=points,
                source="Yahoo",
                used_adjusted=True,
            )
    return None


# --------------------------------------------------------------------------- #
_PIPELINE = [_sina, _akshare, _eastmoney, _yahoo]


def get_price_history(ticker: str, *, allow_yahoo: bool = False) -> PriceHistory:
    """Resolve daily history for `ticker`, trying each provider in order."""
    ticker = ticker.upper().strip()
    errors: list[str] = []
    for provider in _PIPELINE:
        if provider is _yahoo and not allow_yahoo:
            continue
        try:
            result = provider(ticker)
        except Exception as exc:
            errors.append(f"{provider.__name__}: {exc}")
            continue
        if result and len(result.points) >= 2:
            # Keep ~6 years: enough for 5-year CAGRs plus the 1-year window.
            result.points = result.points[-(6 * 366):]
            return result
        errors.append(f"{provider.__name__}: no data")
    raise FetchError(f"no price history for {ticker} ({'; '.join(errors[:3])})")
