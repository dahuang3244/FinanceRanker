"""Cross-sectional scoring.

Each metric is converted to a 1..10 peer percentile score. The 21 ranked
metrics form five components, combined with configurable weights
(growth/profit/cash/valuation/market: 20/15/20/25/20 by default).
"""

from __future__ import annotations

import logging

from app.config import settings
from app.models import MetricRow

log = logging.getLogger(__name__)

# (metric attribute, component, higher_is_better)
# This is the scoring universe, in the same order as Scoring!W..AQ.
#
# The `market` component is deliberately split across two ideas: what the stock
# returned (raw and versus SPY) and what it cost in risk to do so. Without the
# risk half, a screen rewards the most volatile name in the peer set simply
# because it moved furthest.
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
    ("debt_to_assets", "cash", False),
    ("forward_pe", "valuation", False),
    ("price_to_sales", "valuation", False),
    ("ev_to_ebitda", "valuation", False),
    ("price_to_book", "valuation", False),
    ("price_to_fcf", "valuation", False),
    # market / performance — absolute and benchmark-relative returns
    ("return_3m", "market", True),
    ("return_6m", "market", True),
    ("return_1y", "market", True),
    ("excess_return_3m", "market", True),
    ("excess_return_6m", "market", True),
    # market / risk — risk-adjusted and drawdown measures
    ("sharpe_ratio", "market", True),
    ("sortino_ratio", "market", True),
    ("volatility", "market", False),
    ("beta_1y", "market", False),
    ("max_drawdown_1y", "market", True),
    ("drawdown_52w", "market", True),
    # market / performance — the 1-year leg, so all three windows agree
    ("excess_return_1y", "market", True),
    # profitability — return on the asset base, alongside return on equity
    ("roa", "profitability", True),
    # cash — how much cash the business spends to earn its revenue
    ("capex_intensity", "cash", False),
]

# Metrics the peer screen reports but deliberately does NOT score.
#
# They are kept out of `METRICS` for a reason worth stating, because the
# detail view used to render a blank "score" for them with no explanation:
# a row that shows no score must be distinguishable from a row that scored
# badly. Each entry carries the reason it is reference-only.
REFERENCE_METRICS: list[tuple[str, str, bool, str]] = [
    # (attribute, component, higher_is_better, why not scored)
    ("return_1m", "market", True, "one month is noise at a peer-comparison horizon"),
    ("return_ytd", "market", True, "part-year window; not comparable across fiscal calendars"),
    ("return_3y", "market", True, "overlaps the 1-year window and spans regimes"),
    ("excess_return_1m", "market", True, "one month is noise at a peer-comparison horizon"),
    ("excess_return_ytd", "market", True, "part-year window; not comparable across fiscal calendars"),
    ("beta", "market", False, "duplicates the scored 1-year beta on a different window"),
    ("downside_deviation", "market", False, "tracked by volatility and Sortino already"),
    ("revenue_cagr_3y", "growth", True, "highly collinear with the scored 5-year CAGR"),
    ("eps_growth_yoy", "growth", True, "complements the scored forward-looking FY1 estimate; kept for cross-checking"),
    ("net_income_growth_yoy", "growth", True, "highly collinear with EPS growth on a stable share count"),
    ("gross_profit_growth_yoy", "growth", True, "unavailable for many filers that tag only a cost line"),
    ("fcf_growth_yoy", "growth", True, "undefined when the prior year's free cash flow is negative"),
    ("asset_turnover", "profitability", True, "size/labour differences dominate across sectors"),
    ("operating_leverage", "profitability", True, "unstable when prior-year operating income is near zero"),
    ("cash_to_assets", "cash", True, "balance-sheet mix, not a quality signal on its own"),
    ("sbc_pct_revenue", "cash", False, "genuinely unavailable for TSM (no SBC tag)"),
    ("ev_to_sales", "valuation", False, "duplicates price/sales once debt is small"),
    ("price_to_ocf", "valuation", False, "duplicates FCF yield for capital-light filers"),
    ("peg_ratio", "valuation", False, "undefined for negative or near-zero growth"),
    ("net_debt_to_ebitda", "valuation", False, "unavailable for JNJ; net cash makes it negative"),
]


