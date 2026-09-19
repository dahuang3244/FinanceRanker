"""Per-company detail CSV — mirrors the workbook's `Stock Detail` sheet.

Where `csv_export.py` emits one flat row per ticker (good for diffing against
`Data Cache`), this module emits a *single company* review as named blocks:

    ## Meta / ## Dimension scores / ## Metrics / ## Calculation inputs

Each metric is shown against the peer median with its within-group rank, its
1–10 score and its weight group, which is what the `Stock Detail` sheet shows.
"""

from __future__ import annotations

import csv
import io
from statistics import median
from typing import Any

from app.models import MetricRow

# ---------------------------------------------------------------- shape of a row
# (block, label, attribute, kind, better-is-higher, weight group)
# `kind` drives formatting: ratio -> %, multiple -> x, money -> $, num -> plain.
MetricSpec = tuple[str, str, str, str, bool, str]

DIMENSIONS: list[tuple[str, str, str, list[MetricSpec]]] = [
    ("growth", "Growth", "score_growth", [
        ("Growth", "Revenue growth YoY", "revenue_growth_yoy", "ratio", True, "Growth"),
        ("Growth", "Revenue CAGR 5Y", "revenue_cagr_5y", "ratio", True, "Growth"),
        ("Growth", "FY1 EPS growth (quote estimate)", "eps_growth_fy1", "ratio", True, "Growth"),
        ("Growth", "GAAP EPS CAGR 5Y", "eps_cagr_5y", "ratio", True, "Growth"),
    ]),
    ("profitability", "Profitability", "score_profitability", [
        ("Profitability", "Gross margin", "gross_margin", "ratio", True, "Profitability"),
        ("Profitability", "Operating margin", "operating_margin", "ratio", True, "Profitability"),
        ("Profitability", "Net margin", "net_margin", "ratio", True, "Profitability"),
        ("Profitability", "ROE", "roe", "ratio", True, "Profitability"),
        ("Profitability", "ROIC", "roic", "ratio", True, "Profitability"),
    ]),
    ("cash", "Cash quality", "score_cash", [
        ("Cash quality", "FCF margin", "fcf_margin", "ratio", True, "Cash quality"),
        ("Cash quality", "FCF yield", "fcf_yield", "ratio", True, "Cash quality"),
        ("Cash quality", "Cash conversion", "ocf_to_net_income", "multiple", True, "Cash quality"),
    ]),
    ("valuation", "Valuation", "score_valuation", [
        ("Valuation", "Forward 12m P/E", "forward_pe", "multiple", False, "Valuation"),
        ("Valuation", "Price / sales", "price_to_sales", "multiple", False, "Valuation"),
        ("Valuation", "EV / EBITDA", "ev_to_ebitda", "multiple", False, "Valuation"),
        ("Valuation", "Price / book", "price_to_book", "multiple", False, "Valuation"),
        ("Valuation", "Price / FCF", "price_to_fcf", "multiple", False, "Valuation"),
    ]),
    ("market", "Market / risk", "score_market", [
        ("Market / risk", "1Y return", "return_1y", "ratio", True, "Market / risk"),
        ("Market / risk", "Debt / assets", "debt_to_assets", "ratio", False, "Market / risk"),
        ("Market / risk", "52W drawdown", "drawdown_52w", "ratio", True, "Market / risk"),
        ("Market / risk", "Beta", "beta", "num", False, "Market / risk"),
    ]),
]

# inputs shown under "Calculation inputs (latest annual filing)"
INPUTS: list[tuple[str, str, str]] = [
    ("Revenue FY0", "revenue_fy0", "money"),
    ("Revenue FY-1", "revenue_fy_minus_1", "money"),
    ("Market cap", "market_cap", "money"),
    ("Debt", "debt_fy0", "money"),
    ("Cash", "cash_fy0", "money"),
    ("Operating income", "operating_income_fy0", "money"),
    ("D&A", "da_fy0", "money"),
    ("Operating cash flow", "ocf_fy0", "money"),
    ("Capital expenditure", "capex_fy0", "money"),
    ("Free cash flow", "fcf_fy0", "money"),
    ("Total assets", "total_assets_fy0", "money"),
    ("Equity", "equity_fy0", "money"),
    ("Diluted shares", "diluted_shares_fy0", "money"),
    ("Share compensation", "sbc_fy0", "money"),
    ("Effective tax rate", "effective_tax_rate", "ratio"),
    ("GAAP diluted EPS", "gaap_eps", "num"),
    ("Model adjusted EPS", "model_adjusted_eps", "num"),
]


# ------------------------------------------------------------------ statistics
def _population(rows: list[MetricRow], attr: str) -> list[float]:
    """Scoring population for a metric: eligible rows when possible."""
    eligible = [getattr(r, attr) for r in rows if r.rank_eligible]
    eligible = [v for v in eligible if v is not None]
    if eligible:
        return eligible
    return [v for v in (getattr(r, attr) for r in rows) if v is not None]


def _peer_median(rows: list[MetricRow], attr: str) -> float | None:
    values = _population(rows, attr)
    return median(values) if values else None


