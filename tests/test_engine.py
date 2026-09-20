"""Regression tests for the metric engine and exporters.

These run fully offline using synthetic data, so they are fast and deterministic.
The expected values are taken from `Free_Public_Data_Tech_Ranker.xlsx`
(MSFT row of `Data Cache`), which the engine reproduces exactly.

Run:  PYTHONPATH=. python -m pytest tests -q
      PYTHONPATH=. python tests/test_engine.py     # no pytest required
"""

from __future__ import annotations

from datetime import date, timedelta

from app.engine import metrics, scoring
from app.engine.market import beta, daily_returns, drawdown_from_high, return_over_window
from app.models import FactSeries, Fundamentals, MetricRow, PriceHistory, PricePoint, Quote


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def series(tag: str, values: list[tuple[str, float]]) -> FactSeries:
    return FactSeries(
        tag=tag, unit="USD", points=[(date.fromisoformat(d), v) for d, v in values]
    )


def msft_like() -> Fundamentals:
    """Synthetic inputs reproducing the workbook's MSFT row."""
    return Fundamentals(
        ticker="MSFT",
        entity_name="MICROSOFT CORPORATION",
        currency="USD",
        source="SEC XBRL (US-GAAP)",
        fiscal_end=date(2026, 6, 30),
        revenue=series("Revenues", [
            ("2026-06-30", 331_839_000_000), ("2025-06-30", 281_724_000_000),
            ("2024-06-30", 245_122_000_000), ("2023-06-30", 211_915_000_000),
            ("2022-06-30", 198_270_000_000), ("2021-06-30", 168_088_000_000),
        ]),
        gross_profit=series("GrossProfit", [("2026-06-30", 225_465_000_000)]),
        cost_of_revenue=series("CostOfRevenue", [("2026-06-30", 106_374_000_000)]),
        operating_income=series("OperatingIncomeLoss", [("2026-06-30", 155_237_000_000)]),
        net_income=series("NetIncomeLoss", [("2026-06-30", 133_749_000_000)]),
        equity=series("StockholdersEquity", [("2026-06-30", 442_387_000_000)]),
        assets=series("Assets", [("2026-06-30", 758_376_000_000)]),
        cash=series("Cash", [("2026-06-30", 20_935_000_000)]),
        operating_cash_flow=series("OCF", [("2026-06-30", 182_935_000_000)]),
        capex=series("Capex", [("2026-06-30", 115_948_000_000)]),
        eps_diluted=series("EPS", [
            ("2026-06-30", 17.95), ("2025-06-30", 13.64), ("2024-06-30", 11.80),
            ("2023-06-30", 9.68), ("2022-06-30", 9.65), ("2021-06-30", 8.05),
        ]),
        shares_diluted=series("Shares", [("2026-06-30", 7_453_000_000)]),
        shares_outstanding=7_425_545_491,
        shares_basis="Latest common shares (SEC DEI)",
        sbc=series("SBC", [("2026-06-30", 12_405_000_000)]),
        amortization=series("Amort", [("2026-06-30", 4_700_000_000)]),
        depreciation_amortization=series("DA", [("2026-06-30", 34_300_000_000)]),
        tax_provision=series("Tax", [("2026-06-30", 25_000_000_000)]),
        pretax_income=series("Pretax", [("2026-06-30", 128_900_000_000)]),
        debt_long=series("DebtLong", [("2026-06-30", 40_294_000_000)]),
    )


def flat_history(days: int = 400, start: float = 300.0, drift: float = 0.001) -> PriceHistory:
    points = []
    price = start
    day = date(2025, 1, 1)
    for i in range(days):
        price *= (1 + drift)
        points.append(PricePoint(d=day + timedelta(days=i), close=round(price, 4)))
    return PriceHistory(ticker="TEST", points=points, source="test")


def msft_quote() -> Quote:
    return Quote(
        ticker="MSFT", name="Microsoft Corporation", currency="USD", price=493.78,
        market_cap=3_665_400_000_000, shares_outstanding=7_425_545_491, source="test",
    )


# --------------------------------------------------------------------------- #
# workbook parity
# --------------------------------------------------------------------------- #
def test_workbook_parity_msft():
    """Core ratios must match Data Cache row 7 to 12 decimal places."""
    row = metrics.compute_row(
        "MSFT", history=flat_history(), quote=msft_quote(), fund=msft_like()
    )
    assert row.revenue_fy0 == 331_839_000_000
    assert row.gaap_eps == 17.95
    assert row.total_assets_fy0 == 758_376_000_000
    assert row.equity_fy0 == 442_387_000_000
    # Exact workbook values.
    assert round(row.operating_margin, 12) == 0.467808184089
    assert round(row.net_margin, 12) == 0.403053890592
    assert round(row.roe, 12) == 0.302334833528
    assert row.status == "Refreshed"


