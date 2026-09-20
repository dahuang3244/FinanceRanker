"""Annual TWD gap fill and dated technical indicators."""

from datetime import date, timedelta
from unittest.mock import patch

from app.insights import build_insights, technical
from app.models import FactSeries, Fundamentals, MetricRow, PriceHistory, PricePoint
from app.providers.fundamentals import get_fundamentals
from app.providers.news import headline_temperature
from app.providers.prices import get_price_history


def _history(ticker, count, end=None):
    end = end or date.today()
    return PriceHistory(ticker=ticker, points=[
        PricePoint(d=end - timedelta(days=count - i - 1), close=100 + i)
        for i in range(count)
    ], source="sample")


def test_short_history_does_not_hide_full_history():
    first, second = _history("TSM", 5), _history("TSM", 400)
    with patch("app.providers.prices._PIPELINE", [lambda _: first, lambda _: second]):
        chosen = get_price_history("TSM")
    assert chosen is second
    assert technical(chosen)["ma200"] is not None


def test_sparse_year_does_not_hide_daily_series():
    sparse = PriceHistory(ticker="TSM", points=[
        PricePoint(d=date.today() - timedelta(days=365), close=100),
        PricePoint(d=date.today(), close=105),
    ])
    daily = _history("TSM", 400)
    with patch("app.providers.prices._PIPELINE", [lambda _: sparse, lambda _: daily]):
        assert get_price_history("TSM") is daily


def test_tsm_annual_gap_fill_preserves_twd_fiscal_alignment():
    fy = date(2025, 12, 31)
    series = lambda value: FactSeries(tag="SEC", unit="TWD", points=[(fy, value)])
    sec = Fundamentals(ticker="TSM", currency="TWD", fiscal_end=fy,
                       revenue=series(100), operating_income=series(35))
    yahoo = Fundamentals(ticker="2330.TW", currency="TWD", fiscal_end=fy,
                         capex=FactSeries(tag="Yahoo", unit="TWD", points=[(fy, 15)]))
    with patch("app.providers.fundamentals.get_sec_fundamentals", return_value=sec), patch(
        "app.providers.yahoo_fundamentals.get_yahoo_fundamentals", return_value=yahoo
    ) as fallback:
        result = get_fundamentals("TSM", allow_yahoo=True)
    fallback.assert_called_once_with("2330.TW", currency="TWD")
    assert result.ticker == "TSM" and result.capex.latest() == 15
    assert result.revenue.latest() == 100


def test_technical_requires_real_bars_and_exposes_dates():
    short = technical(_history("TSM", 10))
    assert short["ma20"] is None and short["rsi14"] is None
    full = technical(_history("TSM", 400))
    assert full["ma200"] is not None and full["rsi14"] == 100
    assert full["from_high"] == 0
    result = build_insights(MetricRow(ticker="TSM", return_3m=0.11), _history("TSM", 400))
    assert result["report"]["next_earnings_date"] is None
    assert result["return_3m"] == 0.11


def test_headline_temperature_needs_directional_evidence():
    assert headline_temperature(["the company reports", "stock update"]) is None
    assert headline_temperature(["Company beats expectations", "Shares rise on record growth"]) == 1
    assert headline_temperature(["Company beats expectations", "Stock downgraded today"]) == 0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