def _within_group_rank(rows: list[MetricRow], attr: str, value: float | None, higher_better: bool) -> int | None:
    """1 = best inside the peer group (mirrors Stock Detail column D)."""
    if value is None:
        return None
    values = _population(rows, attr)
    if not values:
        return None
    if higher_better:
        better = sum(1 for v in values if v > value)
    else:
        better = sum(1 for v in values if v < value)
    return better + 1


def _format(value: Any, kind: str) -> str:
    if value is None:
        return ""
    if kind == "ratio":
        return f"{value * 100:.1f}%"
    if kind == "multiple":
        return f"{value:.1f}x"
    if kind == "money":
        return f"{value:.0f}"
    return f"{value:.2f}"


# --------------------------------------------------------------------- builder
def _detail_sections(rows: list[MetricRow], row: MetricRow) -> list[tuple[str, list[list[str]]]]:
    """The four CSV blocks, as (title, table) pairs — one builder for both formats."""
    meta = [
        ["Ticker", row.ticker],
        ["Company", row.company or ""],
        ["Currency", row.currency],
        ["Fiscal year end", row.fiscal_end.isoformat() if row.fiscal_end else ""],
        ["Peer group size", str(len(rows))],
        ["Overall score (1-10)", "" if row.score_overall is None else f"{row.score_overall:.2f}"],
        ["Peer rank", "" if row.rank is None else str(row.rank)],
        ["Profile", row.profile or ""],
        ["Metrics present", f"{row.data_coverage}/21"],
        ["Rank eligible", "yes" if row.rank_eligible else "no"],
        ["Last refresh", row.fetched_at.strftime("%Y-%m-%d %H:%M:%S") if row.fetched_at else ""],
        ["Fundamentals source", row.sec_source or ""],
        ["Market source", row.market_source or ""],
        ["Notes", row.notes or ""],
    ]

    scores = [["Dimension", "Score (1-10)", "Weight group"]]
    scores.append(["Growth", _num(row.score_growth), "Growth"])
    scores.append(["Profitability", _num(row.score_profitability), "Profitability"])
    scores.append(["Cash quality", _num(row.score_cash), "Cash quality"])
    scores.append(["Valuation", _num(row.score_valuation), "Valuation"])
    scores.append(["Market / risk", _num(row.score_market), "Market / risk"])
    scores.append(["Overall", _num(row.score_overall), "Weighted 25/25/20/20/10"])

    metrics = [["Metric", "Stock", "Peer median", "Within-group rank", "Score", "Better direction", "Weight group"]]
    for _, _, _, specs in DIMENSIONS:
        for _, label, attr, kind, higher, group in specs:
            value = getattr(row, attr, None)
            metrics.append([
                label,
                _format(value, kind),
                _format(_peer_median(rows, attr), kind),
                "" if (rank := _within_group_rank(rows, attr, value, higher)) is None else str(rank),
                "" if value is None else _num(getattr(row, "z_" + _z_suffix(attr), None)),
                "Higher" if higher else "Lower",
                group,
            ])

    inputs = [["Input", "Selected stock", "Peer median"]]
    for label, attr, kind in INPUTS:
        inputs.append([
            label,
            _format(getattr(row, attr, None), kind),
            _format(_peer_median(rows, attr), kind),
        ])

    return [("Meta", meta), ("Dimension scores", scores), ("Metrics", metrics), ("Calculation inputs", inputs)]


_Z_SUFFIX = {
    "revenue_growth_yoy": "growth_yoy",
    "revenue_cagr_5y": "revenue_cagr",
    "eps_growth_fy1": "eps_growth_fy1",
    "eps_cagr_5y": "eps_cagr",
    "gross_margin": "gross_margin",
    "operating_margin": "operating_margin",
    "net_margin": "net_margin",
    "roe": "roe",
    "roic": "roic",
    "fcf_margin": "fcf_margin",
    "fcf_yield": "fcf_yield",
    "ocf_to_net_income": "cash_conversion",
    "forward_pe": "forward_pe",
    "price_to_sales": "price_sales",
    "ev_to_ebitda": "ev_ebitda",
    "price_to_book": "price_book",
    "price_to_fcf": "price_fcf",
    "return_1y": "return_1y",
    "debt_to_assets": "debt_assets",
    "drawdown_52w": "drawdown",
    "beta": "beta",
}


def _z_suffix(attr: str) -> str:
    return _Z_SUFFIX.get(attr, attr)


def _num(value: Any) -> str:
    return "" if value is None else f"{value:.2f}"


# ------------------------------------------------------------------- rendering
def detail_rows_to_csv(rows: list[MetricRow], row: MetricRow) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for index, (title, table) in enumerate(_detail_sections(rows, row)):
        if index:
            writer.writerow([])
        writer.writerow([f"## {title}"])
        writer.writerows(table)
    return buffer.getvalue()


def detail_rows_to_xlsx_block(rows: list[MetricRow], row: MetricRow) -> list[tuple[str, list[list[str]]]]:
    """Same blocks, for callers that write sheets instead of text."""
    return _detail_sections(rows, row)


def find_row(rows: list[MetricRow], ticker: str) -> MetricRow | None:
    wanted = ticker.strip().upper()
    return next((r for r in rows if r.ticker == wanted), None)
