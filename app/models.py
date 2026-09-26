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
    # Non-GAAP bridge inputs — whatever the filer actually tags. Absent means
    # "not disclosed", which the reconciliation reports as unavailable rather
    # than treating it as zero.
    equity_securities_gain: FactSeries | None = None
    other_nonoperating_income: FactSeries | None = None
    legal_settlement: FactSeries | None = None
    impairment: FactSeries | None = None
    acquisition_costs: FactSeries | None = None
    debt_extinguishment: FactSeries | None = None
    discontinued_operations: FactSeries | None = None

    @property
    def is_usable(self) -> bool:
        return self.revenue is not None and self.operating_income is not None


class NonGaapLine(BaseModel):
    """One row of a company's GAAP-to-adjusted reconciliation.

    Annual figures stay in the reporting currency: dividing every add-back by
    the share count is what the per-share bridge already does, and doing it twice
    invites the two from disagreeing. `is_addback` says which direction the line
    moves adjusted income.
    """

    key: str
    label: str
    value: float | None = None
    is_addback: bool = True
    source: str = ""


class NonGaapReconciliation(BaseModel):
    """A workbook-style bridge: GAAP income -> adjusted income -> adjusted EPS.

    Filers do not tag a comparable "non-GAAP EPS". Each discloses its own
    adjustments, so this carries the lines a company actually reported and names
    the ones it did not, instead of silently treating those as zero — which
    would present an untagged figure as though the company had endorsed it.

    `period` is not decoration. A quarterly bridge and an annual one measure
    different things, and a reader cannot tell them apart from the numbers:
    Alphabet's equity-security gains run from $1.3bn to $99bn across six
    consecutive quarters, so an annual bridge averages periods with nothing in
    common. Every bridge therefore states the basis it was built on.
    """

    currency: str = ""
    tax_rate: float | None = None
    lines: list[NonGaapLine] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    gaap_net_income: float | None = None
    adjusted_net_income: float | None = None
    diluted_shares: float | None = None
    gaap_eps: float | None = None
    adjusted_eps: float | None = None
    basis: str = ""
    eps_basis: str = ""
    complete_years: int = 0
    # "annual" | "quarter" — always populated, so the UI can label the figure.
    period: str = "annual"
    period_label: str = ""       # e.g. "2026 Q2"
    period_span: str = ""        # e.g. "2026-04-01 → 2026-06-30"


class QuarterPoint(BaseModel):
    """One quarter of the per-share series, for a chart."""

    label: str
    gaap_eps: float | None = None
    adjusted_eps: float | None = None
    revenue: float | None = None
    end: str = ""


class QuarterlyEps(BaseModel):
    """One quarter's GAAP and adjusted EPS, with the bridge that produced it.

    Quarterly rather than annual because the adjustments are period-specific. Each
    quarter carries its own lines, tax rate and share count, so the figure
    describes a period a reader can act on rather than an average of several.
    """

    label: str = ""              # "2026 Q2"
    start: str = ""
    end: str = ""
    fiscal_label: str = ""
    gaap_eps: float | None = None
    adjusted_eps: float | None = None
    net_income: float | None = None
    adjusted_net_income: float | None = None
    revenue: float | None = None
    diluted_shares: float | None = None
    effective_tax_rate: float | None = None
    lines: list[NonGaapLine] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    has_adjustments: bool = False
    # Where this quarter came from: "sec" carries a full bridge, "analyst" carries
    # a reported EPS and consensus but no adjustment lines, because a foreign
    # private issuer's adjustments are not machine-readable.
    source: str = "sec"
    # Filled from the analyst feed where available, so the table can show what the
    # quarter was measured against rather than the actual alone.
    consensus_eps: float | None = None
    surprise_pct: float | None = None
    # The actual the surprise was computed from, and the basis both sides share.
    # A beat is only meaningful when the actual and the consensus are the same
    # measure; comparing a GAAP actual against a consensus that analysts forecast
    # on an adjusted basis produces nonsense.
    surprise_actual: float | None = None
    # Which of this row's two figures the consensus was compared against:
    # "adjusted" or "gaap". Analysts forecast a non-GAAP number, so the consensus
    # must be compared with the adjusted figure whenever the two differ materially;
    # comparing it with GAAP measures the gap between two definitions rather than
    # the gap between a result and a forecast.
    surprise_basis: str = ""
    # True when the reported actual and the consensus do not look like the same
    # measure. Kept as a warning for the case where even the adjusted figure is far
    # from the consensus, which points at a definition this site does not capture.
    surprise_mixed_basis: bool = False


