"""Domain models shared by providers, the metric engine and the API."""

from __future__ import annotations

from datetime import date, datetime
from pydantic import BaseModel, Field


class PricePoint(BaseModel):
    d: date
    close: float


class PriceHistory(BaseModel):
    ticker: str
    currency: str = "USD"
    points: list[PricePoint] = Field(default_factory=list)
    source: str = ""
    used_adjusted: bool = False

    def closes(self) -> list[float]:
        return [p.close for p in self.points]

    def last(self) -> float | None:
        return self.points[-1].close if self.points else None

    def window(self, days: int) -> list[PricePoint]:
        if not self.points:
            return []
        cutoff = self.points[-1].d.toordinal() - days
        return [p for p in self.points if p.d.toordinal() >= cutoff]


class Quote(BaseModel):
    ticker: str
    name: str | None = None
    currency: str = "USD"
    price: float | None = None
    market_cap: float | None = None
    shares_outstanding: float | None = None
    trailing_pe: float | None = None
    forward_pe: float | None = None
    eps_forward: float | None = None
    eps_current_year: float | None = None
    target_mean_price: float | None = None
    beta: float | None = None
    source: str = ""
    as_of: datetime | None = None


class AnalystView(BaseModel):
    """Sell-side consensus, kept separate from the quote.

    The consensus is a dated survey, not a live price, and mixing it into the
    quote would blur which provider a field came from.
    """

    ticker: str
    target_mean: float | None = None
    target_median: float | None = None
    target_high: float | None = None
    target_low: float | None = None
    analyst_count: float | None = None
    recommendation: str | None = None
    forward_pe: float | None = None
    yahoo_beta: float | None = None
    source: str = ""
    as_of: datetime | None = None


class RatingCounts(BaseModel):
    """One period's rating distribution, newest period first in the list."""

    period: str
    strong_buy: int = 0
    buy: int = 0
    hold: int = 0
    sell: int = 0
    strong_sell: int = 0

    @property
    def total(self) -> int:
        return self.strong_buy + self.buy + self.hold + self.sell + self.strong_sell


class AnalystAction(BaseModel):
    """A single published rating or target change.

    Kept as raw as Yahoo reports it: `action` is up/down/init/main/reit and
    `price_target_action` is Raises/Lowers/Announces, so a caller can decide how
    to summarise rather than inheriting our reading of it.
    """

    date: date
    firm: str
    action: str | None = None
    from_grade: str | None = None
    to_grade: str | None = None
    price_target_action: str | None = None
    price_target: float | None = None
    prior_price_target: float | None = None


class EarningsSurprise(BaseModel):
    """One reported quarter against the consensus that preceded it."""

    period: str
    quarter_end: date | None = None
    eps_actual: float | None = None
    eps_estimate: float | None = None
    eps_difference: float | None = None
    surprise_pct: float | None = None
    currency: str | None = None


class EarningsEstimate(BaseModel):
    """Forward consensus for one period (`0q`, `+1q`, `0y`, `+1y`)."""

    period: str
    end_date: str | None = None
    eps_avg: float | None = None
    eps_low: float | None = None
    eps_high: float | None = None
    eps_year_ago: float | None = None
    analyst_count: int | None = None
    growth: float | None = None
    revenue_avg: float | None = None


class AnalystDetail(BaseModel):
    """Everything the analyst section shows for one company."""

    ticker: str
    target_mean: float | None = None
    target_median: float | None = None
    target_high: float | None = None
    target_low: float | None = None
    recommendation: str | None = None
    recommendation_mean: float | None = None
    current_price: float | None = None
    ratings: list[RatingCounts] = Field(default_factory=list)
    actions: list[AnalystAction] = Field(default_factory=list)
    earnings_history: list[EarningsSurprise] = Field(default_factory=list)
    estimates: list[EarningsEstimate] = Field(default_factory=list)
    next_earnings_date: str | None = None
    source: str = ""
    as_of: datetime | None = None
    notes: list[str] = Field(default_factory=list)


class FactSeries(BaseModel):
    """One annual figure series (newest first)."""

    tag: str
    unit: str
    points: list[tuple[date, float]] = Field(default_factory=list)

    def latest(self) -> float | None:
        return self.points[0][1] if self.points else None

    def latest_end(self) -> date | None:
        return self.points[0][0] if self.points else None

    def at(self, index: int) -> float | None:
        return self.points[index][1] if len(self.points) > index else None

    def aligned(self, end: date | None, tolerance_days: int = 120) -> float | None:
        """Value for the period ending at `end`, when one exists.

        Scans the whole series rather than only the newest point: a filer whose
        latest tagged period sits outside the tolerance (Oracle's fiscal year
        ends May 31 and its newest comparatives can lag the anchor) would
        otherwise return a blank even though the matching period is present.
        Picking the *nearest* end also avoids borrowing a period that is close
        but wrong.
        """
        if end is None or not self.points:
            return None
        best: tuple[int, float] | None = None
        for point_end, value in self.points:
            delta = abs((end - point_end).days)
            if delta <= tolerance_days and (best is None or delta < best[0]):
                best = (delta, value)
        return best[1] if best else None


