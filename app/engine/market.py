"""Market statistics: returns, risk and beta.

Everything here works on a `PriceHistory` alone, so a stock never loses a market
metric just because one provider was unreachable. When a benchmark (SPY) is
available the same helpers are reused on it, which keeps stock and benchmark
returns on an identical price basis — a requirement for the excess-return
figures to mean anything.

Return convention: *total* return over the window, first close to last close
inside the window. Feed this module dividend/split-adjusted closes (Yahoo
`adjclose`) when they are available, otherwise a raw close series will
understate returns by the dividend yield.
"""

from __future__ import annotations

import math
from datetime import date

from app.models import PriceHistory, PricePoint

# Trading days per year — the standard annualisation factor.
TRADING_DAYS = 252

# 3M / 6M are calendar windows, matching how the workbook and every data vendor
# quote "3-month return". The `*_min_days` floor stops a holiday-shortened or
# sparse series from silently quoting a two-week move as a quarterly return.
WINDOWS: dict[str, int] = {
    "1m": 30,
    "3m": 91,
    "6m": 183,
    "1y": 365,
    "3y": 1095,
}
MIN_WINDOW_DAYS: dict[str, int] = {
    "1m": 21,
    "3m": 70,
    "6m": 150,
    "1y": 300,
    "3y": 900,
}


# --------------------------------------------------------------------------- #
# series alignment
# --------------------------------------------------------------------------- #
def align(
    stock: PriceHistory,
    benchmark: PriceHistory,
) -> list[tuple[date, float, float]]:
    """(date, stock close, benchmark close) on dates present in both series."""
    left = {p.d: p.close for p in stock.points}
    right = {p.d: p.close for p in benchmark.points}
    return [(d, left[d], right[d]) for d in sorted(left.keys() & right.keys())]


def daily_returns(points: list[PricePoint]) -> dict[int, float]:
    """Map `date.toordinal()` -> simple daily return."""
    out: dict[int, float] = {}
    for prev, cur in zip(points, points[1:]):
        if prev.close:
            out[cur.d.toordinal()] = (cur.close / prev.close) - 1.0
    return out


def _paired_returns(
    stock: PriceHistory,
    benchmark: PriceHistory | None,
) -> tuple[list[float], list[float]]:
    """Aligned daily return lists; the benchmark list is empty without SPY.

    Both lists are ordered by the same date key. Returning them independently
    would desynchronise the pairs as soon as the stock has a trading day the
    benchmark does not (or vice versa), which silently corrupts covariance.
    """
    sr = daily_returns(stock.points)
    if benchmark is None:
        return list(sr.values()), []
    br = daily_returns(benchmark.points)
    shared = sorted(set(sr) & set(br))
    return [sr[k] for k in shared], [br[k] for k in shared]


# --------------------------------------------------------------------------- #
# returns
# --------------------------------------------------------------------------- #
def return_over_window(history: PriceHistory, days: int = 365) -> float | None:
    """Total return using closes across the last `days` calendar days."""
    if len(history.points) < 2:
        return None
    points = history.window(days)
    if len(points) < 2 or not points[0].close:
        return None
    return points[-1].close / points[0].close - 1.0


def period_return(
    history: PriceHistory,
    days: int,
    *,
    min_days: int | None = None,
) -> float | None:
    """Return over `days`, rejected when the window is too short to be honest.

    `min_days` defaults to roughly 70% of the requested window, which tolerates
    holidays and suspended trading without accepting a truncated series.
    """
    floor = min_days if min_days is not None else int(days * 0.7)
    if len(history.points) < 2:
        return None
    points = history.window(days)
    if len(points) < 2 or not points[0].close:
        return None
    if (points[-1].d - points[0].d).days < floor:
        return None
    return points[-1].close / points[0].close - 1.0


def year_to_date_return(history: PriceHistory, as_of: date | None = None) -> float | None:
    """Total return from the last close of the previous calendar year.

    The base is the last available close *at or before* December 31 of the prior
    year, not the first close of the current year: a series that only starts in
    March has no prior-year close and therefore no year-to-date figure, and
    anchoring on the first current-year close would silently misstate it.
    """
    if len(history.points) < 2:
        return None
    last = as_of or history.points[-1].d
    year_end = date(last.year - 1, 12, 31)
    prior = [p for p in history.points if p.d <= year_end]
    if not prior or not prior[-1].close:
        return None
    first = prior[-1]
    if (last - first.d).days < 14:
        return None
    return history.points[-1].close / first.close - 1.0


