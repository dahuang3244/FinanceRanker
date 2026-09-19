"""Metric engine.

Rebuilds every derived figure the workbook computes, but in plain Python instead
of Excel formulas. `Set-V5Formulas` / `Set-V5Formulas`-style logic from
`Refresh_Free_Data.ps1` is the reference; each function below notes the
corresponding `Data Cache` column so the output can be reconciled cell-by-cell.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from app.engine.market import beta, drawdown_from_high, return_over_window
from app.engine.scoring import compute_coverage
from app.models import Fundamentals, MetricRow, PriceHistory, Quote

log = logging.getLogger(__name__)

# Fallback marginal tax rate when the effective rate cannot be derived
# (mirrors the 0.21 default in the PowerShell refresher).
DEFAULT_TAX_RATE = 0.21
MAX_TAX_RATE = 0.40
DEFAULT_BENCHMARK = "SPY"


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def growth(current: float | None, prior: float | None) -> float | None:
    if current is None or prior is None or prior <= 0 or current <= 0:
        return None
    return current / prior - 1.0


def cagr(current: float | None, base: float | None, periods: int) -> float | None:
    if current is None or base is None or periods <= 0 or current <= 0 or base <= 0:
        return None
    return (current / base) ** (1.0 / periods) - 1.0


def effective_tax_rate(fund: Fundamentals) -> float:
    tax = fund.tax_provision.latest() if fund.tax_provision else None
    pretax = fund.pretax_income.latest() if fund.pretax_income else None
    if tax is None or pretax is None or abs(pretax) <= 1:
        return DEFAULT_TAX_RATE
    return min(MAX_TAX_RATE, max(0.0, tax / pretax))


# --------------------------------------------------------------------------- #
# main entry point
# --------------------------------------------------------------------------- #
def compute_row(
    ticker: str,
    *,
    history: PriceHistory,
    quote: Quote,
    fund: Fundamentals,
    benchmark: PriceHistory | None = None,
) -> MetricRow:
    row = MetricRow(
        ticker=ticker,
        currency=quote.currency or fund.currency or "USD",
        fetched_at=datetime.now(),
    )
    row.company = fund.entity_name or quote.name or ticker
    row.fiscal_end = fund.fiscal_end
    row.source_quality = f"{fund.source} annual facts + {history.source} price history"
    row.sec_source = fund.source_url
    row.market_source = history.source
    row.quote_url = quote.source

    # Order matters: the raw filing block resolves the authoritative share
    # count, which the market block then uses to derive market cap. Everything
    # downstream (yields, multiples) depends on that one market-cap value.
    _raw_filing_block(row, fund)
    _market_block(row, history, quote, benchmark)
    _fundamental_block(row, fund)
    _pershare_block(row, fund)
    _estimate_block(row, quote)
    _multiples_block(row)
    apply_ev_ebitda(row)

    # Cross-currency guard. Foreign private issuers (e.g. TSM, a USD ADR filing
    # in TWD) would otherwise produce nonsense ratios such as
    # market cap(USD) / revenue(TWD) or price(USD) / EPS(TWD). Detect the
    # mismatch, drop the affected figures and say so plainly.
    mismatch_note: str | None = None
    if (fund.currency or "USD").upper() != (row.currency or "USD").upper():
        dropped = _drop_cross_currency(row, fund.currency, row.currency)
        mismatch_note = (
            f"Reporting currency {fund.currency} differs from trading currency "
            f"{row.currency} (ADR / foreign private issuer); {dropped} withheld "
            f"because no FX or ADR-ratio conversion is applied"
        )
        row.source_quality = f"{fund.source} annual facts (currency mismatch flagged)"
        row.status = "Partial filing data"

    row.data_coverage = compute_coverage(row)

    # Status/notes are composed last so the currency warning survives.
    if row.status != "Partial filing data" and (
        row.revenue_fy0 is not None and row.gaap_eps is not None and row.operating_income_fy0 is not None
    ):
        row.status = "Refreshed"
        row.notes = (
            f"{fund.source} annual facts + {history.source} price history. "
            "Model EPS adjustments are after-tax per-share estimates; review the reconciliation."
        )
    else:
        missing = [
            label
            for label, value in (
                ("annual revenue", row.revenue_fy0),
                ("annual EPS", row.gaap_eps),
                ("annual operating income", row.operating_income_fy0),
            )
            if value is None
        ]
        parts = [f"Partial {fund.source} coverage"]
        if missing:
            parts.append(f"missing: {', '.join(missing)}")
        if mismatch_note:
            parts.append(mismatch_note)
        row.status = "Partial filing data"
        row.notes = "; ".join(parts) + "."

    return row


# --------------------------------------------------------------------------- #
# market data (Data Cache F..K, AR, AS, AT)
# --------------------------------------------------------------------------- #
def _market_block(
    row: MetricRow,
    history: PriceHistory,
    quote: Quote,
    benchmark: PriceHistory | None,
) -> None:
    price = quote.price or history.last()
    row.price = price

    window = history.window(365)
    closes = [p.close for p in window] or history.closes()
    if closes:
        row.high_52w = max(closes)
        row.low_52w = min(closes)
    row.return_1y = return_over_window(history, 365)
    row.drawdown_52w = drawdown_from_high(price, row.high_52w)

    # Market cap: prefer price x filing share count, else the provider's figure.
    shares = row.share_count or quote.shares_outstanding
    if shares is None and quote.market_cap and price:
        shares = quote.market_cap / price
        row.share_count = shares
    if price is not None and shares:
        row.market_cap = price * shares
    if row.market_cap is None:
        row.market_cap = quote.market_cap

    if benchmark is not None:
        row.beta = beta(history, benchmark)

    row.analyst_target = quote.target_mean_price
    if quote.target_mean_price and price:
        row.analyst_upside = quote.target_mean_price / price - 1.0


# --------------------------------------------------------------------------- #
# fundamentals (Data Cache L..X, T..W, AL, AR)
# --------------------------------------------------------------------------- #
def _fundamental_block(row: MetricRow, fund: Fundamentals) -> None:
    anchor = fund.fiscal_end

    revenue = _series_value(fund.revenue, anchor)
    gross = _series_value(fund.gross_profit, anchor)
    cost = _series_value(fund.cost_of_revenue, anchor)
    operating = _series_value(fund.operating_income, anchor)
    net = _series_value(fund.net_income, anchor)
    equity = _series_value(fund.equity, anchor)
    assets = _series_value(fund.assets, anchor)
    cash = _series_value(fund.cash, anchor)
    ocf = _series_value(fund.operating_cash_flow, anchor)
    capex = _series_value(fund.capex, anchor)
    eps = _series_value(fund.eps_diluted, anchor)

    # Gross profit can be derived when only the cost of revenue is tagged.
    if gross is None and revenue is not None and cost is not None:
        gross = revenue - cost

    row.revenue_fy0 = revenue
    row.gross_margin = safe_div(gross, revenue)
    row.operating_margin = safe_div(operating, revenue)
    row.net_margin = safe_div(net, revenue)
    row.roe = safe_div(net, equity)
    row.gaap_eps = eps

    # Growth: prefer like-for-like period pairs from the same series.
    row.revenue_growth_yoy = _series_growth(fund.revenue, 1)
    row.revenue_cagr_5y = _series_cagr(fund.revenue, 5)
    row.eps_cagr_5y = _series_cagr(fund.eps_diluted, 5)

    fcf = None
    if ocf is not None and capex is not None:
        fcf = ocf - abs(capex)
    row.fcf_fy0 = fcf
    row.fcf_margin = safe_div(fcf, revenue)
    row.fcf_yield = safe_div(fcf, row.market_cap)
    row.ocf_to_net_income = safe_div(ocf, net)

    debt = _total_debt(fund, anchor)
    row.debt_to_assets = safe_div(debt, assets)

    # ROIC = operating income * (1 - tax) / (equity + debt - cash)
    invested = None
    if equity is not None and debt is not None and cash is not None:
        invested = equity + debt - cash
    if operating is not None and invested and invested > 0:
        row.roic = operating * (1.0 - effective_tax_rate(fund)) / invested


def _series_value(series, anchor: date | None) -> float | None:
    """Latest value, aligned to the fiscal anchor when the dates differ."""
    if series is None or not series.points:
        return None
    if anchor is None:
        return series.latest()
    aligned = series.aligned(anchor)
    return aligned if aligned is not None else series.latest()


def _series_at_offset(series, periods: int) -> float | None:
    """Value `periods` fiscal years before the latest, matched by calendar date."""
    if series is None or len(series.points) <= periods:
        return None
    current_end, _ = series.points[0]
    target = date(current_end.year - periods, current_end.month, current_end.day)
    best: tuple[int, float] | None = None
    for end, value in series.points[1:]:
        delta = abs((end - target).days)
        if best is None or delta < best[0]:
            best = (delta, value)
    if best is None or best[0] > 48:
        return None
    return best[1]


def _series_growth(series, periods: int) -> float | None:
    if series is None or len(series.points) <= periods:
        return None
    return growth(series.points[0][1], _series_at_offset(series, periods))


def _series_cagr(series, periods: int) -> float | None:
    if series is None or len(series.points) <= periods:
        return None
    return cagr(series.points[0][1], _series_at_offset(series, periods), periods)


def _total_debt(fund: Fundamentals, anchor: date | None) -> float | None:
    current = _series_value(fund.debt_current, anchor)
    long_term = _series_value(fund.debt_long, anchor)
    if current is None and long_term is None:
        return None
    return (current or 0.0) + (long_term or 0.0)


# --------------------------------------------------------------------------- #
# per-share non-GAAP bridge (Data Cache Y..AG)
# --------------------------------------------------------------------------- #
def _pershare_block(row: MetricRow, fund: Fundamentals) -> None:
    anchor = fund.fiscal_end
    tax_rate = effective_tax_rate(fund)
    shares = _series_value(fund.shares_diluted, anchor)

    row.effective_tax_rate = tax_rate
    row.diluted_shares_fy0 = shares

    if row.gaap_eps is None or not shares:
        row.model_adjusted_eps = row.reported_non_gaap_eps
        row.selected_adjusted_eps = row.reported_non_gaap_eps or row.model_adjusted_eps
        return

    def per_share(series) -> float | None:
        value = _series_value(series, anchor)
        if value is None:
            return None
        return value * (1.0 - tax_rate) / shares

    row.sbc_adj_share = per_share(fund.sbc)
    row.restructuring_adj_share = per_share(fund.restructuring)
    row.amortization_adj_share = per_share(fund.amortization)
    row.tax_adj_share = None

    addbacks = [
        v
        for v in (row.sbc_adj_share, row.restructuring_adj_share, row.amortization_adj_share)
        if v is not None
    ]
    if addbacks:
        row.model_adjusted_eps = row.gaap_eps + sum(addbacks)
    else:
        row.model_adjusted_eps = None

    row.selected_adjusted_eps = row.reported_non_gaap_eps or row.model_adjusted_eps


# --------------------------------------------------------------------------- #
# estimates & multiples (Data Cache AH..AQ)
# --------------------------------------------------------------------------- #
def _estimate_block(row: MetricRow, quote: Quote) -> None:
    row.forward_12m_eps = quote.eps_forward
    row.eps_fy1_estimate = quote.eps_forward or quote.eps_current_year

    base = row.selected_adjusted_eps or row.gaap_eps
    if row.eps_fy1_estimate is not None and base:
        row.eps_growth_fy1 = row.eps_fy1_estimate / base - 1.0

    if quote.forward_pe:
        row.forward_pe = quote.forward_pe
    elif row.price and row.eps_fy1_estimate:
        row.forward_pe = row.price / row.eps_fy1_estimate


def _multiples_block(row: MetricRow) -> None:
    row.price_to_sales = safe_div(row.market_cap, row.revenue_fy0)
    if row.market_cap and row.equity_fy0:
        row.price_to_book = row.market_cap / row.equity_fy0
    if row.market_cap and row.fcf_fy0 and row.fcf_fy0 > 0:
        row.price_to_fcf = row.market_cap / row.fcf_fy0


def apply_ev_ebitda(row: MetricRow) -> None:
    """Needs D&A and the balance-sheet inputs, so it runs after the raw block."""
    if row.market_cap is None or row.debt_fy0 is None or row.cash_fy0 is None:
        return
    if row.operating_income_fy0 is None or row.da_fy0 is None:
        return
    ebitda = row.operating_income_fy0 + row.da_fy0
    if ebitda <= 0:
        return
    enterprise_value = row.market_cap + row.debt_fy0 - row.cash_fy0
    row.ev_to_ebitda = enterprise_value / ebitda


# Metrics that mix a money amount with the trading price / share count and are
# therefore meaningless when the filing currency differs from the trading one.
_CROSS_CURRENCY_FIELDS = (
    "market_cap",
    "fcf_yield",
    "price_to_sales",
    "price_to_book",
    "price_to_fcf",
    "ev_to_ebitda",
    "forward_pe",
    "eps_fy1_estimate",
    "forward_12m_eps",
    "eps_growth_fy1",
    "model_adjusted_eps",
    "selected_adjusted_eps",
    "sbc_adj_share",
    "restructuring_adj_share",
    "amortization_adj_share",
    "analyst_upside",
)


def _drop_cross_currency(row: MetricRow, reporting: str, trading: str) -> str:
    """Clear price-dependent figures and return a human-readable summary."""
    for field in _CROSS_CURRENCY_FIELDS:
        setattr(row, field, None)
    # ROIC / margins are currency-free ratios, so they survive; but ROE and
    # returns that mix the market cap do not. Recompute the pure ratios.
    row.status = f"Partial filing data ({reporting} reporting vs {trading} trading)"
    return "market cap and all price-based multiples"


# --------------------------------------------------------------------------- #
# raw filing inputs (Data Cache AZ..BS) — kept for the Excel export
# --------------------------------------------------------------------------- #
def _raw_filing_block(row: MetricRow, fund: Fundamentals) -> None:
    anchor = fund.fiscal_end

    row.revenue_fy_minus_1 = _series_at_offset(fund.revenue, 1)
    row.revenue_fy_minus_5 = _series_at_offset(fund.revenue, 5)
    row.gaap_eps_fy_minus_1 = _series_at_offset(fund.eps_diluted, 1)
    row.gaap_eps_fy_minus_5 = _series_at_offset(fund.eps_diluted, 5)

    shares = _series_value(fund.shares_diluted, anchor)
    row.share_count = fund.shares_outstanding or shares
    row.share_basis = fund.shares_basis or ("Approximation: annual diluted shares" if shares else "")
    row.diluted_shares_fy0 = shares

    row.cash_fy0 = _series_value(fund.cash, anchor)
    row.debt_fy0 = _total_debt(fund, anchor)
    row.operating_income_fy0 = _series_value(fund.operating_income, anchor)
    row.da_fy0 = _depreciation_amortization(fund, anchor)
    row.equity_fy0 = _series_value(fund.equity, anchor)
    row.ocf_fy0 = _series_value(fund.operating_cash_flow, anchor)
    capex = _series_value(fund.capex, anchor)
    row.capex_fy0 = abs(capex) if capex is not None else None
    row.total_assets_fy0 = _series_value(fund.assets, anchor)
    row.gross_profit_fy0 = _series_value(fund.gross_profit, anchor)
    row.cost_of_revenue_fy0 = _series_value(fund.cost_of_revenue, anchor)
    row.sbc_fy0 = _series_value(fund.sbc, anchor)
    row.restructuring_fy0 = _series_value(fund.restructuring, anchor)
    row.amortization_fy0 = _series_value(fund.amortization, anchor)


def _depreciation_amortization(fund: Fundamentals, anchor: date | None) -> float | None:
    """Prefer a combined D&A tag; otherwise add separate depreciation + amortization.

    `depreciation_amortization` already carries the `Depreciation` fallback tag
    from the provider, so this only needs to add intangibles amortization when
    the combined figure was absent.
    """
    combined = _series_value(fund.depreciation_amortization, anchor)
    if combined is not None:
        return combined
    amort = _series_value(fund.amortization, anchor)
    return amort if amort is not None else None