class Fundamentals(BaseModel):
    """Raw annual inputs. All monetary values are in the reported currency."""

    ticker: str
    entity_name: str | None = None
    currency: str = "USD"
    fiscal_end: date | None = None
    source: str = ""
    source_url: str | None = None
    cik: int | None = None
    notes: list[str] = Field(default_factory=list)

    revenue: FactSeries | None = None
    gross_profit: FactSeries | None = None
    cost_of_revenue: FactSeries | None = None
    operating_income: FactSeries | None = None
    net_income: FactSeries | None = None
    equity: FactSeries | None = None
    assets: FactSeries | None = None
    cash: FactSeries | None = None
    operating_cash_flow: FactSeries | None = None
    capex: FactSeries | None = None
    eps_diluted: FactSeries | None = None
    shares_diluted: FactSeries | None = None
    shares_outstanding: float | None = None
    shares_basis: str = ""
    reporting_currency: str | None = None
    fx_usd_per_twd: float | None = None
    fx_source: str | None = None
    sbc: FactSeries | None = None
    restructuring: FactSeries | None = None
    amortization: FactSeries | None = None
    depreciation_amortization: FactSeries | None = None
    tax_provision: FactSeries | None = None
    pretax_income: FactSeries | None = None
    debt_current: FactSeries | None = None
    debt_long: FactSeries | None = None

    @property
    def is_usable(self) -> bool:
        return self.revenue is not None and self.operating_income is not None


