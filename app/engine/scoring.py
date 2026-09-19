"""Cross-sectional scoring.

Reproduces the `Scoring` sheet: each metric is converted to a 1..10 percentile
z-score across the peer set, the 21 metrics are averaged into five components,
and the components are combined with the weights in `Scoring!AR39:AV39`
(25/25/20/20/10 by default).
"""

from __future__ import annotations

import logging

from app.config import settings
from app.models import MetricRow

log = logging.getLogger(__name__)

# (metric attribute, component, higher_is_better)
# This is the scoring universe, in the same order as Scoring!W..AQ.
METRICS: list[tuple[str, str, bool]] = [
    ("revenue_growth_yoy", "growth", True),
    ("revenue_cagr_5y", "growth", True),
    ("eps_growth_fy1", "growth", True),
    ("eps_cagr_5y", "growth", True),
    ("gross_margin", "profitability", True),
    ("operating_margin", "profitability", True),
    ("net_margin", "profitability", True),
    ("roe", "profitability", True),
    ("roic", "profitability", True),
    ("fcf_margin", "cash", True),
    ("fcf_yield", "cash", True),
    ("ocf_to_net_income", "cash", True),
    ("forward_pe", "valuation", False),
    ("price_to_sales", "valuation", False),
    ("ev_to_ebitda", "valuation", False),
    ("price_to_book", "valuation", False),
    ("price_to_fcf", "valuation", False),
    ("return_1y", "market", True),
    ("debt_to_assets", "market", False),
    ("drawdown_52w", "market", True),
    ("beta", "market", False),
]

Z_FIELDS: dict[str, str] = {
    "revenue_growth_yoy": "z_growth_yoy",
    "revenue_cagr_5y": "z_revenue_cagr",
    "eps_growth_fy1": "z_eps_growth_fy1",
    "eps_cagr_5y": "z_eps_cagr",
    "gross_margin": "z_gross_margin",
    "operating_margin": "z_operating_margin",
    "net_margin": "z_net_margin",
    "roe": "z_roe",
    "roic": "z_roic",
    "fcf_margin": "z_fcf_margin",
    "fcf_yield": "z_fcf_yield",
    "ocf_to_net_income": "z_cash_conversion",
    "forward_pe": "z_forward_pe",
    "price_to_sales": "z_price_sales",
    "ev_to_ebitda": "z_ev_ebitda",
    "price_to_book": "z_price_book",
    "price_to_fcf": "z_price_fcf",
    "return_1y": "z_return_1y",
    "debt_to_assets": "z_debt_assets",
    "drawdown_52w": "z_drawdown",
    "beta": "z_beta",
}

COMPONENT_FIELDS = {
    "growth": "score_growth",
    "profitability": "score_profitability",
    "cash": "score_cash",
    "valuation": "score_valuation",
    "market": "score_market",
}

# Minimum observations required before a component is scored at all, mirroring
# the workbook's eligibility rule.
MIN_PER_COMPONENT = {
    "growth": 2,
    "profitability": 2,
    "cash": 2,
    "valuation": 2,
    "market": 2,
}
MIN_TOTAL_METRICS = 12
MIN_TOTAL_COVERAGE = 21


def compute_coverage(row: MetricRow) -> int:
    """Number of populated scoring metrics (mirrors Scoring!AY)."""
    return sum(1 for attr, _, _ in METRICS if getattr(row, attr, None) is not None)


def _percentile_score(value: float, population: list[float], higher_is_better: bool) -> float | None:
    """1..10 score by rank, matching the workbook's COUNTIFS-based formula.

    score = 1 + 9 * (#values strictly below) / (n - 1)        when higher is better
    score = 10 - 9 * (#values strictly below) / (n - 1)       when lower is better
    """
    n = len(population)
    if n < 2:
        return None
    below = sum(1 for v in population if v < value)
    frac = below / (n - 1)
    score = 1.0 + 9.0 * frac if higher_is_better else 10.0 - 9.0 * frac
    return round(score, 4)


def score_peers(rows: list[MetricRow]) -> list[MetricRow]:
    """Assign z-scores, component scores and the weighted overall score in place."""
    if not rows:
        return rows

    for attr, component, higher_better in METRICS:
        population = [
            getattr(r, attr) for r in rows if getattr(r, attr) is not None
        ]
        if len(population) < 2:
            continue
        z_field = Z_FIELDS[attr]
        for row in rows:
            value = getattr(row, attr, None)
            if value is None:
                continue
            setattr(row, z_field, _percentile_score(value, population, higher_better))

    for row in rows:
        component_scores: dict[str, float] = {}
        for component, field in COMPONENT_FIELDS.items():
            members = [
                getattr(row, Z_FIELDS[attr])
                for attr, comp, _ in METRICS
                if comp == component and getattr(row, Z_FIELDS[attr], None) is not None
            ]
            if len(members) < MIN_PER_COMPONENT[component]:
                continue
            score = sum(members) / len(members)
            setattr(row, field, round(score, 4))
            component_scores[component] = score

        row.data_coverage = compute_coverage(row)

        if row.data_coverage < MIN_TOTAL_METRICS or len(component_scores) < len(COMPONENT_FIELDS):
            row.rank_eligible = False
            continue

        weights = {
            "growth": settings.w_growth,
            "profitability": settings.w_profitability,
            "cash": settings.w_cash,
            "valuation": settings.w_valuation,
            "market": settings.w_market,
        }
        total_weight = sum(weights[c] for c in component_scores)
        if total_weight <= 0:
            row.rank_eligible = False
            continue

        overall = sum(component_scores[c] * weights[c] for c in component_scores) / total_weight
        row.score_overall = round(overall, 4)
        row.rank_eligible = True
        row.profile = _profile(overall)

    _assign_ranks(rows)
    return rows


def _profile(overall: float) -> str:
    if overall >= 8:
        return "Leading"
    if overall >= 6.5:
        return "Strong"
    if overall >= 5:
        return "Average"
    if overall >= 3.5:
        return "Weak"
    return "Lagging"


def _assign_ranks(rows: list[MetricRow]) -> None:
    eligible = sorted(
        (r for r in rows if r.rank_eligible and r.score_overall is not None),
        key=lambda r: r.score_overall,
        reverse=True,
    )
    for position, row in enumerate(eligible, start=1):
        row.rank = position
    for row in rows:
        if not row.rank_eligible:
            row.rank = None