# How each metric is rendered and labelled. `kind` drives formatting in the UI
# (ratio -> %, multiple -> x, money -> currency, num -> plain) and the label
# keys live in the frontend dictionary. This lives beside `METRICS` on purpose:
# the company detail view used to carry its own parallel list, and the two
# drifted — the UI rendered `beta` and `excess_return_1y` with an empty score
# column because the backend no longer scored them and nothing said so.
METRIC_PRESENTATION: dict[str, str] = {
    # growth
    "revenue_growth_yoy": "ratio", "revenue_cagr_5y": "ratio",
    "revenue_cagr_3y": "ratio", "eps_growth_fy1": "ratio", "eps_cagr_5y": "ratio",
    "eps_growth_yoy": "ratio", "net_income_growth_yoy": "ratio",
    "gross_profit_growth_yoy": "ratio", "fcf_growth_yoy": "ratio",
    # profitability
    "gross_margin": "ratio", "operating_margin": "ratio", "net_margin": "ratio",
    "roe": "ratio", "roic": "ratio", "roa": "ratio",
    "asset_turnover": "num", "operating_leverage": "num",
    # cash
    "fcf_margin": "ratio", "fcf_yield": "ratio", "ocf_to_net_income": "multiple",
    "debt_to_assets": "ratio", "capex_intensity": "ratio", "cash_to_assets": "ratio",
    "sbc_pct_revenue": "ratio",
    # valuation
    "forward_pe": "multiple", "price_to_sales": "multiple", "ev_to_ebitda": "multiple",
    "price_to_book": "multiple", "price_to_fcf": "multiple", "price_to_ocf": "multiple",
    "ev_to_sales": "multiple", "peg_ratio": "multiple", "net_debt_to_ebitda": "multiple",
    # market
    "return_1m": "ratio", "return_3m": "ratio", "return_6m": "ratio",
    "return_1y": "ratio", "return_ytd": "ratio", "return_3y": "ratio",
    "excess_return_1m": "ratio", "excess_return_3m": "ratio",
    "excess_return_6m": "ratio", "excess_return_1y": "ratio",
    "volatility": "ratio", "downside_deviation": "ratio",
    "sharpe_ratio": "num", "sortino_ratio": "num", "beta": "num", "beta_1y": "num",
    "max_drawdown_1y": "ratio", "drawdown_52w": "ratio",
}


# Canonical English display names, used by the CSV/Excel exports and as the
# fallback label in the UI. Keeping them next to the metric definitions means a
# new metric cannot be added without a name attached to it.
METRIC_LABELS: dict[str, str] = {
    "revenue_growth_yoy": "Revenue growth YoY",
    "revenue_cagr_5y": "Revenue CAGR 5Y",
    "revenue_cagr_3y": "Revenue CAGR 3Y",
    "eps_growth_fy1": "FY1 EPS growth",
    "eps_cagr_5y": "GAAP EPS CAGR 5Y",
    "eps_growth_yoy": "EPS growth YoY",
    "net_income_growth_yoy": "Net income growth YoY",
    "gross_profit_growth_yoy": "Gross profit growth YoY",
    "fcf_growth_yoy": "FCF growth YoY",
    "gross_margin": "Gross margin",
    "operating_margin": "Operating margin",
    "net_margin": "Net margin",
    "roe": "ROE",
    "roic": "ROIC",
    "roa": "ROA",
    "asset_turnover": "Asset turnover",
    "operating_leverage": "Operating leverage",
    "fcf_margin": "FCF margin",
    "fcf_yield": "FCF yield",
    "ocf_to_net_income": "Cash conversion",
    "debt_to_assets": "Debt / assets",
    "capex_intensity": "Capex intensity",
    "cash_to_assets": "Cash / assets",
    "sbc_pct_revenue": "SBC / revenue",
    "forward_pe": "Forward 12m P/E",
    "price_to_sales": "Price / sales",
    "ev_to_ebitda": "EV / EBITDA",
    "price_to_book": "Price / book",
    "price_to_fcf": "Price / FCF",
    "price_to_ocf": "Price / OCF",
    "ev_to_sales": "EV / sales",
    "peg_ratio": "PEG ratio",
    "net_debt_to_ebitda": "Net debt / EBITDA",
    "return_1m": "1M return",
    "return_3m": "3M return",
    "return_6m": "6M return",
    "return_1y": "1Y return",
    "return_ytd": "YTD return",
    "return_3y": "3Y return",
    "excess_return_1m": "1M excess vs SPY",
    "excess_return_3m": "3M excess vs SPY",
    "excess_return_6m": "6M excess vs SPY",
    "excess_return_1y": "1Y excess vs SPY",
    "excess_return_ytd": "YTD excess vs SPY",
    "volatility": "Volatility (ann.)",
    "downside_deviation": "Downside deviation",
    "sharpe_ratio": "Sharpe ratio",
    "sortino_ratio": "Sortino ratio",
    "beta": "Beta (full history)",
    "beta_1y": "Beta (1Y)",
    "max_drawdown_1y": "Max drawdown 1Y",
    "drawdown_52w": "52W drawdown",
}


def reference_attrs() -> set[str]:
    return {attr for attr, _, _, _ in REFERENCE_METRICS}