class MetricRow(BaseModel):
    """A fully computed peer row (mirrors Data Cache rows 7..36)."""

    ticker: str
    company: str | None = None
    currency: str = "USD"
    filing_currency: str | None = None
    fx_usd_per_twd: float | None = None
    fx_source: str | None = None
    status: str = "pending"
    notes: str = ""

    # market — quoted returns (calendar windows, benchmark-relative where noted)
    price: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    market_cap: float | None = None
    return_1m: float | None = None
    return_3m: float | None = None
    return_6m: float | None = None
    return_1y: float | None = None
    return_ytd: float | None = None
    return_3y: float | None = None
    excess_return_1m: float | None = None
    excess_return_3m: float | None = None
    excess_return_6m: float | None = None
    excess_return_1y: float | None = None
    excess_return_ytd: float | None = None
    benchmark_ticker: str | None = None
    benchmark_return_3m: float | None = None
    benchmark_return_6m: float | None = None
    benchmark_return_1y: float | None = None
    benchmark_as_of: date | None = None

    # market — risk (annualised, trailing year of daily returns)
    beta: float | None = None
    beta_1y: float | None = None
    volatility: float | None = None
    downside_deviation: float | None = None
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    max_drawdown_1y: float | None = None
    drawdown_52w: float | None = None
    risk_obs_days: int | None = None

    # market — risk-free assumption actually used for Sharpe/Sortino
    risk_free_rate: float | None = None
    market_price_basis: str | None = None

    # fundamentals / derived
    revenue_fy0: float | None = None
    revenue_growth_yoy: float | None = None
    revenue_cagr_5y: float | None = None
    revenue_cagr_3y: float | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    roe: float | None = None
    roic: float | None = None
    roa: float | None = None
    fcf_fy0: float | None = None
    fcf_margin: float | None = None
    fcf_yield: float | None = None
    ocf_to_net_income: float | None = None
    debt_to_assets: float | None = None
    net_income_fy0: float | None = None
    eps_growth_yoy: float | None = None
    net_income_growth_yoy: float | None = None
    gross_profit_growth_yoy: float | None = None
    fcf_growth_yoy: float | None = None
    operating_leverage: float | None = None
    asset_turnover: float | None = None
    capex_intensity: float | None = None
    cash_to_assets: float | None = None
    sbc_pct_revenue: float | None = None
    price_to_ocf: float | None = None
    ev_to_sales: float | None = None
    peg_ratio: float | None = None
    net_debt_to_ebitda: float | None = None
    interest_cover: float | None = None
    net_debt_fy0: float | None = None
    ebitda_fy0: float | None = None
    # Provenance for substituted measures: which CAGR window was actually used,
    # and which trend figures are changes rather than percentages.
    cagr_basis: str = ""
    trend_basis: str = ""

    # per-share bridge
    gaap_eps: float | None = None
    sbc_adj_share: float | None = None
    restructuring_adj_share: float | None = None
    amortization_adj_share: float | None = None
    other_adj_share: float | None = None
    tax_adj_share: float | None = None
    model_adjusted_eps: float | None = None
    reported_non_gaap_eps: float | None = None
    selected_adjusted_eps: float | None = None

    # estimates / multiples
    eps_fy1_estimate: float | None = None
    eps_growth_fy1: float | None = None
    revenue_fy1_estimate: float | None = None
    revenue_growth_fy1: float | None = None
    eps_cagr_5y: float | None = None
    forward_pe: float | None = None
    price_to_sales: float | None = None
    ev_to_ebitda: float | None = None
    price_to_book: float | None = None
    price_to_fcf: float | None = None
    drawdown_52w: float | None = None
    analyst_target: float | None = None
    analyst_upside: float | None = None
    analyst_target_median: float | None = None
    analyst_target_high: float | None = None
    analyst_target_low: float | None = None
    analyst_count: float | None = None
    analyst_recommendation: str | None = None
    analyst_source: str = ""
    beta_published: float | None = None

    # provenance
    source_quality: str = ""
    sec_source: str | None = None
    market_source: str | None = None
    non_gaap_source: str | None = None
    fiscal_end: date | None = None

    # raw filing inputs (Data Cache AZ..BS)
    revenue_fy_minus_1: float | None = None
    revenue_fy_minus_5: float | None = None
    gaap_eps_fy_minus_1: float | None = None
    gaap_eps_fy_minus_5: float | None = None
    share_count: float | None = None
    cash_fy0: float | None = None
    debt_fy0: float | None = None
    operating_income_fy0: float | None = None
    da_fy0: float | None = None
    diluted_shares_fy0: float | None = None
    sbc_fy0: float | None = None
    restructuring_fy0: float | None = None
    amortization_fy0: float | None = None
    effective_tax_rate: float | None = None
    equity_fy0: float | None = None
    ocf_fy0: float | None = None
    capex_fy0: float | None = None
    capex_basis: str = ""
    debt_basis: str = ""
    margin_basis: str = ""
    share_basis: str = ""
    total_assets_fy0: float | None = None
    gross_profit_fy0: float | None = None
    cost_of_revenue_fy0: float | None = None
    forward_12m_eps: float | None = None
    quote_url: str | None = None

    # scoring (1..10) — mirrors Scoring!W..AW
    z_growth_yoy: float | None = None
    z_revenue_cagr: float | None = None
    z_eps_growth_fy1: float | None = None
    z_eps_cagr: float | None = None
    z_gross_margin: float | None = None
    z_operating_margin: float | None = None
    z_net_margin: float | None = None
    z_roe: float | None = None
    z_roic: float | None = None
    z_fcf_margin: float | None = None
    z_fcf_yield: float | None = None
    z_cash_conversion: float | None = None
    z_forward_pe: float | None = None
    z_price_sales: float | None = None
    z_ev_ebitda: float | None = None
    z_price_book: float | None = None
    z_price_fcf: float | None = None
    z_return_1y: float | None = None
    z_return_3m: float | None = None
    z_excess_return_6m: float | None = None
    z_debt_assets: float | None = None
    z_drawdown: float | None = None
    # Retained for snapshots written before `beta` stopped being scored; the
    # scored field is `z_beta_1y`.
    z_beta: float | None = None
    z_return_6m: float | None = None
    z_excess_return_3m: float | None = None
    z_volatility: float | None = None
    z_sharpe: float | None = None
    z_max_drawdown: float | None = None
    z_sortino: float | None = None
    z_beta_1y: float | None = None
    z_excess_return_1y: float | None = None
    z_roa: float | None = None
    z_capex_intensity: float | None = None

    score_growth: float | None = None
    score_profitability: float | None = None
    score_cash: float | None = None
    score_valuation: float | None = None
    score_market: float | None = None
    # Market splits into the two questions it answers, so a screen can show
    # whether a rank came from performance or from risk.
    score_market_performance: float | None = None
    score_market_risk: float | None = None
    score_overall: float | None = None
    data_coverage: int = 0
    coverage_pct: float | None = None
    rank_eligible: bool = False
    rank: int | None = None
    profile: str = ""

    fetched_at: datetime | None = None
    elapsed_ms: int | None = None

    # Which metric generation produced this row. A snapshot written by an older
    # build lacks the newer columns, and silently rendering it as blanks is
    # indistinguishable from a data-source failure — so the value travels with
    # the row and the API reports the mismatch explicitly.
    metrics_version: int = 0


class RefreshRequest(BaseModel):
    tickers: list[str] | None = None
    use_cache: bool = True


class RefreshResult(BaseModel):
    run_id: str
    started_at: datetime
    finished_at: datetime
    rows: list[MetricRow]
    errors: dict[str, str] = Field(default_factory=dict)
