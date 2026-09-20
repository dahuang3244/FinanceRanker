"""Regression coverage for foreign ADR and public statement fallbacks."""

from datetime import date
from math import isclose
from unittest.mock import patch

from app.engine.metrics import compute_row
from app.models import FactSeries, Fundamentals, PriceHistory, PricePoint, Quote
from app.providers.adr import normalize_tsm
from app.providers.fundamentals import get_sec_fundamentals
from app.providers.yahoo_fundamentals import fill_missing, _series


def _annual(tag, value, year, unit="TWD"):
    return {"units": {unit: [{"form": "20-F", "fp": "FY", "start": f"{year}-01-01",
                              "end": f"{year}-12-31", "filed": f"{year+1}-04-01", "val": value}]}}


def test_ifrs_twd_values_load_then_tsm_ads_normalizes() -> None:
    fy = date(2025, 12, 31)
    payload = {"entityName": "Taiwan Semiconductor Manufacturing Co., Ltd.", "facts": {
        "ifrs-full": {
            "RevenueFromContractsWithCustomers": _annual("revenue", 3_000_000_000_000, 2025),
            "ProfitLossFromOperatingActivities": _annual("op", 1_200_000_000_000, 2025),
            "ProfitLoss": _annual("net", 900_000_000_000, 2025),
            "DilutedEarningsLossPerShare": _annual("eps", 35, 2025, "TWD/shares"),
            "WeightedAverageNumberOfOrdinarySharesOutstandingDiluted": _annual("shares", 26e9, 2025, "shares"),
            "Assets": {"units": {"TWD": [{"form": "20-F", "end": "2025-12-31", "val": 7e12}]}},
            "Equity": {"units": {"TWD": [{"form": "20-F", "end": "2025-12-31", "val": 5e12}]}},
        }, "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"form": "20-F", "end": "2025-12-31", "val": 26e9}
        ]}}},
    }}
    with patch("app.providers.fundamentals.lookup_cik", return_value=1046179), patch(
        "app.providers.fundamentals.fetch", return_value=payload
    ):
        fund = get_sec_fundamentals("TSM")
    assert fund is not None and fund.currency == "TWD"
    assert fund.revenue.latest() == 3e12
    assert fund.eps_diluted.unit == "TWD/shares"
    assert fund.equity.latest() == 5e12
    quote = Quote(ticker="TSM", price=400, currency="USD")
    usd = normalize_tsm(fund, quote, 0.032, "Yahoo TWDUSD=X")
    assert usd.shares_outstanding == 5.2e9
    assert isclose(usd.eps_diluted.latest(), 5.6)
    assert fund.shares_outstanding == 26e9  # the SEC input is unchanged
    history = PriceHistory(ticker="TSM", points=[
        PricePoint(d=date(2025, 9, 19), close=300),
        PricePoint(d=date(2026, 9, 19), close=400),
    ])
    row = compute_row("TSM", history=history, quote=quote, fund=usd)
    assert row.market_cap == 400 * 5.2e9
    assert row.operating_margin == 0.4
    assert row.price_to_sales is not None
    assert "spot" in row.notes and "ADS" in row.notes


def test_yahoo_gap_fill_only_matching_fiscal_year_and_currency() -> None:
    sec = Fundamentals(ticker="NVDA", currency="USD", fiscal_end=date(2026, 1, 31),
                       revenue=FactSeries(tag="SEC", unit="USD", points=[(date(2026, 1, 31), 100)]))
    yahoo = Fundamentals(ticker="NVDA", currency="USD", source_url="https://finance.yahoo.com/",
                         capex=FactSeries(tag="Yahoo", unit="USD", points=[(date(2026, 1, 31), 20)]),
                         revenue=FactSeries(tag="Yahoo", unit="USD", points=[(date(2026, 1, 31), 999)]))
    merged = fill_missing(sec, yahoo)
    assert merged.capex.latest() == 20
    assert merged.revenue.latest() == 100
    yahoo.capex.points = [(date(2025, 1, 31), 20)]
    assert fill_missing(sec, yahoo).capex is None
    yahoo.currency = "TWD"
    assert fill_missing(sec, yahoo).capex is None


def test_yahoo_annual_points_are_dated_and_capex_has_positive_magnitude() -> None:
    result = _series({"annualCapitalExpenditure": [
        {"asOfDate": "2025-12-31", "reportedValue": {"raw": -35}},
        {"asOfDate": "2024-12-31", "reportedValue": {"raw": -25}},
    ]}, ("CapitalExpenditure",), "capex", "USD")
    assert result.points == [(date(2025, 12, 31), 35), (date(2024, 12, 31), 25)]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
