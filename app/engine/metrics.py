"""Metric engine.

Rebuilds every derived figure the workbook computes, but in plain Python instead
of Excel formulas. `Set-V5Formulas` / `Set-V5Formulas`-style logic from
`Refresh_Free_Data.ps1` is the reference; each function below notes the
corresponding `Data Cache` column so the output can be reconciled cell-by-cell.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from app.config import settings
from app.engine.market import (
    drawdown_from_high,
    excess_returns,
    risk_stats,
    window_returns,
)
from app.engine.scoring import METRICS_VERSION, apply_coverage
from app.models import AnalystView, Fundamentals, MetricRow, PriceHistory, Quote

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
    analyst: AnalystView | None = None,
) -> MetricRow:
    row = MetricRow(
        ticker=ticker,
        currency=quote.currency or fund.currency or "USD",
        fetched_at=datetime.now(),
        metrics_version=METRICS_VERSION,
    )
    row.company = fund.entity_name or quote.name or ticker
    row.filing_currency = fund.reporting_currency or fund.currency
    row.fx_usd_per_twd = fund.fx_usd_per_twd
    row.fx_source = fund.fx_source
    row.fiscal_end = fund.fiscal_end
    row.source_quality = f"{fund.source} annual facts + {history.source} price history"
    if fund.notes:
        row.source_quality += "; " + "; ".join(fund.notes)
    row.sec_source = fund.source_url
    row.market_source = (f"{history.source} daily close {history.points[-1].d}"
                         if history.points else history.source)
    row.quote_url = quote.source

    # Order matters: the raw filing block resolves the authoritative share
    # count, which the market block then uses to derive market cap. Everything
    # downstream (yields, multiples) depends on that one market-cap value.
    _raw_filing_block(row, fund)
    _market_block(row, history, quote, benchmark, analyst)
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
        dropped = _drop_cross_currency(row, fund.currency, row.currency, quote)
        mismatch_note = (
            f"Reporting currency {fund.currency} differs from trading currency "
            f"{row.currency} (ADR / foreign private issuer); {dropped} withheld "
            f"because no FX conversion is available; ADS share counts are normalized where known"
        )
        # The guard clears everything that mixes the filing currency with the
        # trading price, including ratios rebuilt from filing inputs. Restore
        # the ones that are provably currency-free so an ADR is not left with
        # avoidable blanks next to its US peers.
        _rebuild_after_currency_guard(row, fund)
        row.source_quality = f"{fund.source} annual facts (currency mismatch flagged)"
        row.status = "Partial filing data"

    row.data_coverage = apply_coverage(row)
    market_note = _market_provenance_note(row)

    # Status/notes are composed last so the currency warning survives.
    if row.status != "Partial filing data" and (
        row.revenue_fy0 is not None and row.gaap_eps is not None and row.operating_income_fy0 is not None
    ):
        row.status = "Refreshed"
        row.notes = (
            f"{fund.source} annual facts + {history.source} price history. "
            "Model EPS adjustments are after-tax per-share estimates; review the reconciliation."
        )
        if market_note:
            row.notes += " " + market_note
        if fund.notes:
            row.notes += " " + " ".join(fund.notes)
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
        if market_note:
            parts.append(market_note)
        if mismatch_note:
            parts.append(mismatch_note)
        row.status = "Partial filing data"
        row.notes = "; ".join(parts) + "."

    return row


# --------------------------------------------------------------------------- #
# market data (Data Cache F..K, AR, AS, AT)
# --------------------------------------------------------------------------- #
def _market_provenance_note(row: MetricRow) -> str:
    """Spell out the two assumptions behind every risk figure on the row.

    Returns and Sharpe ratios are only meaningful next to the basis they were
    measured on and the risk-free rate they were measured against, so both are
    stated rather than left to a tooltip.
    """
    parts: list[str] = []
    if row.market_price_basis:
        parts.append(f"Returns use {row.market_price_basis}.")
    if row.risk_free_rate is not None and row.sharpe_ratio is not None:
        parts.append(f"Sharpe/Sortino use a {row.risk_free_rate:.1%} annual risk-free rate.")
    if row.benchmark_ticker:
        parts.append(f"Excess returns versus {row.benchmark_ticker}.")
    else:
        parts.append("Benchmark unavailable, so benchmark-relative figures are blank.")
    return " ".join(parts)


def _analyst_block(
    row: MetricRow,
    analyst: AnalystView | None,
    price: float | None,
) -> None:
    """Attach sell-side consensus and derive the upside it implies.

    The quote provider is tried first (it is the primary source for the row);
    Yahoo's crumb-authenticated consensus fills the gap when the quote has no
    target, which is the normal case for the Tencent quote used by default.
    """
    if analyst is None:
        return
    if row.analyst_target is None:
        row.analyst_target = analyst.target_mean
    row.analyst_target_median = analyst.target_median
    row.analyst_target_high = analyst.target_high
    row.analyst_target_low = analyst.target_low
    row.analyst_count = analyst.analyst_count
    row.analyst_recommendation = analyst.recommendation
    if analyst.source and analyst.source not in row.analyst_source:
        row.analyst_source = analyst.source
    if row.analyst_upside is None and row.analyst_target and price:
        row.analyst_upside = row.analyst_target / price - 1.0
    # A published beta is worth carrying as a cross-check on our own estimate;
    # the screen still scores the beta computed from the same series as every
    # other row, so the comparison stays internally consistent.
    if row.beta_published is None:
        row.beta_published = analyst.yahoo_beta
    if row.forward_pe is None and analyst.forward_pe:
        row.forward_pe = analyst.forward_pe


def _market_block(
    row: MetricRow,
    history: PriceHistory,
    quote: Quote,
    benchmark: PriceHistory | None,
    analyst: AnalystView | None = None,
) -> None:
    price = quote.price or history.last()
    row.price = price

    window = history.window(365)
    closes = [p.close for p in window] or history.closes()
    if closes:
        row.high_52w = max(closes)
        row.low_52w = min(closes)

    # ---- returns -----------------------------------------------------------
    # Computed from the daily series we already hold, so these are never blank
    # for a stock that has any usable price history. `excess_return_*` is the
    # 3M/6M/1Y comparison against SPY the peer screen is built around.
    returns = window_returns(history)
    row.return_1m = returns.get("1m")
    row.return_3m = returns.get("3m")
    row.return_6m = returns.get("6m")
    row.return_1y = returns.get("1y")
    row.return_ytd = returns.get("ytd")
    row.return_3y = returns.get("3y")

    if benchmark is not None:
        bench_returns = window_returns(benchmark)
        row.benchmark_ticker = benchmark.ticker or DEFAULT_BENCHMARK
        row.benchmark_return_3m = bench_returns.get("3m")
        row.benchmark_return_6m = bench_returns.get("6m")
        row.benchmark_return_1y = bench_returns.get("1y")
        row.benchmark_as_of = benchmark.points[-1].d if benchmark.points else None
        excess = excess_returns(history, benchmark)
        row.excess_return_1m = excess.get("1m")
        row.excess_return_3m = excess.get("3m")
        row.excess_return_6m = excess.get("6m")
        row.excess_return_1y = excess.get("1y")
        row.excess_return_ytd = excess.get("ytd")

    # ---- risk --------------------------------------------------------------
    risk_free = settings.risk_free_rate
    stats = risk_stats(history, benchmark, risk_free=risk_free)
    row.risk_free_rate = risk_free
    row.volatility = stats["volatility"]
    row.downside_deviation = stats["downside_deviation"]
    row.sharpe_ratio = stats["sharpe"]
    row.sortino_ratio = stats["sortino"]
    row.max_drawdown_1y = stats["max_drawdown"]
    # `beta` stays on the full available history because that is how every
    # vendor quotes it; the trailing-year estimate is kept beside it because it
    # is the one that lines up with the 1-year Sharpe/volatility figures.
    row.beta = stats["beta_history"] if stats["beta_history"] is not None else stats["beta"]
    row.beta_1y = stats["beta_1y"]
    row.risk_obs_days = int(stats["obs"]) if stats.get("obs") else None

    # Both sides must use the same price basis. A live raw quote against a
    # dividend-adjusted historical high gives a misleading drawdown.
    row.drawdown_52w = drawdown_from_high(history.last(), row.high_52w)
    row.market_price_basis = (
        f"{history.source} adjusted closes (dividends/splits)" if history.used_adjusted
        else f"{history.source} raw closes (no dividend adjustment)"
    )

    # Market cap: prefer price x filing share count, else the provider's figure.
    shares = row.share_count or quote.shares_outstanding
    if shares is None and quote.market_cap and price:
        shares = quote.market_cap / price
        row.share_count = shares
    if price is not None and shares:
        row.market_cap = price * shares
    if row.market_cap is None:
        row.market_cap = quote.market_cap

    row.analyst_target = quote.target_mean_price
    if quote.target_mean_price and price:
        row.analyst_upside = quote.target_mean_price / price - 1.0

    _analyst_block(row, analyst, price)


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
    # The raw block may already have substituted a proxy for a filer that tags
    # no capex at all; honour it here so free cash flow is computed once, from
    # one capex number, instead of silently disagreeing with `capex_fy0`.
    capex = _series_value(fund.capex, anchor)
    if capex is None and row.capex_fy0 is not None:
        capex = row.capex_fy0
    eps = _series_value(fund.eps_diluted, anchor)

    # Gross profit can be derived when only a cost line is tagged, and vice
    # versa. Filers migrate these tags constantly (Oracle stopped tagging
    # `GrossProfit` after FY2018), so deriving from whichever side is current
    # is the difference between a usable margin and a blank.
    if gross is None and revenue is not None and cost is not None:
        gross = revenue - cost
    if revenue is None and gross is not None and cost is not None:
        revenue = gross + cost
    if gross is None and row.gross_profit_fy0 is not None:
        gross = row.gross_profit_fy0

    # Last resort: revenue - operating income is an *upper bound* on gross
    # profit (it omits R&D and SG&A), so it is only used when the filer tags no
    # cost line at all, and the basis string records the overstatement. Without
    # it a large filer like Oracle shows no gross margin while every peer does.
    derived_gross = False
    if gross is None and revenue is not None and operating is not None:
        uplift = revenue - operating
        if 0 < uplift < revenue:
            gross = uplift
            derived_gross = True
            row.margin_basis = "Gross margin = (revenue - operating income); no cost line tagged"
    if derived_gross and row.gross_profit_fy0 is None:
        row.gross_profit_fy0 = gross

    row.revenue_fy0 = revenue
    row.gross_margin = safe_div(gross, revenue)
    row.operating_margin = safe_div(operating, revenue)
    row.net_margin = safe_div(net, revenue)
    row.roe = safe_div(net, equity)
    row.gaap_eps = eps
    row.net_income_fy0 = net
    # Return on the asset base, beside return on equity: ROE flatters a
    # leveraged or buyback-shrunk balance sheet, ROA does not.
    row.roa = safe_div(net, assets)
    row.asset_turnover = safe_div(revenue, assets)

    # Growth: prefer like-for-like period pairs from the same series.
    row.revenue_growth_yoy = _series_growth(fund.revenue, 1)
    row.revenue_cagr_5y = _series_cagr(fund.revenue, 5)
    row.revenue_cagr_3y = _series_cagr(fund.revenue, 3)
    row.eps_cagr_5y = _series_cagr(fund.eps_diluted, 5)
    row.eps_growth_yoy = _series_growth(fund.eps_diluted, 1)
    row.net_income_growth_yoy = _series_growth(fund.net_income, 1)
    row.gross_profit_growth_yoy = _series_growth(fund.gross_profit, 1)
    if row.gross_profit_growth_yoy is None and gross is not None and cost is not None:
        # Derive gross profit per year when the filer tags only a cost line, so
        # its growth is still measurable.
        prior_gross = _prior_derived_gross(fund, gross)
        if prior_gross:
            row.gross_profit_growth_yoy = growth(gross, prior_gross)

    # Operating leverage: how much faster operating income moves than revenue.
    # A ratio near 1 means costs scaled with sales; above 1 means margin
    # expansion. Left blank when the prior year is near zero, where the ratio
    # would explode into noise rather than information.
    op_prior = _series_at_offset(fund.operating_income, 1)
    rev_prior = _series_at_offset(fund.revenue, 1)
    if None not in (operating, op_prior, revenue, rev_prior) and abs(op_prior) > 1e-9 and rev_prior:
        op_growth = operating / op_prior - 1.0
        rev_growth = revenue / rev_prior - 1.0
        if abs(rev_growth) > 0.01:
            row.operating_leverage = op_growth / rev_growth

    fcf = None
    if ocf is not None and capex is not None:
        fcf = ocf - abs(capex)
    row.fcf_fy0 = fcf
    row.fcf_margin = safe_div(fcf, revenue)
    row.fcf_yield = safe_div(fcf, row.market_cap)
    row.ocf_to_net_income = safe_div(ocf, net)
    # Capital intensity: the share of revenue consumed by capex. A high figure
    # explains a low FCF margin, which otherwise looks like poor cash generation.
    row.capex_intensity = safe_div(abs(capex) if capex is not None else None, revenue)
    # Year-over-year FCF growth, recomputed per year so it does not depend on a
    # single capex figure.
    row.fcf_growth_yoy = _fcf_growth(fund, anchor)
    row.cash_to_assets = safe_div(cash, assets)
    sbc = _series_value(fund.sbc, anchor)
    row.sbc_pct_revenue = safe_div(sbc, revenue)

    debt = _total_debt(fund, anchor)
    row.debt_fy0 = debt
    row.debt_to_assets = safe_div(debt, assets)
    if row.debt_to_assets is None and assets and equity is not None:
        # A filer that tags no borrowings at the fiscal anchor (Oracle tags only
        # a stale `LongTermDebt`) still has a leverage figure worth showing.
        # Total liabilities is an upper bound on debt, so the proxy overstates
        # leverage; the basis string says so rather than hiding it.
        liabilities = assets - equity
        if liabilities > 0:
            row.debt_to_assets = liabilities / assets
            row.debt_basis = "Total liabilities proxy (assets - equity); no borrowings tagged"
    else:
        row.debt_basis = row.debt_basis or ("Filing borrowings" if debt is not None else "")

    # ROIC = operating income * (1 - tax) / (equity + debt - cash)
    invested = None
    if equity is not None and debt is not None and cash is not None:
        invested = equity + debt - cash
    if operating is not None and invested and invested > 0:
        row.roic = operating * (1.0 - effective_tax_rate(fund)) / invested

    # Enterprise-value and coverage figures. EBITDA is the same operating
    # income + D&A used by EV/EBITDA, kept on the row so the multiple and the
    # leverage ratio cannot disagree about what EBITDA is.
    row.ebitda_fy0 = (operating + row.da_fy0) if (operating is not None and row.da_fy0 is not None) else None
    if debt is not None and cash is not None:
        row.net_debt_fy0 = debt - cash
    if row.net_debt_fy0 is not None and row.ebitda_fy0 and row.ebitda_fy0 > 0:
        row.net_debt_to_ebitda = row.net_debt_fy0 / row.ebitda_fy0


def _series_value(series, anchor: date | None) -> float | None:
    """Latest value, aligned to the fiscal anchor when the dates differ."""
    if series is None or not series.points:
        return None
    if anchor is None:
        return series.latest()
    aligned = series.aligned(anchor)
    return aligned


def _series_at_offset(series, periods: int) -> float | None:
    """Value `periods` fiscal years before the latest, matched by calendar date."""
    if series is None or len(series.points) < 2:
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
    if series is None or len(series.points) < 2:
        return None
    return growth(series.points[0][1], _series_at_offset(series, periods))


def _series_cagr(series, periods: int) -> float | None:
    if series is None or len(series.points) < 2:
        return None
    return cagr(series.points[0][1], _series_at_offset(series, periods), periods)


def _prior_derived_gross(fund: Fundamentals, current_gross: float | None) -> float | None:
    """Prior-year gross profit when the filer tags only a cost line.

    Oracle stopped tagging `GrossProfit` after FY2018, so its gross-profit
    growth is only measurable if the prior year is derived the same way the
    current year was.
    """
    anchor = fund.fiscal_end
    revenue = _series_at_offset(fund.revenue, 1)
    cost = _series_at_offset(fund.cost_of_revenue, 1)
    if revenue is not None and cost is not None:
        derived = revenue - cost
        return derived if derived > 0 else None
    gross = _series_at_offset(fund.gross_profit, 1)
    if gross is not None:
        return gross
    return None


def _fcf_growth(fund: Fundamentals, anchor: date | None) -> float | None:
    """Free-cash-flow growth, computed per year from that year's own capex.

    Comparing the current FCF against a prior figure taken from a different capex
    basis would mix a real change with a substitution, so both legs are built
    from the same filings.
    """
    current_ocf = _series_value(fund.operating_cash_flow, anchor)
    current_capex = _series_value(fund.capex, anchor)
    prior_ocf = _series_at_offset(fund.operating_cash_flow, 1)
    prior_capex = _series_at_offset(fund.capex, 1)
    if None in (current_ocf, current_capex, prior_ocf, prior_capex):
        return None
    current = current_ocf - abs(current_capex)
    prior = prior_ocf - abs(prior_capex)
    if prior <= 0:
        # Growth off a non-positive base is not a percentage, it is a sign flip.
        return None
    return current / prior - 1.0


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
    row.diluted_shares_fy0 = shares if shares is not None else row.diluted_shares_fy0

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
    if row.market_cap and row.ocf_fy0 and row.ocf_fy0 > 0:
        # Operating cash flow is a steadier denominator than FCF, which swings
        # with a single year's capex programme.
        row.price_to_ocf = row.market_cap / row.ocf_fy0
    if row.market_cap is not None and row.net_debt_fy0 is not None and row.revenue_fy0:
        enterprise = row.market_cap + row.net_debt_fy0
        if enterprise > 0:
            row.ev_to_sales = enterprise / row.revenue_fy0
    # PEG is only defined for positive, non-trivial growth: a near-zero
    # denominator turns it into noise rather than a valuation.
    if row.forward_pe and row.eps_growth_fy1 is not None:
        growth_pct = row.eps_growth_fy1 * 100.0
        if row.forward_pe > 0 and growth_pct > 1.0:
            row.peg_ratio = row.forward_pe / growth_pct


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
    "fcf_yield",
    "price_to_sales",
    "price_to_book",
    "price_to_fcf",
    "ev_to_ebitda",
    "eps_growth_fy1",
    "model_adjusted_eps",
    "selected_adjusted_eps",
    "sbc_adj_share",
    "restructuring_adj_share",
    "amortization_adj_share",
)


def _drop_cross_currency(row: MetricRow, reporting: str, trading: str, quote: Quote) -> str:
    """Clear price-dependent figures and return a human-readable summary."""
    for field in _CROSS_CURRENCY_FIELDS:
        setattr(row, field, None)
    # A market cap based on USD ADS quotes is valid only if the share count is
    # ADS-adjusted. A provider-supplied USD market cap is also usable.
    if "ADS ratio" not in row.share_basis:
        row.market_cap = quote.market_cap
    # Filing EPS keeps its own unit; quote-derived forward P/E and analyst
    # upside already use USD per ADS.
    row.status = f"Partial filing data ({reporting} reporting vs {trading} trading)"
    return "filing-based price multiples and cross-currency EPS growth"


# Ratios rebuilt here are currency-free by construction (a money amount divided
# by the same money amount), so applying them after the currency guard is exact
# rather than an approximation.
def _rebuild_after_currency_guard(row: MetricRow, fund: Fundamentals) -> None:
    """Restore the currency-free derivations the guard just cleared."""
    anchor = fund.fiscal_end
    revenue = row.revenue_fy0
    ocf = row.ocf_fy0
    capex = row.capex_fy0

    fcf = ocf - abs(capex) if (ocf is not None and capex is not None) else None
    row.fcf_fy0 = fcf
    row.fcf_margin = safe_div(fcf, revenue)

    net = _series_value(fund.net_income, anchor)
    row.ocf_to_net_income = safe_div(ocf, net)

    # Leverage is a same-currency ratio, so it survives the guard as well.
    assets = row.total_assets_fy0
    equity = row.equity_fy0
    row.debt_to_assets = safe_div(row.debt_fy0, assets)
    if row.debt_to_assets is None and assets and equity is not None:
        liabilities = assets - equity
        if liabilities > 0:
            row.debt_to_assets = liabilities / assets
            row.debt_basis = "Total liabilities proxy (assets - equity); no borrowings tagged"


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
    if row.diluted_shares_fy0 is None:
        # Foreign private issuers (TSM files IFRS) frequently tag only a share
        # count and no diluted weighted-average series. Fall back to the
        # authoritative count so per-share maths is never left blank; the basis
        # string records that this is an approximation.
        if shares is None and row.share_count is not None:
            shares = row.share_count
            row.share_basis = (fund.shares_basis or "share count") + " (diluted series unavailable)"
    row.share_basis = row.share_basis or fund.shares_basis or (
        "Approximation: annual diluted shares" if shares else ""
    )
    row.diluted_shares_fy0 = shares

    row.cash_fy0 = _series_value(fund.cash, anchor)
    row.debt_fy0 = _total_debt(fund, anchor)
    row.operating_income_fy0 = _series_value(fund.operating_income, anchor)
    row.da_fy0 = _depreciation_amortization(fund, anchor)
    row.equity_fy0 = _series_value(fund.equity, anchor)
    row.ocf_fy0 = _series_value(fund.operating_cash_flow, anchor)
    capex = _series_value(fund.capex, anchor)
    if capex is None:
        # A filer that tags no capital expenditure at all (common for IFRS
        # foreign private issuers) still leaves free cash flow comparable when
        # we substitute the maintenance-level proxy "D&A - 2". Using D&A alone
        # would overstate FCF; the 2x haircut keeps it conservative and the
        # basis string makes the substitution explicit.
        da = row.da_fy0
        if da is not None and da > 0:
            capex = da / 2.0
            row.capex_basis = "D&A proxy (50% of depreciation & amortisation)"
    row.capex_fy0 = abs(capex) if capex is not None else None
    row.capex_basis = row.capex_basis or ("Filing capex" if capex is not None else "")
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