def test_gross_margin_derived_from_cost_of_revenue():
    fund = msft_like()
    fund.gross_profit = None  # force the revenue - cost fallback
    row = metrics.compute_row("MSFT", history=flat_history(), quote=msft_quote(), fund=fund)
    assert row.gross_profit_fy0 is None
    expected = (331_839_000_000 - 106_374_000_000) / 331_839_000_000
    assert round(row.gross_margin, 12) == round(expected, 12)


def test_growth_uses_like_for_like_periods():
    row = metrics.compute_row(
        "MSFT", history=flat_history(), quote=msft_quote(), fund=msft_like()
    )
    assert round(row.revenue_growth_yoy, 10) == round(331_839 / 281_724 - 1, 10)
    assert round(row.revenue_fy_minus_1, 0) == 281_724_000_000
    assert round(row.revenue_fy_minus_5, 0) == 168_088_000_000
    assert row.eps_cagr_5y is not None


# --------------------------------------------------------------------------- #
# cross-currency guard (bug #3)
# --------------------------------------------------------------------------- #
def test_cross_currency_guard_withholds_price_multiples():
    fund = msft_like()
    fund.currency = "TWD"                       # e.g. TSM: TWD filing, USD ADR
    quote = Quote(ticker="TSM", currency="USD", price=336.13, source="test")
    row = metrics.compute_row("TSM", history=flat_history(), quote=quote, fund=fund)

    # Price-derived figures must be withheld ...
    assert row.market_cap is None
    assert row.price_to_sales is None
    assert row.price_to_book is None
    assert row.forward_pe is None
    assert row.ev_to_ebitda is None
    # ... while currency-free ratios and raw filing values survive.
    assert row.operating_margin is not None
    assert row.revenue_fy0 == 331_839_000_000
    assert row.status.startswith("Partial filing data")
    assert "TWD" in row.notes and "USD" in row.notes
    # The row keeps the traded currency for prices, but records what the filings
    # are quoted in so the UI can label the filing-derived figures.
    assert row.currency == "USD"
    assert row.filing_currency == "TWD"


def test_same_currency_is_unaffected():
    row = metrics.compute_row(
        "MSFT", history=flat_history(), quote=msft_quote(), fund=msft_like()
    )
    assert row.market_cap is not None
    assert row.price_to_sales is not None
    assert "differs" not in row.notes
    assert row.filing_currency == row.currency == "USD"


# --------------------------------------------------------------------------- #
# engine primitives
# --------------------------------------------------------------------------- #
def test_safe_div_and_cagr():
    assert metrics.safe_div(10, 2) == 5
    assert metrics.safe_div(10, 0) is None
    assert metrics.safe_div(None, 2) is None
    assert metrics.cagr(200, 100, 1) == 1.0
    assert round(metrics.cagr(121, 100, 2), 6) == 0.1
    assert metrics.cagr(-5, 100, 2) is None
    assert metrics.growth(0, 100) is None


def test_effective_tax_rate_bounds():
    fund = msft_like()
    assert round(metrics.effective_tax_rate(fund), 6) == round(25_000 / 128_900, 6)
    fund.tax_provision = series("Tax", [("2026-06-30", 999_000_000_000)])
    assert metrics.effective_tax_rate(fund) == metrics.MAX_TAX_RATE
    fund.tax_provision = None
    assert metrics.effective_tax_rate(fund) == metrics.DEFAULT_TAX_RATE


def test_series_at_offset_matches_calendar_year():
    s = series("R", [("2026-06-30", 100.0), ("2025-06-30", 80.0), ("2024-06-30", 60.0)])
    assert metrics._series_at_offset(s, 1) == 80.0
    assert metrics._series_at_offset(s, 2) == 60.0
    assert metrics._series_at_offset(s, 3) is None
    # A series whose dates do not line up within tolerance yields nothing.
    s2 = series("R", [("2026-06-30", 100.0), ("2023-01-01", 50.0)])
    assert metrics._series_at_offset(s2, 1) is None


