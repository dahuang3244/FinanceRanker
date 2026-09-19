"""Market statistics: returns, beta and drawdown."""

from __future__ import annotations

from app.models import PriceHistory, PricePoint


def daily_returns(points: list[PricePoint]) -> dict[int, float]:
    """Map `date.toordinal()` -> simple daily return."""
    out: dict[int, float] = {}
    for prev, cur in zip(points, points[1:]):
        if prev.close:
            out[cur.d.toordinal()] = (cur.close / prev.close) - 1.0
    return out


def beta(stock: PriceHistory, benchmark: PriceHistory, min_obs: int = 30) -> float | None:
    """Covariance / variance of aligned daily returns against the benchmark."""
    sr = daily_returns(stock.points)
    br = daily_returns(benchmark.points)
    xs: list[float] = []
    ys: list[float] = []
    for key, ret in sr.items():
        if key in br:
            xs.append(br[key])
            ys.append(ret)
    if len(xs) < min_obs:
        return None
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    var = sum((x - mx) ** 2 for x in xs)
    if var == 0:
        return None
    return cov / var


def drawdown_from_high(price: float | None, high: float | None) -> float | None:
    if price is None or not high:
        return None
    return price / high - 1.0


def return_over_window(history: PriceHistory, days: int = 365) -> float | None:
    """Total return using adjusted closes across the last `days` calendar days."""
    if len(history.points) < 2:
        return None
    points = history.window(days)
    if len(points) < 2 or not points[0].close:
        return None
    return points[-1].close / points[0].close - 1.0