class OptionsContract(BaseModel):
    """One listed contract.

    Greeks come from the CBOE feed, which publishes them. The Yahoo chain reports
    none, and its `implied_volatility` is placeholder data (1e-5 or exactly 0.5),
    so on that source volatility had to be inverted from last-traded prices. Both
    are carried here; the presence of `implied_volatility` does not by itself say
    which source produced it — the chain's `source` does.
    """

    kind: str                       # "call" | "put"
    strike: float
    expiration: date
    volume: float | None = None
    open_interest: float | None = None
    last_price: float | None = None
    change: float | None = None
    bid: float | None = None
    ask: float | None = None
    implied_volatility: float | None = None
    # Reported by CBOE; absent on the Yahoo chain.
    delta: float | None = None
    gamma: float | None = None
    vega: float | None = None
    theta: float | None = None
    rho: float | None = None
    in_the_money: bool = False
    contract_symbol: str = ""


class OptionsExpiry(BaseModel):
    """One expiry's calls and puts."""

    expiration: date
    calls: list[OptionsContract] = Field(default_factory=list)
    puts: list[OptionsContract] = Field(default_factory=list)


class OptionsExpirySummary(BaseModel):
    """Per-expiry put/call and positioning figures."""

    expiration: str
    days: int = 0
    call_volume: int = 0
    put_volume: int = 0
    call_oi: int = 0
    put_oi: int = 0
    volume_pcr: float | None = None
    oi_pcr: float | None = None
    max_pain: float | None = None
    straddle_move: float | None = None
    buckets: dict[str, int] = Field(default_factory=dict)


class OptionsChain(BaseModel):
    """Raw chain as fetched, before any interpretation."""

    ticker: str
    currency: str = "USD"
    spot: float | None = None
    as_of: datetime | None = None
    source: str = ""
    expiries_available: int = 0
    expiries: list[OptionsExpiry] = Field(default_factory=list)
    # Published by CBOE for the underlying, so IV/RV needs no inversion.
    iv30: float | None = None
    # The source's own terms, carried with the data rather than left implicit.
    compliance: str = ""
    # True when the chain reports Greeks, which decides whether a dealer-gamma
    # figure is measurable or would have to be assumed.
    has_greeks: bool = False


class OptionsVerdict(BaseModel):
    """The plain-language judgement the analysis tab leads with.

    Every field traces to volume or open interest. There is deliberately no
    volatility-regime field: the free chain's IV is placeholder data, so a
    "volatility is cheap" verdict would be invented rather than measured.
    """

    stance: str = ""                 # defensive | bullish | balanced
    stance_label: str = ""
    # A semantic key for the lean, so the UI can name it in either language.
    # `lean` remains as the API-level English fallback.
    lean_key: str = ""
    lean: str = ""
    novelty: str = ""                # new_defensive | new_bullish | aligned
    novelty_label: str = ""
    flow_gap: float | None = None
    chase_safety: int | None = None      # 1..5, from stated quantities
    put_value: int | None = None         # 1..5
    wait: str = ""                       # yes | no | expired
    max_pain_distance: float | None = None
    concentration_ratio: float | None = None
    concentration_label: str = ""
    # The next dated event, and whether it falls inside the reference expiry. An
    # earnings date within the option's life dominates a short-dated position and
    # is invisible in volume and open interest.
    next_earnings: str | None = None
    days_to_earnings: int | None = None
    earnings_in_window: bool = False


class OptionsVolatility(BaseModel):
    """Implied and realised volatility for the threshold tab.

    `basis` records where each side came from, because the two are different in
    kind: implied is forward-looking and inverted from traded prices, realised is
    backward-looking and measured from returns.
    """

    iv_atm: float | None = None
    iv_call: float | None = None
    iv_put: float | None = None
    rv_21d: float | None = None
    iv_rv: float | None = None
    iv_rank: float | None = None
    skew_points: float | None = None
    skew: dict = Field(default_factory=dict)
    expiry: str | None = None
    # How far out the reference expiry actually is, and how far apart the two
    # at-the-money legs inverted. Both are surfaced so a reader can judge the
    # reading rather than having to trust it.
    expiry_days: float | None = None
    leg_gap: float | None = None
    iv_note: str = ""
    basis: str = ""
    # "published iv30" when the source reports an implied vol, "inverted" when it
    # had to be recovered from traded prices.
    iv_source: str = ""