def test_market_helpers():
    h = flat_history(days=60, start=100.0, drift=0.0)
    r = daily_returns(h.points)
    assert len(r) == 59
    assert all(abs(v) < 1e-12 for v in r.values())
    assert abs(drawdown_from_high(90, 100) - (-0.1)) < 1e-12
    assert drawdown_from_high(None, 100) is None
    assert drawdown_from_high(90, 0) is None
    assert return_over_window(h, 365) == 0.0
    assert beta(h, h) is None  # zero variance has no defined slope


def test_beta_of_identical_series_is_one():
    points = []
    price = 100.0
    day = date(2025, 1, 1)
    for i in range(80):
        price *= 1.01 if i % 3 else 0.99
        points.append(PricePoint(d=day + timedelta(days=i), close=price))
    hist = PriceHistory(ticker="A", points=points)
    result = beta(hist, hist)
    assert result is not None and abs(result - 1.0) < 1e-9


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #
def test_percentile_scoring_bounds_and_direction():
    population = [1.0, 2.0, 3.0, 4.0]
    higher = scoring._percentile_score(4.0, population, True)
    lower = scoring._percentile_score(4.0, population, False)
    assert higher == 10.0          # best when higher is better
    assert lower == 1.0            # worst when lower is better
    assert scoring._percentile_score(1.0, population, True) == 1.0
    assert scoring._percentile_score(2.0, [1.0], True) is None


def test_score_peers_assigns_ranks_and_eligibility():
    rows = []
    for i, name in enumerate(["AAA", "BBB", "CCC"]):
        row = MetricRow(ticker=name)
        # Populate every scoring metric so all rows are eligible.
        for attr, _, _ in scoring.METRICS:
            setattr(row, attr, float(i + 1))
        rows.append(row)
    scored = scoring.score_peers(rows)
    eligible = [r for r in scored if r.rank_eligible]
    assert len(eligible) == 3
    assert [r.ticker for r in scored if r.rank == 1] == ["CCC"]  # highest values win
    assert all(r.data_coverage == 21 for r in scored)
    assert all(r.profile for r in scored)


def test_score_peers_marks_sparse_rows_ineligible():
    good = MetricRow(ticker="GOOD")
    for attr, _, _ in scoring.METRICS:
        setattr(good, attr, 5.0)
    sparse = MetricRow(ticker="SPARSE")
    sparse.revenue_growth_yoy = 1.0
    sparse.gross_margin = 0.5
    scored = scoring.score_peers([good, sparse])
    sparse_out = next(r for r in scored if r.ticker == "SPARSE")
    assert sparse_out.rank_eligible is False
    assert sparse_out.rank is None
    assert sparse_out.score_overall is None


# --------------------------------------------------------------------------- #
# exporters
# --------------------------------------------------------------------------- #
def test_csv_export_roundtrip_and_no_scientific_notation():
    from app.exporters.csv_export import COLUMNS, rows_to_csv

    row = metrics.compute_row(
        "MSFT", history=flat_history(), quote=msft_quote(), fund=msft_like()
    )
    text = rows_to_csv([row])
    lines = text.splitlines()
    # The exporter may emit `## section` banner rows, so locate the real header
    # and data row rather than assuming fixed offsets.
    header_idx = next(i for i, l in enumerate(lines) if "Ticker" in l)
    header = lines[header_idx]
    assert "Score overall" in header, "scoring column missing from the header"
    assert len(header.split(",")) == len(COLUMNS), (
        f"header has {len(header.split(','))} columns, catalogue declares {len(COLUMNS)}"
    )
    data = lines[header_idx + 1]
    assert "MSFT" in data
    # Large money values must be plain digits, never 4.15e+11.
    assert "e+11" not in data and "e+12" not in data


def test_excel_export_has_all_sheets(tmpdir=None):
    import tempfile
    from pathlib import Path

    import openpyxl

    from app.exporters.excel_export import write_xlsx

    row = metrics.compute_row(
        "MSFT", history=flat_history(), quote=msft_quote(), fund=msft_like()
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = write_xlsx([row], Path(tmp) / "out.xlsx")
        wb = openpyxl.load_workbook(path)
        assert wb.sheetnames == [
            "Ranking", "Stock Detail", "Peer Data", "Scoring",
            "Non-GAAP Bridge", "Data Cache", "Methodology", "Guidance",
        ]
        assert wb["Data Cache"].cell(6, 1).value == "MSFT"
        assert wb["Ranking"].cell(8, 2).value == "MSFT"


# --------------------------------------------------------------------------- #
def _main() -> int:
    """Run every test_* function without needing pytest."""
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