def window_returns(history: PriceHistory) -> dict[str, float | None]:
    """Every quoted return window this price series can support."""
    out: dict[str, float | None] = {
        key: period_return(history, days, min_days=MIN_WINDOW_DAYS[key])
        for key, days in WINDOWS.items()
    }
    out["ytd"] = year_to_date_return(history)
    return out


def excess_returns(
    stock: PriceHistory,
    benchmark: PriceHistory,
) -> dict[str, float | None]:
    """Stock return minus benchmark return, per window, on aligned prices."""
    out: dict[str, float | None] = {}
    for key, days in WINDOWS.items():
        floor = MIN_WINDOW_DAYS[key]
        stock_ret = period_return(stock, days, min_days=floor)
        bench_ret = period_return(benchmark, days, min_days=floor)
        # Both legs must be quoted over the same calendar window, otherwise the
        # difference mixes two different holding periods.
        stock_points, bench_points = stock.window(days), benchmark.window(days)
        same_window = (
            stock_points
            and bench_points
            and abs((stock_points[0].d - bench_points[0].d).days) <= 7
        )
        out[key] = (
            stock_ret - bench_ret
            if stock_ret is not None and bench_ret is not None and same_window
            else None
        )
    out["ytd"] = None
    ytd_stock = year_to_date_return(stock)
    ytd_bench = year_to_date_return(benchmark)
    if ytd_stock is not None and ytd_bench is not None:
        out["ytd"] = ytd_stock - ytd_bench
    return out


# --------------------------------------------------------------------------- #
# risk
# --------------------------------------------------------------------------- #
def volatility(returns: list[float], days: int = TRADING_DAYS) -> float | None:
    """Annualised standard deviation of daily returns (sample, n-1)."""
    n = len(returns)
    if n < 2:
        return None
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    return math.sqrt(variance) * math.sqrt(days)


def annualised_return(returns: list[float], days: int = TRADING_DAYS) -> float | None:
    """Geometric annualised return implied by a daily return series."""
    if len(returns) < 2:
        return None
    growth = 1.0
    for r in returns:
        growth *= 1.0 + r
    if growth <= 0:
        return None
    years = len(returns) / days
    if years <= 0:
        return None
    return growth ** (1.0 / years) - 1.0


def sharpe_ratio(
    returns: list[float],
    risk_free: float = 0.0,
    days: int = TRADING_DAYS,
) -> float | None:
    """(annualised excess return) / annualised volatility.

    `risk_free` is an annual rate; it is de-annualised before subtracting so the
    arithmetic stays in daily units. A single risk-free assumption is applied to
    every peer, which is what makes the cross-sectional comparison fair.
    """
    vol = volatility(returns, days)
    if vol is None or vol == 0:
        return None
    daily_rf = (1.0 + risk_free) ** (1.0 / days) - 1.0
    excess = [r - daily_rf for r in returns]
    mean = sum(excess) / len(excess)
    return (mean * days) / vol


def sortino_ratio(
    returns: list[float],
    risk_free: float = 0.0,
    days: int = TRADING_DAYS,
) -> float | None:
    """Like Sharpe, but only downside deviation is penalised."""
    if len(returns) < 2:
        return None
    daily_rf = (1.0 + risk_free) ** (1.0 / days) - 1.0
    excess = [r - daily_rf for r in returns]
    downside = [min(0.0, e) for e in excess]
    dd = math.sqrt(sum(d * d for d in downside) / len(downside))
    if dd == 0:
        return None
    mean = sum(excess) / len(excess)
    return (mean * days) / (dd * math.sqrt(days))


def max_drawdown(history: PriceHistory, lookback_days: int | None = None) -> float | None:
    """Worst peak-to-trough decline (a negative number) over the window."""
    points = history.window(lookback_days) if lookback_days else history.points
    if len(points) < 2:
        return None
    peak = points[0].close
    worst = 0.0
    for p in points:
        peak = max(peak, p.close)
        if peak:
            worst = min(worst, p.close / peak - 1.0)
    return worst


