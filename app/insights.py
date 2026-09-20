"""Dated, inspectable company indicators; no inferred earnings announcements."""

from __future__ import annotations

from app.models import MetricRow, PriceHistory


def _rsi(closes: list[float]) -> float | None:
    if len(closes) < 15:
        return None
    changes = [b - a for a, b in zip(closes[-15:-1], closes[-14:])]
    gains = sum(max(0.0, change) for change in changes)
    losses = sum(max(0.0, -change) for change in changes)
    if losses == 0:
        return 100.0 if gains else 50.0
    return 100.0 - 100.0 / (1.0 + gains / losses)


def technical(history: PriceHistory) -> dict:
    points = sorted(history.points, key=lambda p: p.d)
    closes = [p.close for p in points if p.close > 0]
    result = {"as_of": points[-1].d.isoformat() if points else None,
              "source": history.source, "adjusted": history.used_adjusted,
              "close": closes[-1] if closes else None, "bars": len(closes),
              "ma20": None, "ma50": None, "ma200": None, "rsi14": _rsi(closes),
              "high_52w": None, "low_52w": None, "from_high": None, "from_low": None}
    for length in (20, 50, 200):
        if len(closes) >= length:
            result[f"ma{length}"] = sum(closes[-length:]) / length
    if points:
        cutoff = points[-1].d.toordinal() - 365
        year = [p.close for p in points if p.d.toordinal() >= cutoff and p.close > 0]
        if len(year) >= 2:
            high, low = max(year), min(year)
            result.update(high_52w=high, low_52w=low,
                          from_high=closes[-1] / high - 1,
                          from_low=closes[-1] / low - 1)
    return result


def build_insights(row: MetricRow, history: PriceHistory, news: dict | None = None,
                   earnings: dict | None = None) -> dict:
    trend = technical(history)
    # Financial and price indicators have different as-of dates. Keep both;
    # do not turn a fiscal end or a guessed quarter into an earnings date.
    report = {
        "fiscal_end": row.fiscal_end.isoformat() if row.fiscal_end else None,
        "next_earnings_date": earnings.get("date") if earnings else None,
        "next_earnings_source": earnings.get("source") if earnings else None,
        "earnings_growth": row.eps_growth_fy1,
        "revenue_growth": row.revenue_growth_yoy,
        "roe": row.roe, "fcf_margin": row.fcf_margin,
        "source": row.sec_source,
    }
    risk = {"drawdown_52w": row.drawdown_52w,
            "debt_to_assets": row.debt_to_assets,
            "beta": row.beta, "market_source": row.market_source}
    def evidence(fields: tuple[str, ...], invert: bool = False) -> dict:
        values = [getattr(row, name) for name in fields if getattr(row, name) is not None]
        # 1..10 peer percentiles mapped to 1..7; risk reverses the direction.
        score = (1 + (sum(values) / len(values) - 1) * 6 / 9) if values else None
        return {"score": 8 - score if invert and score is not None else score,
                "count": len(values), "total": len(fields)}

    facets = {
        "profit": evidence(("z_roe", "z_operating_margin")),
        "growth": evidence(("z_growth_yoy", "z_revenue_cagr")),
        "valuation": evidence(("z_forward_pe", "z_price_sales", "z_ev_ebitda")),
        "recent": evidence(("z_return_3m", "z_drawdown")),
        "risk": evidence(("z_drawdown", "z_debt_assets", "z_beta"), invert=True),
    }
    return {"ticker": row.ticker, "return_3m": row.return_3m,
            "technical": trend, "report": report,
            "risk": risk, "facets": facets,
            "news": news or {"articles": [], "temperature": None,
                                           "source": "Google News RSS"}}
