"""The GAAP-to-adjusted bridge must show its working and never invent a line.

The product rule this pins down: a filer that does not tag an add-back must have
that line reported as unavailable, never as zero. Presenting an untagged line as
zero would make the app's adjusted EPS look company-endorsed when it is a
modelling choice.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engine import metrics
from app.models import FactSeries, Fundamentals, PriceHistory, PricePoint, Quote


def series(values: dict[str, float], unit: str = "USD") -> dict[str, FactSeries]:
    return {
        tag: FactSeries(tag=tag, unit=unit,
                        points=[(date(2025, 12, 31), value)])
        for tag, value in values.items()
    }


def history() -> PriceHistory:
    return PriceHistory(
        ticker="TEST",
        points=[PricePoint(d=date(2025, 1, 1) + __import__("datetime").timedelta(days=i),
                           close=100.0 + i) for i in range(400)],
        source="test",
    )


def build(**fields) -> Fundamentals:
    base = {
        "revenue": FactSeries(tag="R", unit="USD",
                              points=[(date(2025, 12, 31), 1000.0)]),
        "operating_income": FactSeries(tag="OI", unit="USD",
                                       points=[(date(2025, 12, 31), 200.0)]),
        "net_income": FactSeries(tag="NI", unit="USD",
                                 points=[(date(2025, 12, 31), 150.0)]),
        "eps_diluted": FactSeries(tag="EPS", unit="USD/shares",
                                  points=[(date(2025, 12, 31), 1.50)]),
        "tax_provision": FactSeries(tag="Tax", unit="USD",
                                    points=[(date(2025, 12, 31), 30.0)]),
        "pretax_income": FactSeries(tag="Pretax", unit="USD",
                                    points=[(date(2025, 12, 31), 180.0)]),
    }
    base.update(fields)
    return Fundamentals(ticker="TEST", entity_name="Test Co", currency="USD",
                        source="SEC XBRL (US-GAAP)", fiscal_end=date(2025, 12, 31),
                        shares_diluted=FactSeries(tag="Sh", unit="shares",
                                                  points=[(date(2025, 12, 31), 100.0)]),
                        shares_outstanding=100.0, **base)


def row_for(**fields):
    return metrics.compute_row(
        "TEST", history=history(), quote=Quote(ticker="TEST", currency="USD", price=100.0),
        fund=build(**fields),
    )


def test_bridge_starts_from_gaap_income_and_divides_by_shares():
    row = row_for(sbc=FactSeries(tag="SBC", unit="USD",
                                 points=[(date(2025, 12, 31), 20.0)]))
    recon = row.non_gaap
    assert recon is not None
    assert recon.gaap_net_income == 150.0
    # 150 + 20 SBC = 170, over 100 shares = 1.70
    assert recon.adjusted_net_income == 170.0
    assert abs(recon.adjusted_eps - 1.70) < 1e-9
    assert recon.gaap_eps == 1.50
    assert row.non_gaap_eps == recon.adjusted_eps


def test_gain_is_subtracted_and_charge_is_added_back():
    row = row_for(
        equity_securities_gain=FactSeries(
            tag="Gain", unit="USD", points=[(date(2025, 12, 31), 40.0)]),
        restructuring=FactSeries(
            tag="Restr", unit="USD", points=[(date(2025, 12, 31), 10.0)]),
    )
    recon = row.non_gaap
    # 150 - 40 gain + 10 restructuring = 120
    assert recon.adjusted_net_income == 120.0
    directions = {line.key: line.is_addback for line in recon.lines}
    assert directions["equity_securities_gain"] is False
    assert directions["restructuring"] is True


def test_an_untagged_line_is_reported_as_missing_not_as_zero():
    """The whole point: absent disclosure must not read as a zero add-back."""
    row = row_for(sbc=FactSeries(tag="SBC", unit="USD",
                                 points=[(date(2025, 12, 31), 20.0)]))
    recon = row.non_gaap
    # Restructuring and amortization were not supplied.
    assert "Restructuring charges" in recon.missing
    assert "Amortization of intangibles" in recon.missing
    # And no line object exists for them, so nothing claims a 0 was added.
    assert not [line for line in recon.lines if line.key == "restructuring"]
    assert recon.complete_years == 1  # only SBC of the three common lines


def test_no_addbacks_means_adjusted_equals_gaap_rather_than_blank():
    """A filer with nothing to adjust still reconciles; it is not a missing row."""
    row = row_for()
    recon = row.non_gaap
    assert recon is not None
    assert recon.adjusted_net_income == recon.gaap_net_income
    assert recon.adjusted_eps == recon.gaap_eps
    assert recon.lines == []
    assert len(recon.missing) == 3


def test_uplift_measures_how_much_of_adjusted_eps_is_adjustment():
    row = row_for(sbc=FactSeries(tag="SBC", unit="USD",
                                 points=[(date(2025, 12, 31), 50.0)]))
    # (150 + 50) / 100 = 2.00 adjusted against 1.50 reported -> +33.3%
    assert abs(row.gaap_to_adjusted_uplift - (2.00 / 1.50 - 1.0)) < 1e-9


def test_uplift_is_blank_when_gaap_eps_is_not_positive():
    """A loss-making filer has no meaningful uplift percentage."""
    fund = build()
    fund.net_income = FactSeries(tag="NI", unit="USD",
                                 points=[(date(2025, 12, 31), -150.0)])
    fund.eps_diluted = FactSeries(tag="EPS", unit="USD/shares",
                                  points=[(date(2025, 12, 31), -1.50)])
    row = metrics.compute_row("TEST", history=history(),
                              quote=Quote(ticker="TEST", currency="USD", price=100.0),
                              fund=fund)
    assert row.gaap_eps == -1.50
    assert row.gaap_to_adjusted_uplift is None


def test_both_headline_metrics_are_catalogued_and_unscored():
    from app.engine import scoring

    catalog = {m["attr"]: m for m in scoring.metric_catalog()}
    for attr in ("non_gaap_eps", "gaap_to_adjusted_uplift"):
        assert attr in catalog, attr
        assert catalog[attr]["scored"] is False, attr
        # Unscored metrics must explain themselves.
        assert catalog[attr]["note"], attr
        assert catalog[attr]["component"] == "growth", attr


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
