"""EBITDA must not vanish when a filer stops tagging operating income.

Two independent bugs caused blank valuation columns, both found from the screens:

* Johnson & Johnson's `OperatingIncomeLoss` series ends in 2014. It tags neither
  `CostsAndExpenses` nor `OperatingExpenses`, so operating profit cannot be
  reconstructed — the fallback is pre-tax income plus D&A, labelled as such.
* The Yahoo timeSeries field for D&A was named `...InCashFlow`, which 404s. Every
  Yahoo-primary ticker therefore lost D&A and, with it, EBITDA, EV/EBITDA and
  net-debt/EBITDA. Eli Lilly is the case in point.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engine import metrics
from app.models import FactSeries, Fundamentals, PriceHistory, PricePoint, Quote


def series(tag: str, value: float, unit: str = "USD") -> FactSeries:
    return FactSeries(tag=tag, unit=unit, points=[(date(2025, 12, 31), value)])


def history() -> PriceHistory:
    import datetime as dt

    return PriceHistory(
        ticker="TEST",
        points=[PricePoint(d=date(2025, 1, 1) + dt.timedelta(days=i), close=100.0 + i)
                for i in range(400)],
        source="test",
    )


def base_fund(**overrides) -> Fundamentals:
    fields = dict(
        revenue=series("R", 1000.0),
        operating_income=series("OI", 200.0),
        net_income=series("NI", 150.0),
        eps_diluted=series("EPS", 1.50, "USD/shares"),
        tax_provision=series("Tax", 30.0),
        pretax_income=series("Pretax", 180.0),
        equity=series("EQ", 800.0),
        assets=series("A", 2000.0),
        cash=series("Cash", 100.0),
        debt_long=series("Debt", 300.0),
        depreciation_amortization=series("DA", 50.0),
    )
    fields.update(overrides)
    return Fundamentals(
        ticker="TEST", entity_name="Test", currency="USD", source="SEC XBRL (US-GAAP)",
        fiscal_end=date(2025, 12, 31), shares_outstanding=100.0, **fields,
    )


def build(fund: Fundamentals):
    return metrics.compute_row(
        "TEST", history=history(),
        quote=Quote(ticker="TEST", currency="USD", price=100.0), fund=fund,
    )


def test_ebitda_is_operating_income_plus_da_when_both_are_tagged():
    row = build(base_fund())
    assert row.ebitda_fy0 == 250.0          # 200 + 50
    assert row.ebitda_basis == "", "no substitution means no basis note"
    assert row.ev_to_ebitda is not None


def test_ebitda_falls_back_to_pretax_income_when_operating_income_is_absent():
    """The JNJ case: no operating income series at all."""
    fund = base_fund(operating_income=None)
    row = build(fund)
    # 180 pre-tax + 50 D&A
    assert row.ebitda_fy0 == 230.0
    assert row.ebitda_basis, "a substituted EBITDA must say so"
    assert "Pre-tax income" in row.ebitda_basis
    # And the multiple still computes, using that same figure.
    assert row.ev_to_ebitda is not None
    expected_ev = row.market_cap + 300.0 - 100.0
    assert abs(row.ev_to_ebitda - expected_ev / 230.0) < 1e-6


def test_ebitda_is_blank_when_neither_operating_nor_pretax_income_is_tagged():
    """A blank is correct here: there is nothing to measure."""
    row = build(base_fund(operating_income=None, pretax_income=None))
    assert row.ebitda_fy0 is None
    assert row.ev_to_ebitda is None


def test_ebitda_is_blank_without_depreciation():
    row = build(base_fund(depreciation_amortization=None))
    assert row.ebitda_fy0 is None
    assert row.ev_to_ebitda is None


def test_ev_ebitda_uses_the_rows_own_ebitda():
    """The multiple must agree with the EBITDA the screen shows.

    Recomputing it from operating income silently dropped filers whose EBITDA
    came from the fallback back to a blank multiple.
    """
    fund = base_fund(operating_income=None)
    row = build(fund)
    assert row.ebitda_fy0 is not None
    # Re-running the EV pass must not change the answer.
    before = row.ev_to_ebitda
    metrics.apply_ev_ebitda(row)
    assert row.ev_to_ebitda == before
    # A non-positive EBITDA yields no multiple rather than a negative one.
    row.ebitda_fy0 = -1.0
    row.ev_to_ebitda = None
    metrics.apply_ev_ebitda(row)
    assert row.ev_to_ebitda is None


def test_net_debt_to_ebitda_uses_the_same_ebitda():
    row = build(base_fund())
    expected = (300.0 - 100.0) / 250.0
    assert abs(row.net_debt_to_ebitda - expected) < 1e-9


def test_yahoo_da_field_is_not_the_404_name():
    """`DepreciationAndAmortizationInCashFlow` 404s; using it cost tickers EBITDA."""
    from app.providers import yahoo_fundamentals as yf

    names = yf.FIELDS["depreciation_amortization"]
    assert "DepreciationAndAmortizationInCashFlow" not in names, (
        "that Yahoo field name returns HTTP 404 and silently blanks EBITDA"
    )
    assert names[0] == "DepreciationAndAmortization", names


if __name__ == "__main__":
    import traceback

    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception:
                failed += 1
                print(f"FAIL  {name}")
                traceback.print_exc()
            else:
                passed += 1
                print(f"PASS  {name}")
    print(f"\n{passed}/{passed + failed} passed")
    raise SystemExit(1 if failed else 0)
