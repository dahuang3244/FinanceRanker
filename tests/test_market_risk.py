"""Market return/risk metrics, price-basis discipline and ADR completeness.

These run offline on synthetic series, so they assert *arithmetic* rather than
whatever the network happened to return today.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from unittest.mock import patch

from app.engine import market, metrics, scoring
from app.models import (
    AnalystView,
    FactSeries,
    Fundamentals,
    MetricRow,
    PriceHistory,
    PricePoint,
    Quote,
)
from app.providers import prices


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def series_from_returns(returns: list[float], start: float = 100.0) -> PriceHistory:
    """A price series whose day-over-day returns are exactly `returns`."""
    points = [PricePoint(d=date(2024, 1, 1), close=start)]
    price = start
    for i, r in enumerate(returns, start=1):
        price *= 1.0 + r
        points.append(PricePoint(d=date(2024, 1, 1) + timedelta(days=i), close=price))
    return PriceHistory(ticker="X", points=points, source="test", used_adjusted=True)


def business_days(days: int) -> list[date]:
    out: list[date] = []
    day = date(2024, 1, 1)
    while len(out) < days:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def synthetic_history(returns: list[float], start: float = 100.0) -> PriceHistory:
    days = business_days(len(returns) + 1)
    points = [PricePoint(d=days[0], close=start)]
    price = start
    for i, r in enumerate(returns):
        price *= 1.0 + r
        points.append(PricePoint(d=days[i + 1], close=price))
    return PriceHistory(ticker="X", points=points, source="test", used_adjusted=True)


# --------------------------------------------------------------------------- #
# beta — the metric that was silently wrong before
# --------------------------------------------------------------------------- #
def test_beta_divides_by_benchmark_variance_not_stock_variance():
    """beta = cov/var(benchmark). Using var(stock) would return the correlation."""
    bench = synthetic_history([0.010, -0.008, 0.004, -0.002, 0.006, -0.005, 0.003, 0.001] * 10)
    # A stock that is exactly twice the benchmark moves: true beta is 2.0.
    stock = synthetic_history([2 * r for r in [0.010, -0.008, 0.004, -0.002, 0.006, -0.005, 0.003, 0.001] * 10])
    value = market.beta(stock, bench)
    assert value is not None and math.isclose(value, 2.0, rel_tol=1e-9)
    # With perfect correlation the ratio cov/var(stock) would be 1/2, so this
    # also pins the exact denominator that was wrong.
    assert not math.isclose(value, 0.5, rel_tol=1e-6)


def test_beta_reproduces_correlation_times_volatility_ratio():
    rng = [((i * 7919) % 101 - 50) / 500 for i in range(120)]
    bench = synthetic_history(rng)
    stock = synthetic_history([r * 1.5 + 0.0004 for r in rng])
    beta = market.beta(stock, bench)
    correlation = market.correlation(stock, bench)
    ratio = market.volatility(market._paired_returns(stock, None)[0]) / 0.0 if False else None
    xs, ys = market._paired_returns(stock, bench)
    ratio = market.volatility(xs) / market.volatility(ys)
    assert beta is not None and correlation is not None
    assert math.isclose(beta, correlation * ratio, rel_tol=1e-9)


def test_paired_returns_stay_aligned_when_dates_differ():
    """Extra days on one side must not shift the pairing."""
    bench = synthetic_history([0.01, -0.01, 0.02, -0.02, 0.03, -0.03] * 10)
    stock = bench.model_copy(update={"points": bench.points[:40]})
    xs, ys = market._paired_returns(stock, bench)
    assert len(xs) == len(ys) == 39
    # day i of the stock must sit next to the same date of the benchmark
    sr = market.daily_returns(stock.points)
    br = market.daily_returns(bench.points)
    assert xs[0] == sr[sorted(sr)[0]]
    assert ys[0] == br[sorted(br)[0]]


# --------------------------------------------------------------------------- #
# returns and windows
# --------------------------------------------------------------------------- #
def test_window_returns_require_enough_calendar_coverage():
    # 20 trading days inside a 91-day window must NOT be quoted as a 3M return.
    short = synthetic_history([0.001] * 20)
    assert market.period_return(short, 91, min_days=70) is None
    assert market.window_returns(short)["3m"] is None
    # A full quarter of trading days is accepted.
    full = synthetic_history([0.001] * 70)
    assert market.window_returns(full)["3m"] is not None


def test_excess_return_needs_both_legs_on_the_same_window():
    stock = synthetic_history([0.002] * 200)
    bench = synthetic_history([0.001] * 200)
    excess = market.excess_returns(stock, bench)
    assert excess["3m"] is not None and excess["6m"] is not None
    # 200 business days ≈ 280 calendar days, so the 1Y leg is not available.
    assert excess["1y"] is None
    # 3M excess is the difference of the two legs
    assert math.isclose(
        excess["3m"],
        market.period_return(stock, 91, min_days=70)
        - market.period_return(bench, 91, min_days=70),
        rel_tol=1e-12,
    )


def test_year_to_date_uses_the_previous_calendar_year_close():
    points = [
        PricePoint(d=date(2025, 12, 24), close=100.0),
        PricePoint(d=date(2025, 12, 31), close=110.0),   # the YTD base
        PricePoint(d=date(2026, 1, 20), close=121.0),
        PricePoint(d=date(2026, 6, 30), close=132.0),
    ]
    history = PriceHistory(ticker="X", points=points)
    assert math.isclose(market.year_to_date_return(history), 0.20, rel_tol=1e-12)


def test_year_to_date_is_blank_for_a_series_that_starts_this_year():
    points = [
        PricePoint(d=date(2026, 3, 2), close=100.0),
        PricePoint(d=date(2026, 6, 30), close=110.0),
    ]
    assert market.year_to_date_return(PriceHistory(ticker="X", points=points)) is None


# --------------------------------------------------------------------------- #
# risk statistics
# --------------------------------------------------------------------------- #
def test_sharpe_uses_the_shared_risk_free_rate():
    returns = [0.001, -0.0005, 0.002, -0.001, 0.0015] * 40
    history = synthetic_history(returns)
    xs, _ = market._paired_returns(history, None)
    zero_rf = market.sharpe_ratio(xs, 0.0)
    with_rf = market.sharpe_ratio(xs, 0.05)
    assert zero_rf is not None and with_rf is not None
    # A higher risk-free rate can only lower the ratio.
    assert with_rf < zero_rf
    # And the ratio is the annualised mean spread over annualised volatility.
    assert math.isclose(
        zero_rf, (sum(xs) / len(xs)) * market.TRADING_DAYS / market.volatility(xs), rel_tol=1e-12
    )


def test_volatility_and_downside_deviation_are_annualised():
    # Constant +1% then -1%: sample std of the daily returns, annualised.
    returns = [0.01, -0.01] * 50
    history = synthetic_history(returns)
    xs, _ = market._paired_returns(history, None)
    expected_daily = math.sqrt(sum((r - sum(xs) / len(xs)) ** 2 for r in xs) / (len(xs) - 1))
    assert math.isclose(market.volatility(xs), expected_daily * math.sqrt(252), rel_tol=1e-12)
    # Downside deviation only penalises the negative half.
    assert market.downside_deviation(xs) < market.volatility(xs)


def test_max_drawdown_finds_the_worst_peak_to_trough():
    points = [
        PricePoint(d=date(2025, 1, 1), close=100.0),
        PricePoint(d=date(2025, 2, 1), close=150.0),   # peak
        PricePoint(d=date(2025, 3, 1), close=90.0),    # -40%
        PricePoint(d=date(2025, 4, 1), close=120.0),
    ]
    history = PriceHistory(ticker="X", points=points)
    assert math.isclose(market.max_drawdown(history), -0.40, rel_tol=1e-12)


def varying_returns(count: int, seed: int = 17) -> list[float]:
    """Deterministic but non-constant daily returns, so vol/Sharpe are defined."""
    out: list[float] = []
    state = seed
    for _ in range(count):
        state = (state * 1103515245 + 12345) % (2 ** 31)
        out.append(((state / (2 ** 31)) - 0.5) * 0.04)   # ±2% band
    return out


def test_risk_stats_leave_benchmark_figures_blank_without_a_benchmark():
    history = synthetic_history(varying_returns(260))
    stats = market.risk_stats(history, None, risk_free=0.04)
    assert stats["volatility"] is not None
    assert stats["sharpe"] is not None
    assert stats["beta"] is None and stats["beta_1y"] is None
    assert stats["correlation"] is None


# --------------------------------------------------------------------------- #
# engine wiring
# --------------------------------------------------------------------------- #
def _fund() -> Fundamentals:
    return Fundamentals(
        ticker="AAA", entity_name="AAA Inc", currency="USD", source="test",
        fiscal_end=date(2025, 12, 31),
        revenue=FactSeries(tag="R", unit="USD", points=[(date(2025, 12, 31), 1000.0)]),
        operating_income=FactSeries(tag="OI", unit="USD", points=[(date(2025, 12, 31), 200.0)]),
        net_income=FactSeries(tag="NI", unit="USD", points=[(date(2025, 12, 31), 150.0)]),
        eps_diluted=FactSeries(tag="EPS", unit="USD/shares", points=[(date(2025, 12, 31), 1.5)]),
        operating_cash_flow=FactSeries(tag="OCF", unit="USD", points=[(date(2025, 12, 31), 250.0)]),
        capex=FactSeries(tag="Capex", unit="USD", points=[(date(2025, 12, 31), 100.0)]),
        shares_outstanding=100.0,
    )


def test_row_gets_every_return_and_risk_metric_with_a_benchmark():
    stock = synthetic_history(varying_returns(400, seed=3))
    bench = synthetic_history(varying_returns(400, seed=11))
    row = metrics.compute_row(
        "AAA", history=stock, quote=Quote(ticker="AAA", currency="USD", price=stock.last()),
        fund=_fund(), benchmark=bench,
    )
    for attr in ("return_1m", "return_3m", "return_6m", "return_1y",
                 "excess_return_3m", "excess_return_6m", "excess_return_1y",
                 "volatility", "sharpe_ratio", "sortino_ratio",
                 "max_drawdown_1y", "drawdown_52w", "beta", "beta_1y"):
        assert getattr(row, attr) is not None, f"{attr} should be populated"
    assert row.benchmark_ticker == bench.ticker
    assert row.risk_free_rate is not None
    assert row.market_price_basis and "adjusted" in row.market_price_basis
    assert "risk-free" in row.notes


def test_row_without_a_benchmark_keeps_absolute_returns_and_says_so():
    stock = synthetic_history([0.001] * 400)
    row = metrics.compute_row(
        "AAA", history=stock, quote=Quote(ticker="AAA", currency="USD", price=stock.last()),
        fund=_fund(), benchmark=None,
    )
    assert row.return_3m is not None and row.volatility is not None
    assert row.excess_return_3m is None and row.beta is None
    assert "Benchmark unavailable" in row.notes


def test_analyst_view_populates_target_and_upside():
    stock = synthetic_history([0.001] * 400)
    row = metrics.compute_row(
        "AAA", history=stock, quote=Quote(ticker="AAA", currency="USD", price=100.0),
        fund=_fund(), analyst=AnalystView(
            ticker="AAA", target_mean=125.0, target_median=120.0, target_high=160.0,
            target_low=90.0, analyst_count=18.0, recommendation="buy",
            yahoo_beta=1.25, source="test consensus",
        ),
    )
    assert row.analyst_target == 125.0
    assert math.isclose(row.analyst_upside, 0.25, rel_tol=1e-12)
    assert row.analyst_count == 18.0
    assert row.analyst_recommendation == "buy"
    assert row.beta_published == 1.25
    assert row.analyst_source == "test consensus"


# --------------------------------------------------------------------------- #
# ADR / filing gaps
# --------------------------------------------------------------------------- #
def test_missing_diluted_share_series_falls_back_to_the_share_count():
    fund = _fund()
    fund.shares_diluted = None
    row = metrics.compute_row(
        "AAA", history=synthetic_history([0.001] * 400),
        quote=Quote(ticker="AAA", currency="USD", price=10.0), fund=fund,
    )
    assert row.diluted_shares_fy0 == 100.0
    assert "unavailable" in row.share_basis


def test_fcf_subtracts_capex_from_operating_cash_flow():
    """FCF = OCF - capex. The proxy must replace capex, not stack on top of it."""
    fund = _fund()
    fund.capex = None
    fund.depreciation_amortization = FactSeries(
        tag="DA", unit="USD", points=[(date(2025, 12, 31), 80.0)]
    )
    row = metrics.compute_row(
        "AAA", history=synthetic_history(varying_returns(400)),
        quote=Quote(ticker="AAA", currency="USD", price=10.0), fund=fund,
    )
    # Half of D&A, and the substitution is disclosed rather than silent.
    assert math.isclose(row.capex_fy0, 40.0)
    assert "proxy" in row.capex_basis
    assert row.fcf_fy0 is not None and math.isclose(row.fcf_fy0, 250.0 - 40.0)


def test_currency_guard_keeps_currency_free_ratios():
    """An ADR row must not lose FCF margin just because it files in another currency."""
    fund = _fund()
    fund.currency = "TWD"
    fund.reporting_currency = "TWD"
    row = metrics.compute_row(
        "AAA", history=synthetic_history([0.001] * 400),
        quote=Quote(ticker="AAA", currency="USD", price=10.0), fund=fund,
    )
    assert row.status.startswith("Partial filing data")
    # Ratios of same-currency amounts survive the guard...
    assert row.fcf_margin is not None
    assert row.ocf_to_net_income is not None
    # ...while price multiples that would mix USD with TWD are withheld.
    assert row.price_to_sales is None
    assert row.fcf_yield is None


# --------------------------------------------------------------------------- #
# price basis
# --------------------------------------------------------------------------- #
def test_basis_mismatch_is_rejected_rather_than_silently_mixed():
    """A raw-close series must never be accepted as an adjusted-basis series."""
    raw = PriceHistory(
        ticker="AAA", source="raw", used_adjusted=False,
        points=[PricePoint(d=date(2025, 1, 1) + timedelta(days=i), close=100.0 + i)
                for i in range(400)],
    )
    adjusted_basis = prices.PriceBasis(adjusted=True, source="Yahoo")

    # The acceptance test itself.
    assert prices._matches_basis(raw, adjusted_basis) is False
    assert prices._matches_basis(raw, prices.PriceBasis(adjusted=False, source="Sina")) is True
    assert prices._matches_basis(raw, None) is True

    # A raw-only provider is not even offered when an adjusted basis is wanted.
    def sina_like(ticker):
        return raw

    sina_like.__name__ = "_sina"
    with patch("app.providers.prices._PIPELINE", [sina_like]):
        assert prices._pipeline_for(adjusted_basis, allow_yahoo=False) == []
        try:
            prices.get_price_history("AAA", basis=adjusted_basis)
        except Exception as exc:
            assert "price basis" in str(exc), str(exc)
        else:  # pragma: no cover
            raise AssertionError("a raw-only provider must not satisfy an adjusted basis")

    # And a provider consulted for an adjusted basis that answers raw is refused
    # by name rather than mixed into the comparison.
    def mislabelled(ticker):
        return raw

    mislabelled.__name__ = "_yahoo"
    with patch("app.providers.prices._PIPELINE", [mislabelled]):
        try:
            prices.get_price_history("AAA", allow_yahoo=True, basis=adjusted_basis)
        except Exception as exc:
            assert "basis mismatch" in str(exc), str(exc)
        else:  # pragma: no cover
            raise AssertionError("a raw series must not satisfy an adjusted basis")


def test_basis_resolution_falls_back_to_sina_when_yahoo_is_unreachable():
    with patch("app.providers.prices._yahoo", side_effect=RuntimeError("blocked")):
        basis = prices.resolve_basis("SPY", allow_yahoo=True)
    assert basis.adjusted is False and basis.source == "Sina"


def test_basis_resolution_prefers_yahoo_when_it_returns_adjusted_history():
    spy = PriceHistory(
        ticker="SPY", used_adjusted=True, source="Yahoo",
        points=[PricePoint(d=date(2024, 1, 1) + timedelta(days=i), close=400.0 + i)
                for i in range(400)],
    )
    with patch("app.providers.prices._yahoo", return_value=spy):
        basis = prices.resolve_basis("SPY", allow_yahoo=True)
    assert basis.adjusted is True and basis.source == "Yahoo"


# --------------------------------------------------------------------------- #
# scoring wiring
# --------------------------------------------------------------------------- #
def test_risk_metrics_are_part_of_the_scoring_universe():
    attrs = {attr for attr, _, _ in scoring.METRICS}
    for attr in ("return_3m", "return_6m", "return_1y", "excess_return_3m",
                 "excess_return_6m", "sharpe_ratio", "sortino_ratio", "volatility",
                 "beta_1y", "max_drawdown_1y", "drawdown_52w"):
        assert attr in attrs, f"{attr} should be scored"
    # The published full-history beta is reported but must not be double-counted.
    assert "beta" not in attrs
    market_component = [a for a, c, _ in scoring.METRICS if c == "market"]
    assert len(market_component) >= 8


def test_lower_volatility_and_beta_score_better():
    rows = []
    for i, ticker in enumerate(("CALM", "WILD")):
        row = MetricRow(ticker=ticker)
        for attr, _, _ in scoring.METRICS:
            setattr(row, attr, 5.0)
        rows.append(row)
    calm, wild = rows
    # The market component blends performance and risk, so both halves need to
    # separate the two names for the component score to exist at all.
    calm.return_3m, wild.return_3m = 0.08, -0.02
    calm.excess_return_3m, wild.excess_return_3m = 0.05, -0.06
    calm.excess_return_6m, wild.excess_return_6m = 0.04, -0.05
    calm.volatility, wild.volatility = 0.15, 0.60
    calm.beta_1y, wild.beta_1y = 0.6, 1.9
    calm.sharpe_ratio, wild.sharpe_ratio = 1.4, 0.3
    scoring.score_peers(rows)
    assert calm.z_volatility > wild.z_volatility
    assert calm.z_beta_1y > wild.z_beta_1y
    assert calm.score_market_performance > wild.score_market_performance
    assert calm.score_market_risk > wild.score_market_risk
    assert calm.score_market > wild.score_market


def test_market_aggregate_needs_both_performance_and_risk():
    """A market score must not be decided by returns alone, nor risk alone."""
    def build(strip: str | None):
        rows = [MetricRow(ticker=t) for t in ("A", "B")]
        for i, row in enumerate(rows):
            for attr, _, _ in scoring.METRICS:
                setattr(row, attr, float(i + 1))
        if strip:
            # Drop the raw values, not the z-scores: they are recomputed.
            for attr in scoring.MARKET_SUBWEIGHTS[strip]:
                for row in rows:
                    setattr(row, attr, None)
        scoring.score_peers(rows)
        return rows

    full = build(None)
    assert full[0].score_market_performance is not None
    assert full[0].score_market_risk is not None
    assert full[0].score_market is not None

    returns_only = build("risk")
    assert returns_only[0].score_market_performance is not None
    assert returns_only[0].score_market_risk is None
    assert returns_only[0].score_market is None, "returns alone must not yield a market score"

    risk_only = build("performance")
    assert risk_only[0].score_market_performance is None
    assert risk_only[0].score_market_risk is not None
    assert risk_only[0].score_market is None, "risk alone must not yield a market score"


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
