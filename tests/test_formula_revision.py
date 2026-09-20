"""Regression checks for mixed SEC tags, ADR fallback and investment scores."""

from datetime import date, timedelta
from unittest.mock import patch

from app.engine.metrics import compute_row
from app.engine.scoring import _percentile_score, score_peers
from app.models import FactSeries, Fundamentals, MetricRow, PriceHistory, PricePoint, Quote
from app.providers.fundamentals import _flow_series, _instant_series
from app.providers.fx import usd_per_twd


def _filing(year, value, instant=False):
    entry = {"end": f"{year}-12-31", "val": value, "form": "20-F",
             "filed": f"{year+1}-04-01", "fp": "FY"}
    if not instant:
        entry["start"] = f"{year}-01-01"
    return entry


def test_migrated_sec_tags_keep_prior_years():
    gaap = {
        "NewRevenue": {"units": {"TWD": [_filing(2025, 120)]}},
        "OldRevenue": {"units": {"TWD": [_filing(2024, 100), _filing(2020, 50)]}},
    }
    series = _flow_series(gaap, ["NewRevenue", "OldRevenue"], unit="TWD")
    assert [v for _, v in series.points] == [120, 100, 50]
    from app.engine.metrics import _series_growth, _series_cagr
    assert round(_series_growth(series, 1), 3) == 0.2
    assert round(_series_cagr(series, 5), 3) == round(2.4 ** 0.2 - 1, 3)


def test_instant_tags_do_not_discard_previous_year():
    gaap = {"New": {"units": {"TWD": [_filing(2025, 120, True)]}},
            "Old": {"units": {"TWD": [_filing(2024, 100, True)]}}}
    assert len(_instant_series(gaap, ["New", "Old"], unit="TWD").points) == 2


def test_reference_fx_when_yahoo_unavailable():
    def fake_fetch(url, **kwargs):
        if "yahoo" in url:
            from app.http import FetchError
            raise FetchError("blocked")
        return {"result": "success", "rates": {"TWD": 32},
                "time_last_update_utc": "2026-09-19"}
    with patch("app.providers.fx.fetch", side_effect=fake_fetch):
        fx = usd_per_twd(allow_yahoo=False)
    assert fx is not None and fx[0] == 1 / 32
    assert "open.er-api.com" in fx[1]


def test_momentum_needs_sufficient_history_and_benchmark():
    start = date(2026, 1, 1)
    history = PriceHistory(ticker="TSM", points=[
        PricePoint(d=start + timedelta(days=i), close=100 + i / 2)
        for i in range(260)
    ])
    benchmark = PriceHistory(ticker="SPY", points=[
        PricePoint(d=start + timedelta(days=i), close=100 + i / 4)
        for i in range(260)
    ])
    fund = Fundamentals(ticker="TSM", fiscal_end=date(2025, 12, 31),
                        revenue=FactSeries(tag="x", unit="USD", points=[(date(2025, 12, 31), 100)]))
    row = compute_row("TSM", history=history, benchmark=benchmark,
                      quote=Quote(ticker="TSM", price=history.last()), fund=fund)
    assert row.return_3m is not None
    assert row.excess_return_6m > 0
    short = PriceHistory(ticker="TSM", points=history.points[-30:])
    row_short = compute_row("TSM", history=short, benchmark=benchmark,
                            quote=Quote(ticker="TSM", price=short.last()), fund=fund)
    assert row_short.return_3m is None
    assert row_short.excess_return_6m is None


def test_equal_scores_neutral_and_negative_pe_not_a_bargain():
    assert _percentile_score(1, [1, 1, 1], True) == 5.5
    assert _percentile_score(1, [1, 1, 1], False) == 5.5
    rows = [MetricRow(ticker=t) for t in ("A", "B", "C")]
    for row in rows:
        row.forward_pe = 20 if row.ticker == "A" else (25 if row.ticker == "B" else -5)
    score_peers(rows)
    assert rows[0].z_forward_pe is not None
    assert rows[2].z_forward_pe is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