def drawdown_from_high(price: float | None, high: float | None) -> float | None:
    if price is None or not high:
        return None
    return price / high - 1.0


def beta(
    stock: PriceHistory,
    benchmark: PriceHistory,
    min_obs: int = 30,
) -> float | None:
    """Covariance / variance of aligned daily returns against the benchmark.

    Beta is `cov(stock, benchmark) / var(benchmark)`. Dividing by the *stock's*
    variance instead would produce `corr * sigma_stock / sigma_stock`, i.e. the
    correlation — a classic and silent way to understate every high-volatility
    name against the index.
    """
    xs, ys = _paired_returns(stock, benchmark)
    if len(xs) < min_obs:
        return None
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    var_benchmark = sum((y - my) ** 2 for y in ys)
    if var_benchmark == 0:
        return None
    return cov / var_benchmark


def beta_over_window(
    stock: PriceHistory,
    benchmark: PriceHistory,
    days: int,
    min_obs: int = 30,
) -> float | None:
    """Beta estimated from the last `days` of aligned daily returns."""
    return beta(
        stock.model_copy(update={"points": stock.window(days)}),
        benchmark.model_copy(update={"points": benchmark.window(days)}),
        min_obs=min_obs,
    )


def correlation(
    stock: PriceHistory,
    benchmark: PriceHistory,
    min_obs: int = 30,
) -> float | None:
    """Pearson correlation of aligned daily returns."""
    xs, ys = _paired_returns(stock, benchmark)
    n = len(xs)
    if n < min_obs:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / math.sqrt(vx * vy)


def downside_deviation(returns: list[float], days: int = TRADING_DAYS) -> float | None:
    """Annualised deviation of negative daily returns only."""
    if len(returns) < 2:
        return None
    downside = [min(0.0, r) for r in returns]
    return math.sqrt(sum(d * d for d in downside) / len(downside)) * math.sqrt(days)


def risk_stats(
    stock: PriceHistory,
    benchmark: PriceHistory | None = None,
    *,
    risk_free: float = 0.0,
    lookback_days: int = 365,
    beta_history_days: int = 5 * 365,
) -> dict[str, float | None]:
    """One-shot bundle of the risk figures the UI and scoring both need.

    Volatility, Sharpe, Sortino and the drawdowns use a trailing year of daily
    returns: long enough to be stable, short enough to reflect the current
    regime. Beta is also reported over the full available history (the standard
    vendor convention) because a one-year beta on a single stock is noisy.
    """
    window_points = stock.window(lookback_days)
    window = stock.model_copy(update={"points": window_points})
    returns, _ = _paired_returns(window, benchmark)
    beta_window = stock.model_copy(update={"points": stock.window(beta_history_days)})
    return {
        "volatility": volatility(returns),
        "downside_deviation": downside_deviation(returns),
        "sharpe": sharpe_ratio(returns, risk_free),
        "sortino": sortino_ratio(returns, risk_free),
        "max_drawdown": max_drawdown(stock, lookback_days),
        "beta": beta(stock, benchmark) if benchmark is not None else None,
        "beta_1y": (
            beta(window, benchmark.model_copy(update={"points": benchmark.window(lookback_days)}))
            if benchmark is not None
            else None
        ),
        "beta_history": (
            beta(beta_window,
                 benchmark.model_copy(update={"points": benchmark.window(beta_history_days)}))
            if benchmark is not None
            else None
        ),
        "correlation": correlation(stock, benchmark) if benchmark is not None else None,
        "obs": float(len(returns)),
    }


def return_dates(history: PriceHistory, days: int) -> tuple[date, date] | None:
    """The (start, end) dates a window return is actually measured between."""
    points = history.window(days)
    if len(points) < 2:
        return None
    return points[0].d, points[-1].d


def days_since(d: date, today: date | None = None) -> int:
    return ((today or date.today()) - d).days


__all__ = [
    "TRADING_DAYS", "WINDOWS", "MIN_WINDOW_DAYS", "align", "annualised_return",
    "beta", "beta_over_window", "correlation", "daily_returns", "days_since",
    "downside_deviation", "drawdown_from_high", "excess_returns", "max_drawdown",
    "period_return", "return_dates", "return_over_window", "risk_stats",
    "sharpe_ratio", "sortino_ratio", "volatility", "window_returns",
    "year_to_date_return",
]