class OptionsSnapshot(BaseModel):
    """Computed options view for one ticker.

    Positioning from volume and open interest, plus volatility inverted from
    each contract's traded price. The chain's own `impliedVolatility` field is
    never read: it is placeholder data, so a volatility figure taken from it would
    be invented rather than measured.
    """

    ticker: str
    spot: float | None = None
    currency: str = "USD"
    as_of: datetime | None = None
    source: str = ""
    expiries_available: int = 0
    expiries_used: int = 0
    summaries: list[OptionsExpirySummary] = Field(default_factory=list)
    totals: dict[str, float | None] = Field(default_factory=dict)
    max_pain: float | None = None
    max_pain_expiry: str | None = None
    straddle_move: float | None = None
    concentration: list[dict] = Field(default_factory=list)
    unusual: list[dict] = Field(default_factory=list)
    # Traded contracts with no open interest yet: new positions, reported
    # separately because they have no book to form a ratio against.
    new_positions: list[dict] = Field(default_factory=list)
    # The next dated release, when known. It is the one catalyst invisible in
    # volume and open interest, so the verdict states it and flags it when it
    # falls inside the reference expiry.
    next_earnings: str | None = None
    # Dealer gamma exposure, only where the source reports Greeks. `gex_basis`
    # states the sign convention, because dealer positioning is not observable
    # and the figure is a convention rather than a measurement.
    gex: float | None = None
    gamma_flip: float | None = None
    gex_strikes: list[dict] = Field(default_factory=list)
    gex_basis: str = ""
    # The source's own terms, carried with the data rather than left implicit.
    compliance: str = ""
    verdict: OptionsVerdict = Field(default_factory=OptionsVerdict)
    volatility: OptionsVolatility = Field(default_factory=OptionsVolatility)
    notes: list[str] = Field(default_factory=list)


class CompanyEvent(BaseModel):
    """A dated corporate event, read from a filing index rather than prose.

    The item codes are the fact and the label is what that code means. Nothing
    here describes the *content* of the filing, because only the index is read —
    the event is reported as "material agreement, filed 2026-09-03", which is a
    verifiable claim, rather than a summary that would not be.
    """

    date: date
    form: str = "8-K"
    items: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    kind: str = "other"          # management | acquisition | strategic | results | other
    is_routine: bool = False
    url: str = ""


class Milestone(BaseModel):
    """One dated point on a company's record.

    `source` says where it came from, and that distinction is kept: a filing date
    is a fact, an analyst action is a third-party opinion, a headline is
    unverified. A table that mixed them without saying which was which would
    invite a reader to trust a rumour as much as an 8-K.
    """

    date: date
    kind: str = "other"          # agreement | acquisition | management | obligation | ...
    title: str = ""
    detail: str = ""
    source: str = "sec-8k"
    url: str = ""


class CatalystRow(BaseModel):
    """One dated item that could move the stock.

    `status` distinguishes a scheduled event from one already filed, and stays
    honest about the third case: whether a filed event *worked* is not something a
    filing index can say.
    """

    date: date
    period: str = ""
    title: str = ""
    watch: str = ""
    key_figures: str = ""
    status: str = "filed"        # pending | filed
    source: str = "sec-8k"
    kind: str = "other"
    scheduled: bool = False
    url: str = ""


class CompanyRecord(BaseModel):
    """A company's dated record and its catalyst list."""

    ticker: str
    milestones: list[Milestone] = Field(default_factory=list)
    catalysts: list[CatalystRow] = Field(default_factory=list)
    has_cik: bool = True
    note: str = ""


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
    # Set when EBITDA is not plain operating income + D&A, so a substitution is
    # never mistaken for the reported figure.
    ebitda_basis: str = ""
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
    # The bridge behind the adjusted figures, so any row can show its working.
    non_gaap: NonGaapReconciliation | None = None
    # Headline figures from that bridge, so they can sit beside GAAP EPS in the
    # growth block without the UI reaching into the reconciliation.
    non_gaap_eps: float | None = None
    gaap_to_adjusted_uplift: float | None = None

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
