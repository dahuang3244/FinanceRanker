"""akshare-backed US-stock data access (US equities only).

Only interfaces that were verified reachable are wired in. From this network:

    stock_us_daily                              OK   (Sina, full daily history)
    stock_financial_us_report_em                OK   (Eastmoney datacenter, 3 statements)
    stock_financial_us_analysis_indicator_em    OK   (Eastmoney datacenter, 49 ratios)
    stock_us_spot_em / stock_us_hist / *_min_em  BLOCKED (Eastmoney push2 load-balancers)
    stock_individual_basic_info_us_xq           BLOCKED (Xueqiu needs xq_a_token)
    stock_us_valuation_baidu                    BLOCKED (non-JSON response)

Two notes that matter for correctness:

* Statement values arrive as *strings* in `AMOUNT`, so EPS keeps its decimals
  (MSFT FY2026 diluted EPS is "17.95"). Coercing through a rounded float first
  would yield 18 and silently break the non-GAAP bridge.
* `REPORT` (e.g. "2025/FY") is a display label and is off by one against the
  real period; `REPORT_DATE` is the fiscal period end and is the only safe key.
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from app import cache
from app.http import FetchError

log = logging.getLogger(__name__)

STATEMENTS = ("综合损益表", "现金流量表", "资产负债表")
CACHE_NS = "akshare_us"
PRICE_TTL = 6 * 3600
FIN_TTL = 12 * 3600
LIST_TTL = 24 * 3600


class AkshareUnavailable(FetchError):
    """Raised when akshare cannot serve a request in this environment."""


def _ak():
    try:
        import akshare as ak  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise AkshareUnavailable("akshare is not installed") from exc
    return ak


def _to_float(value) -> float | None:
    """Coerce a raw cell to float, preserving precision and dropping NaN.

    akshare stores `AMOUNT` as strings; `float("17.95")` keeps the decimals that
    a `pd.to_numeric` round-trip on a finance-formatted column might not.
    """
    if value is None:
        return None
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return result if result == result else None  # drop NaN


def _fiscal_year(end: date) -> str:
    return f"FY{end.year}"


# --------------------------------------------------------------------------- #
# price history
# --------------------------------------------------------------------------- #
def get_daily(ticker: str) -> pd.DataFrame:
    """Full daily history from Sina. Cached because it is a large download."""
    ticker = ticker.upper().strip()
    cached = cache.get(CACHE_NS, f"daily:{ticker}", PRICE_TTL)
    if cached is not None:
        frame = pd.DataFrame(cached)
        if not frame.empty and "date" in frame:
            frame["date"] = pd.to_datetime(frame["date"])
        return frame

    ak = _ak()
    try:
        frame = ak.stock_us_daily(symbol=ticker, adjust="qfq")
    except Exception as exc:
        raise AkshareUnavailable(f"stock_us_daily({ticker}) failed: {exc}") from exc

    if frame is None or frame.empty:
        raise AkshareUnavailable(f"stock_us_daily({ticker}) returned no rows")

    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    payload = frame.assign(date=frame["date"].dt.strftime("%Y-%m-%d")).to_dict("records")
    cache.put(CACHE_NS, f"daily:{ticker}", payload)
    return frame


# --------------------------------------------------------------------------- #
# financial statements
# --------------------------------------------------------------------------- #
def get_statement(ticker: str, statement: str) -> pd.DataFrame:
    """One annual statement. `statement` is one of `STATEMENTS`."""
    ticker = ticker.upper().strip()
    key = f"stmt:{ticker}:{statement}"
    cached = cache.get(CACHE_NS, key, FIN_TTL)
    if cached is not None:
        frame = pd.DataFrame(cached)
        if not frame.empty:
            frame["REPORT_DATE"] = pd.to_datetime(frame["REPORT_DATE"])
        return frame

    ak = _ak()
    try:
        frame = ak.stock_financial_us_report_em(
            stock=ticker, symbol=statement, indicator="年报"
        )
    except Exception as exc:
        raise AkshareUnavailable(
            f"stock_financial_us_report_em({ticker}, {statement}) failed: {exc}"
        ) from exc

    if frame is None or frame.empty:
        raise AkshareUnavailable(f"no {statement} for {ticker}")

    frame = frame.copy()
    frame["REPORT_DATE"] = pd.to_datetime(frame["REPORT_DATE"])
    payload = frame.assign(
        REPORT_DATE=frame["REPORT_DATE"].dt.strftime("%Y-%m-%d")
    ).to_dict("records")
    cache.put(CACHE_NS, key, payload)
    return frame


def statement_periods(frame: pd.DataFrame, limit: int | None = None) -> list[date]:
    """Distinct fiscal period ends, newest first."""
    if frame is None or frame.empty or "REPORT_DATE" not in frame:
        return []
    ends = sorted({ts.date() for ts in pd.to_datetime(frame["REPORT_DATE"])}, reverse=True)
    return ends[:limit] if limit else ends


def statement_series(frame: pd.DataFrame, item: str) -> dict[date, float | None]:
    """Map fiscal period end -> value for one `ITEM_NAME`."""
    if frame is None or frame.empty:
        return {}
    subset = frame[frame["ITEM_NAME"].astype(str).str.strip() == item]
    out: dict[date, float | None] = {}
    for _, row in subset.iterrows():
        out[pd.to_datetime(row["REPORT_DATE"]).date()] = _to_float(row.get("AMOUNT"))
    return out


def statement_items(frame: pd.DataFrame, limit: int = 60) -> list[str]:
    """Available line items, ordered by first appearance."""
    if frame is None or frame.empty:
        return []
    seen: list[str] = []
    for item in frame["ITEM_NAME"].astype(str):
        text = item.strip()
        if text and text not in seen and text != "非运算项目":
            seen.append(text)
    return seen[:limit]


# --------------------------------------------------------------------------- #
# analysis indicators (pre-computed ratios)
# --------------------------------------------------------------------------- #
def get_analysis(ticker: str) -> pd.DataFrame:
    ticker = ticker.upper().strip()
    key = f"analysis:{ticker}"
    cached = cache.get(CACHE_NS, key, FIN_TTL)
    if cached is not None:
        frame = pd.DataFrame(cached)
        if not frame.empty:
            frame["REPORT_DATE"] = pd.to_datetime(frame["REPORT_DATE"])
        return frame

    ak = _ak()
    try:
        frame = ak.stock_financial_us_analysis_indicator_em(symbol=ticker, indicator="年报")
    except Exception as exc:
        raise AkshareUnavailable(
            f"stock_financial_us_analysis_indicator_em({ticker}) failed: {exc}"
        ) from exc

    if frame is None or frame.empty:
        raise AkshareUnavailable(f"no analysis indicators for {ticker}")

    frame = frame.copy()
    frame["REPORT_DATE"] = pd.to_datetime(frame["REPORT_DATE"])
    payload = frame.assign(
        REPORT_DATE=frame["REPORT_DATE"].dt.strftime("%Y-%m-%d")
    ).to_dict("records")
    cache.put(CACHE_NS, key, payload)
    return frame


def analysis_series(frame: pd.DataFrame, column: str) -> dict[date, float | None]:
    if frame is None or frame.empty or column not in frame.columns:
        return {}
    out: dict[date, float | None] = {}
    for _, row in frame.iterrows():
        out[pd.to_datetime(row["REPORT_DATE"]).date()] = _to_float(row.get(column))
    return out


def analysis_currency(frame: pd.DataFrame) -> str:
    if frame is None or frame.empty or "CURRENCY" not in frame.columns:
        return ""
    values = frame["CURRENCY"].dropna()
    return str(values.iloc[0]) if not values.empty else ""


# --------------------------------------------------------------------------- #
# instance: fetch everything once per ticker
# --------------------------------------------------------------------------- #
class TickerData:
    """All akshare frames for one ticker, fetched lazily and cached."""

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker.upper().strip()
        self._daily: pd.DataFrame | None = None
        self._statements: dict[str, pd.DataFrame] = {}
        self._analysis: pd.DataFrame | None = None
        self.sources: dict[str, str] = {}
        self.warnings: list[str] = []

    @property
    def daily(self) -> pd.DataFrame:
        if self._daily is None:
            self._daily = get_daily(self.ticker)
            self.sources["日线行情"] = "akshare.stock_us_daily（新浪财经）"
        return self._daily

    def statement(self, name: str) -> pd.DataFrame:
        if name not in self._statements:
            try:
                self._statements[name] = get_statement(self.ticker, name)
                self.sources[name] = "akshare.stock_financial_us_report_em（东方财富）"
            except AkshareUnavailable as exc:
                self.warnings.append(str(exc))
                self._statements[name] = pd.DataFrame()
        return self._statements[name]

    @property
    def income(self) -> pd.DataFrame:
        return self.statement("综合损益表")

    @property
    def cashflow(self) -> pd.DataFrame:
        return self.statement("现金流量表")

    @property
    def balance(self) -> pd.DataFrame:
        return self.statement("资产负债表")

    @property
    def analysis(self) -> pd.DataFrame:
        if self._analysis is None:
            try:
                self._analysis = get_analysis(self.ticker)
                self.sources["分析指标"] = (
                    "akshare.stock_financial_us_analysis_indicator_em（东方财富）"
                )
            except AkshareUnavailable as exc:
                self.warnings.append(str(exc))
                self._analysis = pd.DataFrame()
        return self._analysis

    def fiscal_years(self, limit: int = 6) -> list[date]:
        for frame in (self.income, self.cashflow, self.balance):
            periods = statement_periods(frame, limit)
            if periods:
                return periods
        return []

    def currency(self) -> str:
        text = analysis_currency(self.analysis)
        return {"美元": "USD", "USD": "USD"}.get(text, text or "USD")

    def close(self) -> None:
        for frame in (self._daily, self._analysis, *self._statements.values()):
            del frame