def metric_catalog() -> list[dict]:
    """Every metric the screen displays, with how it is treated and rendered.

    This is the single source of truth behind the UI's metric blocks, exposed at
    `/api/metrics`. It exists because the frontend previously carried its own
    hand-written list keyed by metric name; when the scoring universe changed,
    the UI kept rendering metrics the backend had stopped scoring and showed an
    empty score next to them with no explanation.
    """
    # `order` is the position within each group, so a consumer can simply sort by
    # (not scored, order) without knowing how many metrics are in the other group.
    out: list[dict] = []
    for order, (attr, component, higher) in enumerate(METRICS):
        out.append({
            "attr": attr, "component": component, "higher_is_better": higher,
            "kind": METRIC_PRESENTATION.get(attr, "num"),
            "label": METRIC_LABELS.get(attr, attr),
            "scored": True, "order": order, "note": None,
        })
    for order, (attr, component, higher, why) in enumerate(REFERENCE_METRICS):
        out.append({
            "attr": attr, "component": component, "higher_is_better": higher,
            "kind": METRIC_PRESENTATION.get(attr, "num"),
            "label": METRIC_LABELS.get(attr, attr),
            "scored": False, "order": order, "note": why,
        })
    return out

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
    "return_3m": "z_return_3m",
    "return_6m": "z_return_6m",
    "excess_return_3m": "z_excess_return_3m",
    "excess_return_6m": "z_excess_return_6m",
    "debt_to_assets": "z_debt_assets",
    "drawdown_52w": "z_drawdown",
    "beta_1y": "z_beta_1y",
    "sharpe_ratio": "z_sharpe",
    "sortino_ratio": "z_sortino",
    "volatility": "z_volatility",
    "max_drawdown_1y": "z_max_drawdown",
    "excess_return_1y": "z_excess_return_1y",
    "roa": "z_roa",
    "capex_intensity": "z_capex_intensity",
}

COMPONENT_FIELDS = {
    "growth": "score_growth",
    "profitability": "score_profitability",
    "cash": "score_cash",
    "valuation": "score_valuation",
    "market": "score_market",
}

# Minimum observations required before a component is scored at all.
MIN_PER_COMPONENT = {
    "growth": 2,
    "profitability": 2,
    "cash": 2,
    "valuation": 2,
    "market": 2,
}
# A row is ranked only when the *majority* of the scoring universe is present
# and every component can be scored. Because a peer set now covers 21 metrics,
# the absolute floor stays where it was: raising it would drop legitimate
# partial filings rather than improve comparability.
MIN_TOTAL_METRICS = 12
MIN_TOTAL_COVERAGE = len(METRICS)

# Bump whenever the scoring universe changes shape (metrics added or removed).
# Snapshots are stamped with this number so a screen can tell "this figure is
# blank because the source had nothing" apart from "this snapshot predates the
# metric entirely".
METRICS_VERSION = 3


def compute_coverage(row: MetricRow) -> int:
    """Number of populated scoring metrics (mirrors Scoring!AY)."""
    return sum(1 for attr, _, _ in METRICS if getattr(row, attr, None) is not None)


def coverage_pct(coverage: int) -> float:
    """Populated share of the scoring universe, as a 0..1 fraction."""
    return round(coverage / len(METRICS), 4) if METRICS else 0.0


def apply_coverage(row: MetricRow) -> int:
    """Set both coverage figures together so they can never drift apart."""
    row.data_coverage = compute_coverage(row)
    row.coverage_pct = coverage_pct(row.data_coverage)
    return row.data_coverage


def _percentile_score(value: float, population: list[float], higher_is_better: bool) -> float | None:
    """1..10 score by rank, matching the workbook's COUNTIFS-based formula.

    score = 1 + 9 * (#values strictly below) / (n - 1)        when higher is better
    score = 10 - 9 * (#values strictly below) / (n - 1)       when lower is better

    All-equal inputs return the neutral midrank (5.5). Whether such a metric
    should be scored at all is decided by the caller: `score_peers` skips a
    metric on which every peer is identical, because it separates nobody.
    """
    n = len(population)
    if n < 2:
        return None
    below = sum(1 for v in population if v < value)
    equal = sum(1 for v in population if v == value)
    # Midrank makes identical companies receive the same neutral score;
    # previously every equal value received 1 (or 10 in reverse).
    percentile = (below + (equal - 1) / 2) / (n - 1)
    score = 1.0 + 9.0 * percentile if higher_is_better else 10.0 - 9.0 * percentile
    return round(score, 4)


def score_peers(rows: list[MetricRow]) -> list[MetricRow]:
    """Assign z-scores, component scores and the weighted overall score in place."""
    if not rows:
        return rows

    for attr, component, higher_better in METRICS:
        # Negative earnings / equity ratios are not cheap valuations.
        population = [
            getattr(r, attr) for r in rows
            if getattr(r, attr) is not None
            and (component != "valuation" or getattr(r, attr) > 0)
        ]
        if len(population) < 2:
            continue
        # Every peer identical: the metric separates nobody, so it is left
        # unscored rather than handing all of them the same neutral 5.5, which
        # would still dilute the component average.
        if all(v == population[0] for v in population):
            continue
        z_field = Z_FIELDS[attr]
        for row in rows:
            value = getattr(row, attr, None)
            if value is None or (component == "valuation" and value <= 0):
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

        apply_coverage(row)

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
