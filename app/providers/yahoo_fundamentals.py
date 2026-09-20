"""Best-effort public Yahoo annual statements, with explicit source provenance.

Yahoo's undocumented time-series API can fail or offer only four annual years.
Never infer a missing fifth year from fewer observations.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.http import FetchError, fetch
from app.models import FactSeries, Fundamentals


FIELDS = {
    "revenue": ("TotalRevenue",),
    "gross_profit": ("GrossProfit",),
    "cost_of_revenue": ("CostOfRevenue",),
    "operating_income": ("OperatingIncome",),
    "net_income": ("NetIncome",),
    "operating_cash_flow": ("OperatingCashFlow",),
    "capex": ("CapitalExpenditure", "PurchaseOfPPE"),
    "eps_diluted": ("DilutedEPS",),
    "shares_diluted": ("DilutedAverageShares",),
    "assets": ("TotalAssets",),
    "equity": ("StockholdersEquity",),
    "cash": ("CashAndCashEquivalents",),
    "debt_long": ("LongTermDebt",),
    "depreciation_amortization": ("DepreciationAndAmortizationInCashFlow",),
    "sbc": ("StockBasedCompensation",),
    "tax_provision": ("TaxProvision",),
    "pretax_income": ("PretaxIncome",),
}


def _series(node: dict, names: tuple[str, ...], field: str, currency: str) -> FactSeries | None:
    for name in names:
        values = (node.get("annual" + name) or [])
        observations: dict[date, float] = {}
        for entry in values:
            try:
                end = date.fromisoformat(entry["asOfDate"])
                value = float(entry["reportedValue"]["raw"])
            except (KeyError, ValueError, TypeError):
                continue
            observations[end] = abs(value) if field == "capex" else value
        if observations:
            unit = "shares" if field == "shares_diluted" else (
                f"{currency}/shares" if field == "eps_diluted" else currency
            )
            return FactSeries(
                tag=f"Yahoo:{name}", unit=unit,
                points=sorted(observations.items(), reverse=True),
            )
    return None


def get_yahoo_fundamentals(ticker: str, *, currency: str = "USD") -> Fundamentals | None:
    """Download Yahoo yearly facts; accepts partial statements for SEC gap-filling."""
    types = ["annual" + name for names in FIELDS.values() for name in names]
    period2 = int(datetime.now(timezone.utc).timestamp()) + 86400
    fund = Fundamentals(ticker=ticker, currency=currency, source="Yahoo annual statements")
    urls = []
    for host in ("query1", "query2"):
        collected = {}
        urls = []
        for start in range(0, len(types), 7):
            url = f"https://{host}.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{ticker}"
            try:
                data = fetch(
                    url,
                    params={"symbol": ticker, "type": ",".join(types[start:start + 7]),
                            "period1": 1451606400, "period2": period2},
                    namespace="yahoo_fundamentals", ttl=12 * 3600,
                    impersonate=True, expect_json=True, retries=1,
                )
                for node in ((data.get("timeseries") or {}).get("result") or []):
                    collected.update(node)
                urls.append(url)
            except (FetchError, AttributeError, TypeError):
                continue
        if collected:
            for field, names in FIELDS.items():
                setattr(fund, field, _series(collected, names, field, currency))
            if fund.revenue or fund.operating_income:
                fund.source_url = urls[0] if urls else None
                anchors = [s.latest_end() for s in (fund.revenue, fund.operating_income)
                           if s is not None and s.latest_end()]
                fund.fiscal_end = max(anchors) if anchors else None
                return fund
    return None


def fill_missing(sec: Fundamentals, yahoo: Fundamentals) -> Fundamentals:
    """Add only same-currency fields aligned to the SEC fiscal year."""
    if sec.currency != yahoo.currency or sec.fiscal_end is None:
        return sec
    result = sec.model_copy(deep=True)
    filled = []
    for field in FIELDS:
        if getattr(result, field) is not None:
            continue
        candidate = getattr(yahoo, field)
        if candidate is None or candidate.latest_end() is None:
            continue
        if abs((sec.fiscal_end - candidate.latest_end()).days) > 45:
            continue
        setattr(result, field, candidate)
        filled.append(field)
    if filled:
        result.notes.append(f"Yahoo annual gap fill: {', '.join(filled)}; {yahoo.source_url}")
    return result
