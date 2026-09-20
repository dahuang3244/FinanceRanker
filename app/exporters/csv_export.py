"""CSV export.

The sheet is organised the way the workbook is: named sections in the same
order as the `Data Cache` (A..AT) plus the AZ..BS filing inputs, with the
scoring block last. Section titles are emitted as `## Section` rows so the file
stays readable in a text editor and still splits cleanly in a spreadsheet.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from pathlib import Path

from app.models import MetricRow

# ---------------------------------------------------------------- shape of a row
# (section, CSV header, row attribute)
Column = tuple[str, str, str]

COLUMNS: list[Column] = [
    # --- identity / provenance ---
    ("Identity", "Ticker", "ticker"),
    ("Identity", "Company", "company"),
    ("Identity", "Currency", "currency"),
    ("Identity", "Last refresh", "fetched_at"),
    ("Identity", "Status", "status"),
    # --- market ---
    ("Market", "Price", "price"),
    ("Market", "52W high", "high_52w"),
    ("Market", "52W low", "low_52w"),
    ("Market", "Market cap", "market_cap"),
    # --- returns ---
    ("Returns", "1M return", "return_1m"),
    ("Returns", "3M return", "return_3m"),
    ("Returns", "6M return", "return_6m"),
    ("Returns", "1Y return", "return_1y"),
    ("Returns", "YTD return", "return_ytd"),
    ("Returns", "3Y return", "return_3y"),
    ("Returns", "SPY 3M return", "benchmark_return_3m"),
    ("Returns", "SPY 6M return", "benchmark_return_6m"),
    ("Returns", "SPY 1Y return", "benchmark_return_1y"),
    ("Returns", "1M excess vs SPY", "excess_return_1m"),
    ("Returns", "3M excess vs SPY", "excess_return_3m"),
    ("Returns", "6M excess vs SPY", "excess_return_6m"),
    ("Returns", "1Y excess vs SPY", "excess_return_1y"),
    ("Returns", "YTD excess vs SPY", "excess_return_ytd"),
    # --- risk ---
    ("Risk", "Volatility (ann.)", "volatility"),
    ("Risk", "Downside deviation (ann.)", "downside_deviation"),
    ("Risk", "Sharpe ratio", "sharpe_ratio"),
    ("Risk", "Sortino ratio", "sortino_ratio"),
    ("Risk", "Beta (full history)", "beta"),
    ("Risk", "Beta (1Y)", "beta_1y"),
    ("Risk", "Beta published", "beta_published"),
    ("Risk", "Max drawdown 1Y", "max_drawdown_1y"),
    ("Risk", "52W drawdown", "drawdown_52w"),
    ("Risk", "Risk observations (days)", "risk_obs_days"),
    ("Risk", "Risk-free rate used", "risk_free_rate"),
    ("Risk", "Price basis", "market_price_basis"),
    # --- consensus ---
    ("Consensus", "Analyst target", "analyst_target"),
    ("Consensus", "Analyst target median", "analyst_target_median"),
    ("Consensus", "Analyst target high", "analyst_target_high"),
    ("Consensus", "Analyst target low", "analyst_target_low"),
    ("Consensus", "Analyst opinions", "analyst_count"),
    ("Consensus", "Recommendation", "analyst_recommendation"),
    ("Consensus", "Analyst upside", "analyst_upside"),
    # --- growth ---
    ("Growth", "Revenue FY0", "revenue_fy0"),
    ("Growth", "Revenue growth YoY", "revenue_growth_yoy"),
    ("Growth", "Revenue CAGR 3Y", "revenue_cagr_3y"),
    ("Growth", "Revenue CAGR 5Y", "revenue_cagr_5y"),
    ("Growth", "Revenue FY1 estimate", "revenue_fy1_estimate"),
    ("Growth", "Revenue growth FY1", "revenue_growth_fy1"),
    ("Growth", "EPS FY1 estimate", "eps_fy1_estimate"),
    ("Growth", "EPS growth FY1 (estimate)", "eps_growth_fy1"),
    ("Growth", "EPS growth YoY (actual)", "eps_growth_yoy"),
    ("Growth", "GAAP EPS CAGR 5Y", "eps_cagr_5y"),
    ("Growth", "Net income growth YoY", "net_income_growth_yoy"),
    ("Growth", "Gross profit growth YoY", "gross_profit_growth_yoy"),
    ("Growth", "FCF growth YoY", "fcf_growth_yoy"),
    # --- profitability ---
    ("Profitability", "Gross margin", "gross_margin"),
    ("Profitability", "Operating margin", "operating_margin"),
    ("Profitability", "Net margin", "net_margin"),
    ("Profitability", "ROE", "roe"),
    ("Profitability", "ROA", "roa"),
    ("Profitability", "ROIC", "roic"),
    ("Profitability", "Asset turnover", "asset_turnover"),
    ("Profitability", "Operating leverage", "operating_leverage"),
    # --- cash quality ---
    ("Cash quality", "FCF FY0", "fcf_fy0"),
    ("Cash quality", "FCF margin", "fcf_margin"),
    ("Cash quality", "FCF yield", "fcf_yield"),
    ("Cash quality", "OCF / net income", "ocf_to_net_income"),
    ("Cash quality", "Debt / assets", "debt_to_assets"),
    ("Cash quality", "Capex intensity", "capex_intensity"),
    ("Cash quality", "Cash / assets", "cash_to_assets"),
    ("Cash quality", "SBC / revenue", "sbc_pct_revenue"),
    ("Cash quality", "Net debt FY0", "net_debt_fy0"),
    ("Cash quality", "EBITDA FY0", "ebitda_fy0"),
    # --- per-share bridge ---
    ("Per-share bridge", "GAAP diluted EPS", "gaap_eps"),
    ("Per-share bridge", "SBC adj/share", "sbc_adj_share"),
    ("Per-share bridge", "Restructuring adj/share", "restructuring_adj_share"),
    ("Per-share bridge", "Amortization adj/share", "amortization_adj_share"),
    ("Per-share bridge", "Other adj/share", "other_adj_share"),
    ("Per-share bridge", "Tax adj/share", "tax_adj_share"),
    ("Per-share bridge", "Model adjusted EPS", "model_adjusted_eps"),
    ("Per-share bridge", "Reported non-GAAP EPS", "reported_non_gaap_eps"),
    ("Per-share bridge", "Selected adjusted EPS", "selected_adjusted_eps"),
    ("Per-share bridge", "Forward 12m EPS", "forward_12m_eps"),
    # --- valuation ---
    ("Valuation", "Forward P/E", "forward_pe"),
    ("Valuation", "PEG", "peg_ratio"),
    ("Valuation", "Price / sales", "price_to_sales"),
    ("Valuation", "EV / sales", "ev_to_sales"),
    ("Valuation", "EV / EBITDA", "ev_to_ebitda"),
    ("Valuation", "Price / book", "price_to_book"),
    ("Valuation", "Price / FCF", "price_to_fcf"),
    ("Valuation", "Price / OCF", "price_to_ocf"),
    ("Valuation", "Net debt / EBITDA", "net_debt_to_ebitda"),
    # --- filing inputs (Data Cache AZ..BS) ---
    ("Filing inputs", "Revenue FY-1", "revenue_fy_minus_1"),
    ("Filing inputs", "Revenue FY-5", "revenue_fy_minus_5"),
    ("Filing inputs", "GAAP EPS FY-1", "gaap_eps_fy_minus_1"),
    ("Filing inputs", "GAAP EPS FY-5", "gaap_eps_fy_minus_5"),
    ("Filing inputs", "Share count", "share_count"),
    ("Filing inputs", "Cash FY0", "cash_fy0"),
    ("Filing inputs", "Debt FY0", "debt_fy0"),
    ("Filing inputs", "Operating income FY0", "operating_income_fy0"),
    ("Filing inputs", "D&A FY0", "da_fy0"),
    ("Filing inputs", "Diluted shares FY0", "diluted_shares_fy0"),
    ("Filing inputs", "Share compensation FY0", "sbc_fy0"),
    ("Filing inputs", "Restructuring FY0", "restructuring_fy0"),
    ("Filing inputs", "Intangible amortization FY0", "amortization_fy0"),
    ("Filing inputs", "Effective tax rate FY0", "effective_tax_rate"),
    ("Filing inputs", "Fiscal year end", "fiscal_end"),
    ("Filing inputs", "Equity FY0", "equity_fy0"),
    ("Filing inputs", "Operating cash flow FY0", "ocf_fy0"),
    ("Filing inputs", "Capital expenditure FY0", "capex_fy0"),
    ("Filing inputs", "Capex basis", "capex_basis"),
    ("Filing inputs", "Debt basis", "debt_basis"),
    ("Filing inputs", "Margin basis", "margin_basis"),
    ("Filing inputs", "Share-count basis", "share_basis"),
    ("Filing inputs", "Total assets FY0", "total_assets_fy0"),
    ("Filing inputs", "Gross profit FY0", "gross_profit_fy0"),
    ("Filing inputs", "Cost of revenue FY0", "cost_of_revenue_fy0"),
    # --- scoring (Scoring sheet) ---
    ("Scoring", "Score growth", "score_growth"),
    ("Scoring", "Score profitability", "score_profitability"),
    ("Scoring", "Score cash quality", "score_cash"),
    ("Scoring", "Score valuation", "score_valuation"),
    ("Scoring", "Score market / risk", "score_market"),
    ("Scoring", "Score overall", "score_overall"),
    ("Scoring", "Peer rank", "rank"),
    ("Scoring", "Profile", "profile"),
    ("Scoring", "Metrics present", "data_coverage"),
    ("Scoring", "Coverage share", "coverage_pct"),
    ("Scoring", "Rank eligible", "rank_eligible"),
    # --- provenance ---
    ("Provenance", "Source quality", "source_quality"),
    ("Provenance", "SEC source", "sec_source"),
    ("Provenance", "Market source", "market_source"),
    ("Provenance", "Non-GAAP source", "non_gaap_source"),
    ("Provenance", "Notes", "notes"),
]


def sections() -> list[str]:
    """Section names, in file order."""
    out: list[str] = []
    for section, _, _ in COLUMNS:
        if not out or out[-1] != section:
            out.append(section)
    return out


def _fmt(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, float):
        # `repr` round-trips a float exactly; unlike %g it never falls back to
        # scientific notation (which Excel mis-reads for large money values).
        return repr(value)
    return value


def rows_to_csv(
    rows: list[MetricRow], *, include_header: bool = True, include_sections: bool = True
) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")

    if include_header:
        writer.writerow([f"## FinanceRanker · {len(rows)} tickers · {datetime.now():%Y-%m-%d %H:%M}"])
        if include_sections:
            for section in sections():
                writer.writerow([f"## {section}"])
        writer.writerow([
            f"{section} · {header}" if include_sections else header
            for section, header, _ in COLUMNS
        ])

    for row in rows:
        writer.writerow([_fmt(getattr(row, attr, None)) for _, _, attr in COLUMNS])
    return buffer.getvalue()


def write_csv(rows: list[MetricRow], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig so Excel on Windows opens Chinese/Unicode company names correctly.
    path.write_text(rows_to_csv(rows), encoding="utf-8-sig")
    return path
