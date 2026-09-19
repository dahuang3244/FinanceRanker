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
        """Latest value, but only when it lines up with the chosen fiscal year."""
        if end is None or not self.points:
            return None
        if abs((end - self.points[0][0]).days) > tolerance_days:
            return None
        return self.points[0][1]


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
    status: str = "pending"
    notes: str = ""

    # market
    price: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    market_cap: float | None = None
    return_1y: float | None = None
    beta: float | None = None

    # fundamentals / derived
    revenue_fy0: float | None = None
    revenue_growth_yoy: float | None = None
    revenue_cagr_5y: float | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    roe: float | None = None
    roic: float | None = None
    fcf_fy0: float | None = None
    fcf_margin: float | None = None
    fcf_yield: float | None = None
    ocf_to_net_income: float | None = None
    debt_to_assets: float | None = None

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
    z_debt_assets: float | None = None
    z_drawdown: float | None = None
    z_beta: float | None = None

    score_growth: float | None = None
    score_profitability: float | None = None
    score_cash: float | None = None
    score_valuation: float | None = None
    score_market: float | None = None
    score_overall: float | None = None
    data_coverage: int = 0
    rank_eligible: bool = False
    rank: int | None = None
    profile: str = ""

    fetched_at: datetime | None = None
    elapsed_ms: int | None = None


class RefreshRequest(BaseModel):
    tickers: list[str] | None = None
    use_cache: bool = True


class RefreshResult(BaseModel):
    run_id: str
    started_at: datetime
    finished_at: datetime
    rows: list[MetricRow]
    errors: dict[str, str] = Field(default_factory=dict)
