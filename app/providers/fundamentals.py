"""Fundamentals providers.

SEC XBRL `companyfacts` is the primary source: it is authoritative, free, stable
and its figures were verified to match the existing workbook exactly (MSFT
revenue 331,839,000,000 / EPS 17.95 / assets 758,376,000,000).

`akshare` (Eastmoney financial statements) is the fallback for issuers that do
not file with the SEC or whose tags are unusable.
"""

from __future__ import annotations

import logging
import statistics
from datetime import date, datetime

from app.config import settings
from app.http import FetchError, fetch
from app.models import FactSeries, Fundamentals

log = logging.getLogger(__name__)

SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

_ANNUAL_FORMS = ("10-K", "20-F", "40-F")

# Ordered tag preferences — first usable series wins.
FLOW_TAGS: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ],
    "gross_profit": ["GrossProfit"],
    "cost_of_revenue": [
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsAndServicesSoldIncludingDepreciationDepletionAndAmortization",
        "CostOfGoodsSold",
    ],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        # Qualcomm and others use the broader "productive assets" tag instead.
        "PaymentsToAcquireProductiveAssets",
        "PaymentsToAcquireOtherProductiveAssets",
    ],
    "sbc": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
    "restructuring": ["RestructuringCharges"],
    "amortization": ["AmortizationOfIntangibleAssets"],
    "depreciation_amortization": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationDepletionAndAmortizationPropertyPlantAndEquipment",
        "Depreciation",
    ],
    "tax_provision": ["IncomeTaxExpenseBenefit"],
    "pretax_income": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ],
}

INSTANT_TAGS: dict[str, list[str]] = {
    "equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "assets": ["Assets"],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "debt_current": ["ShortTermBorrowings", "LongTermDebtCurrent"],
    "debt_long": ["LongTermDebtNoncurrent", "LongTermDebt"],
}

UNITS: dict[str, list[str]] = {
    "eps_diluted": ["USD/shares"],
    "shares_diluted": ["shares"],
}

# --------------------------------------------------------------------------- #
# IFRS taxonomy (`ifrs-full`), used by foreign private issuers such as TSM that
# file 20-F instead of 10-K. Tag names differ substantially from US-GAAP, so
# they get their own mapping rather than being forced through the GAAP lists.
# --------------------------------------------------------------------------- #
IFRS_FLOW_TAGS: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractsWithCustomers",
        "Revenue",
        "RevenueAndOperatingIncome",
    ],
    "gross_profit": ["GrossProfit"],
    "cost_of_revenue": ["CostOfSales", "CostOfRevenue"],
    "operating_income": [
        "ProfitLossFromOperatingActivities",
        "ProfitLossFromContinuingOperations",
    ],
    "net_income": ["ProfitLoss", "ProfitLossAttributableToOwnersOfParent"],
    "operating_cash_flow": [
        "CashFlowsFromUsedInOperatingActivities",
        "CashFlowsFromUsedInOperatingActivitiesBeforeIncomeTax",
    ],
    "capex": [
        "PurchaseOfPropertyPlantAndEquipment",
        # TSMC tags capex with the "...ClassifiedAsInvestingActivities" variant
        # only, so the three plain names above (which the IFRS taxonomy also
        # allows) matched nothing and capex -- and with it FCF and P/FCF -- came
        # back empty for TSM.
        "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        "PurchaseOfPropertyPlantAndEquipmentIntangibleAssetsOtherThanGoodwill",
        "AcquisitionOfPropertyPlantAndEquipment",
    ],
    "depreciation_amortization": [
        "DepreciationAndAmortisationExpense",
        "DepreciationExpense",
    ],
    # The IFRS taxonomy has no single "share-based payment expense" tag that FI
    # filers agree on; TSMC reports it as the cash-flow add-back. `amortization`
    # is spelled the IFRS way here and is added to D&A when a filer has no
    # combined depreciation-and-amortisation tag (see `metrics`).
    "amortization": ["AmortisationExpense", "AmortizationOfIntangibleAssets"],
    "sbc": [
        "ShareBasedPaymentExpense",
        "ExpenseFromSharebasedPaymentTransactionsWithEmployees",
        "AdjustmentsForSharebasedPayments",
    ],
    "tax_provision": [
        "IncomeTaxExpenseContinuingOperations",
        "CurrentTaxExpenseIncomeAndOtherComponentsOfIncomeTaxExpense",
        "TaxExpenseIncome",
    ],
    "pretax_income": ["ProfitLossBeforeTax", "AccountingProfit"],
}

IFRS_INSTANT_TAGS: dict[str, list[str]] = {
    "equity": [
        "EquityAttributableToOwnersOfParent",
        "Equity",
        "EquityAndLiabilities",
    ],
    "assets": ["Assets"],
    "cash": ["CashAndCashEquivalents", "CashAndCashEquivalentsAndShortTermInvestments"],
    "debt_current": [
        "CurrentPortionOfLongtermBorrowings",
        "ShorttermBorrowings",
        "CurrentBorrowings",
    ],
    "debt_long": ["LongtermBorrowings", "NoncurrentPortionOfLongtermBorrowings"],
}

# Diluted first: the field is `eps_diluted`, and the two tags reach the same
# period, so the tag order -- not the period -- decides which one is used.
IFRS_EPS_TAGS = ["DilutedEarningsLossPerShare", "BasicEarningsLossPerShare"]
IFRS_SHARES_TAGS = [
    "WeightedAverageNumberOfOrdinarySharesOutstandingDiluted",
    # TSMC tags the weighted average under the shorter IFRS name.
    "WeightedAverageShares",
    "WeightedAverageNumberOfSharesOutstandingBasic",
]

# Concepts used to recover the FX rate a filer used for its US$ convenience
# translation. Each must be a *large monetary* amount that the filing carries in
# both currencies, so that `filing_value / trading_value` is the rate itself
# rather than a rounded per-share or percentage figure. Fourteen independent
# concepts agreeing to within a few parts per million is what makes the derived
# rate trustworthy (see `tests/test_fundamentals.py`).
_FX_RATE_TAGS = (
    "Assets",
    "Equity",
    "EquityAndLiabilities",
    "CurrentAssets",
    "NoncurrentAssets",
    "CurrentLiabilities",
    "NoncurrentLiabilities",
    "Liabilities",
    "Revenue",
    "ProfitLoss",
    "ProfitLossBeforeTax",
    "ProfitLossFromOperatingActivities",
    "CashFlowsFromUsedInOperatingActivities",
    "PropertyPlantAndEquipment",
    "Inventories",
    "RetainedEarnings",
)

# Stand-in for "we could not establish the filing currency". Never a real
# currency, and deliberately not "USD": `metrics.compute_row` withholds the
# price-based figures whenever the filing currency differs from the traded one,
# so an unknown label must compare unequal to USD rather than default to it.
UNKNOWN_CURRENCY = "UNKNOWN"

# A derived rate further than this from the median of its own period is treated
# as a mis-tagged or rounded figure and dropped.
_FX_OUTLIER_TOLERANCE = 0.02

# Fields whose series are plain money amounts in the filing currency.
_MONEY_FIELDS = (
    "revenue",
    "gross_profit",
    "cost_of_revenue",
    "operating_income",
    "net_income",
    "equity",
    "assets",
    "cash",
    "operating_cash_flow",
    "capex",
    "sbc",
    "restructuring",
    "amortization",
    "depreciation_amortization",
    "tax_provision",
    "pretax_income",
    "debt_current",
    "debt_long",
)



# --------------------------------------------------------------------------- #
# Ticker -> CIK map (cached for a day)
# --------------------------------------------------------------------------- #
def load_ticker_map() -> dict[str, dict]:
    try:
        raw = fetch(
            SEC_TICKERS,
            headers={
                "User-Agent": settings.sec_user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
            namespace="sec_map",
            ttl=settings.cache_ttl_secmap,
            expect_json=True,
            retries=3,
        )
    except FetchError as exc:
        log.warning("SEC ticker map unavailable: %s", exc)
        return {}
    out: dict[str, dict] = {}
    for entry in (raw or {}).values():
        try:
            symbol = str(entry.get("ticker", "")).upper()
            if symbol:
                out[symbol] = {
                    "cik": int(entry["cik_str"]),
                    "title": str(entry.get("title", "")),
                }
        except (KeyError, TypeError, ValueError):
            continue
    return out


def lookup_cik(ticker: str) -> int | None:
    entry = load_ticker_map().get(ticker.upper())
    return entry["cik"] if entry else None


# --------------------------------------------------------------------------- #
# Extraction helpers
# --------------------------------------------------------------------------- #
def _dedupe_latest_filing(points: list[dict]) -> list[dict]:
    """For each period end keep the most recently filed value."""
    best: dict[str, dict] = {}
    for p in points:
        key = p["end"]
        if key not in best or str(p.get("filed", "")) > str(best[key].get("filed", "")):
            best[key] = p
    return sorted(best.values(), key=lambda x: x["end"], reverse=True)


def _flow_series(gaap: dict, tags: list[str], unit: str = "USD") -> FactSeries | None:
    """Annual income/cash-flow facts: needs a start date and ~1 year duration.

    Selection is by *period*, not by tag order. US issuers routinely migrate
    tags (Alphabet tagged FY2025 revenue as `Revenues` while earlier years used
    `RevenueFromContractWithCustomerExcludingAssessedTax`), so taking the first
    tag that yields any series silently returns a stale year. Instead every
    candidate tag is scanned and the series containing the newest period wins.
    """
    candidates: list[tuple[str, str, list[tuple[date, float]]]] = []
    for tag in tags:
        node = gaap.get(tag)
        if not node:
            continue
        available_units = [unit] if unit else list(node.get("units", {}))
        for u in available_units:
            points = node.get("units", {}).get(u) or []
            rows = []
            for p in points:
                if p.get("form") not in _ANNUAL_FORMS or p.get("fp") != "FY":
                    continue
                if p.get("val") is None or not p.get("start") or not p.get("end"):
                    continue
                try:
                    span = (date.fromisoformat(p["end"]) - date.fromisoformat(p["start"])).days
                except ValueError:
                    continue
                if 330 <= span <= 380:
                    rows.append(p)
            if not rows:
                continue
            series = [
                (date.fromisoformat(p["end"]), float(p["val"]))
                for p in _dedupe_latest_filing(rows)
            ]
            if series:
                candidates.append((tag, u, series))

    if not candidates:
        return None
    tag, u, series = max(candidates, key=lambda c: c[2][0][0])
    return FactSeries(tag=tag, unit=u, points=series)


def _instant_series(gaap: dict, tags: list[str], unit: str = "USD") -> FactSeries | None:
    """Balance-sheet facts: a point in time, so no `start` is present.

    As with `_flow_series`, the series holding the newest period wins across all
    candidate tags so a stale tag cannot mask the current balance sheet.
    """
    candidates: list[tuple[str, str, list[tuple[date, float]]]] = []
    for tag in tags:
        node = gaap.get(tag)
        if not node:
            continue
        available_units = [unit] if unit else list(node.get("units", {}))
        for u in available_units:
            points = node.get("units", {}).get(u) or []
            rows = [
                p
                for p in points
                if p.get("form") in _ANNUAL_FORMS and not p.get("start") and p.get("val") is not None
            ]
            if not rows:
                continue
            series = [
                (date.fromisoformat(p["end"]), float(p["val"]))
                for p in _dedupe_latest_filing(rows)
            ]
            if series:
                candidates.append((tag, u, series))

    if not candidates:
        return None
    tag, u, series = max(candidates, key=lambda c: c[2][0][0])
    return FactSeries(tag=tag, unit=u, points=series)


def _latest_dei_shares(facts: dict) -> float | None:
    """Cover-page share count (`dei:EntityCommonStockSharesOutstanding`).

    `facts` is the whole companyfacts payload, so the dei block lives under
    `facts["facts"]["dei"]` -- the previous `facts["dei"]` lookup always missed
    and returned None, which is why `share_count` never fell back to the cover
    page and `shares_basis` never reported the SEC DEI source for any issuer.

    The companyfacts API strips dimensional members, so a dual-class filer
    surfaces one undimensioned entry per class at the same period end; those are
    summed (duplicates of the same class are collapsed first) to recover a total.
    """
    node = ((facts.get("facts") or {}).get("dei") or {}).get(
        "EntityCommonStockSharesOutstanding"
    )
    if not node:
        return None

    # (period end, value) -> latest filing date that reported it.
    latest: dict[tuple[str, float], str] = {}
    for points in (node.get("units") or {}).values():
        for p in points:
            if p.get("form") not in _ANNUAL_FORMS + ("10-Q",) or p.get("val") is None:
                continue
            key = (str(p.get("end", "")), float(p["val"]))
            filed = str(p.get("filed", ""))
            if key not in latest or filed > latest[key]:
                latest[key] = filed
    if not latest:
        return None
    newest = max(end for end, _ in latest)
    total = sum(value for end, value in latest if end == newest)
    return total or None


# --------------------------------------------------------------------------- #
# SEC provider
# --------------------------------------------------------------------------- #
def _monetary_units(node_source: dict, flow_tags: dict, instant_tags: dict) -> list[str]:
    """The currency (or other monetary) units a taxonomy carries, best first.

    Only *monetary* tags are inspected. Scanning arbitrary tags is wrong: XBRL
    unit keys also include things like `Year`, `Store` and `instrument`, which
    would masquerade as a currency (this previously mislabelled Apple as
    reporting in "Year" and tripped the cross-currency guard).

    The first entry is the reporting currency, taken from the primary statement
    tags in the order they are declared. Everything downstream must be read in
    *that* unit: a foreign private issuer commonly tags its statements in the
    home currency **and** a US$ convenience translation of them (TSM files TWD
    statements plus 9 years of translated USD), and mixing the two within one
    `Fundamentals` is silently wrong. Reading the units also lets the caller warn
    when a taxonomy offers more than one.
    """
    monetary_flow = ("revenue", "net_income", "operating_income", "assets", "equity", "cash")
    units: list[str] = []
    for group in (flow_tags, instant_tags):
        for field in monetary_flow:
            for tag in group.get(field, []):
                node = node_source.get(tag)
                if not node:
                    continue
                for unit in (node.get("units") or {}):
                    if unit and unit not in ("shares", "pure", "YTD") and "/" not in unit:
                        if unit not in units:
                            units.append(unit)
    return units


def get_sec_fundamentals(ticker: str, trading_currency: str | None = None) -> Fundamentals | None:
    cik = lookup_cik(ticker)
    if cik is None:
        log.info("%s is not in the SEC issuer map", ticker)
        return None
    url = SEC_FACTS.format(cik=cik)
    facts = fetch(
        url,
        headers={
            "User-Agent": settings.sec_user_agent,
            "Accept-Encoding": "gzip, deflate",
        },
        namespace="sec_facts",
        ttl=settings.cache_ttl_fundamentals,
        expect_json=True,
        timeout=60,
        retries=4,
    )
    all_facts = facts.get("facts") or {}
    gaap = all_facts.get("us-gaap") or {}
    ifrs = all_facts.get("ifrs-full") or {}

    # Prefer US-GAAP when present; foreign private issuers file IFRS only.
    if gaap:
        taxonomy, flow_tags, instant_tags = "us-gaap", FLOW_TAGS, INSTANT_TAGS
        eps_tags, shares_tags = ["EarningsPerShareDiluted"], [
            "WeightedAverageNumberOfDilutedSharesOutstanding"
        ]
        taxonomy_label = "US-GAAP"
    elif ifrs:
        taxonomy, flow_tags, instant_tags = "ifrs-full", IFRS_FLOW_TAGS, IFRS_INSTANT_TAGS
        eps_tags, shares_tags = IFRS_EPS_TAGS, IFRS_SHARES_TAGS
        taxonomy_label = "IFRS"
    else:
        log.info("%s (CIK %s) has no us-gaap or ifrs-full facts", ticker, cik)
        return None

    node_source = gaap if taxonomy == "us-gaap" else ifrs
    units = _monetary_units(node_source, flow_tags, instant_tags)
    # The primary statements' unit. Reading every series in *this* unit is what
    # keeps one `Fundamentals` internally consistent; see `_monetary_units`.
    currency = units[0] if units else "USD"

    fund = Fundamentals(
        ticker=ticker,
        entity_name=facts.get("entityName"),
        currency=currency,
        source=f"SEC XBRL ({taxonomy_label})",
        source_url=url,
        cik=cik,
    )
    if len(units) > 1:
        fund.notes.append(
            f"{ticker} tags its statements in {currency} and also files a "
            f"{', '.join(units[1:])} convenience translation; {currency} used throughout"
        )

    for field, tags in flow_tags.items():
        setattr(fund, field, _flow_series(node_source, tags, unit=currency))
    for field, tags in instant_tags.items():
        setattr(fund, field, _instant_series(node_source, tags, unit=currency))

    # Per-share figures carry a `CUR/shares` unit key, so they must follow the
    # same currency as the statements. Reading them with `unit=None` picked
    # whichever series happened to end latest -- for TSM that was TWD/share
    # alongside USD-translated statements.
    fund.eps_diluted = _flow_series(node_source, eps_tags, unit=f"{currency}/shares")
    fund.shares_diluted = _flow_series(node_source, shares_tags, unit="shares")
    fund.shares_outstanding = _latest_dei_shares(facts)
    diluted = fund.shares_diluted.latest() if fund.shares_diluted else None
    if fund.shares_outstanding and diluted and not (
        0.9 <= fund.shares_outstanding / diluted <= 1.1
    ):
        # companyfacts drops dimensional members, so a dual-class filer can
        # surface a single class on the cover page; taking it would silently
        # understate that issuer's market cap. A cover-page count within a
        # plausible basic-vs-diluted spread is accepted, anything else falls back
        # to the annual weighted-average series.
        fund.notes.append(
            f"cover-page share count ({fund.shares_outstanding:,.0f}) is not "
            f"consistent with annual diluted shares ({diluted:,.0f}); annual figure used"
        )
        fund.shares_outstanding = None
    if fund.shares_outstanding:
        fund.shares_basis = "Latest common shares (SEC DEI)"

    # Anchor on the newest fiscal period present in any statement series, so a
    # stale tag on one line item cannot pull the row back a year. Revenue leads
    # because it is the primary driver; other series may legitimately end
    # slightly later (e.g. a balance sheet dated a few weeks after the P&L).
    anchor_candidates = [
        s.latest_end()
        for s in (
            fund.revenue,
            fund.net_income,
            fund.eps_diluted,
            fund.operating_income,
            fund.assets,
        )
        if s is not None and s.points
    ]
    if anchor_candidates:
        fund.fiscal_end = max(anchor_candidates)

    if not fund.is_usable:
        log.info("%s: SEC facts incomplete (no revenue or operating income)", ticker)
    return _restate_in_trading_currency(fund, node_source, trading_currency)


# --------------------------------------------------------------------------- #
# ADR / cross-currency conversion
# --------------------------------------------------------------------------- #
# Why this exists: a foreign private issuer files in its home currency while its
# ADR trades in USD, so "market cap (USD) / revenue (TWD)" is meaningless. The
# engine's cross-currency guard withholds those figures rather than publishing
# nonsense, which is correct but leaves TSM with an empty valuation dimension.
# Both pieces needed to do better are available without a new data source:
#
#   * the FX rate -- a 20-F that carries a US$ convenience translation contains
#     every figure twice, so the ratio of the two values *is* the rate the filer
#     used, per period, straight out of the filing (`_implied_fx_rates`);
#   * the ADS ratio -- a documentary term of the deposit agreement, not an XBRL
#     fact, so it is configured per issuer (see `settings.ads_ratios`, which
#     cites TSMC's 20-F for the default).
#
# With both, the whole `Fundamentals` is restated into the trading currency on
# the ADS basis, the guard no longer has a mismatch to flag, and every
# price-based multiple computes normally.
def _annual_points(points: list[dict]) -> dict[date, float]:
    """Latest-filed annual value per period end, for flows and instants alike.

    Both a 12-month flow and an instant balance-sheet figure are keyed by their
    period end, so a quarterly figure that happens to share the fiscal year end
    must be excluded -- otherwise a 3-month TWD amount would be divided by a
    12-month USD amount and produce a plausible-looking but wrong rate.
    """
    best: dict[date, dict] = {}
    for p in points:
        if p.get("form") not in _ANNUAL_FORMS or p.get("fp") != "FY" or p.get("val") is None:
            continue
        try:
            end = date.fromisoformat(str(p["end"]))
        except (TypeError, ValueError):
            continue
        if p.get("start"):
            try:
                span = (end - date.fromisoformat(str(p["start"]))).days
            except (TypeError, ValueError):
                continue
            if not 330 <= span <= 380:
                continue
        if end not in best or str(p.get("filed", "")) > str(best[end].get("filed", "")):
            best[end] = p

    out: dict[date, float] = {}
    for end, p in best.items():
        try:
            out[end] = float(p["val"])
        except (TypeError, ValueError):
            continue
    return out


def _implied_fx_rates(
    node_source: dict, filing_currency: str, trading_currency: str
) -> dict[date, float]:
    """The rate the filer itself used, as `filing units per one trading unit`.

    Derived from the filing's dual-currency figures (see `_FX_RATE_TAGS`) and
    reduced to the median per period, so a single mis-tagged concept cannot move
    the rate. TSM's FY2024 comes out at 32.79 TWD/USD, which is the rate its
    20-F states and the one its own US$ column was produced with.
    """
    filing = (filing_currency or "").upper()
    trading = (trading_currency or "").upper()
    candidates: dict[date, list[float]] = {}
    for tag in _FX_RATE_TAGS:
        node = node_source.get(tag)
        units = (node or {}).get("units") or {}
        if filing not in units or trading not in units:
            continue
        left = _annual_points(units[filing])
        right = _annual_points(units[trading])
        for end, value in left.items():
            other = right.get(end)
            # A rate is positive by definition, so a non-positive pair means the
            # two series are not the same figure in two currencies.
            if other is not None and value > 0 and other > 0:
                candidates.setdefault(end, []).append(value / other)

    rates: dict[date, float] = {}
    for end, values in candidates.items():
        median = statistics.median(values)
        kept = [v for v in values if abs(v - median) <= median * _FX_OUTLIER_TOLERANCE]
        rates[end] = statistics.median(kept) if kept else median
    return rates


def _rate_for(end: date, rates: dict[date, float]) -> float | None:
    """The rate for `end`; the nearest translated period otherwise.

    A filing only translates a rolling window (TSM: FY2016-FY2024), while a
    series can be longer. Reusing the nearest translated period keeps every
    series complete and in one currency, at the cost of a few percent of rate
    drift on the oldest years -- which only reach growth/CAGR, never a level
    figure, because the anchor year is always inside the window. Callers record
    that approximation in the notes.
    """
    if not rates:
        return None
    if end in rates:
        return rates[end]
    return rates[min(rates, key=lambda known: abs((known - end).days))]


def convert_to_trading_currency(
    fund: Fundamentals, trading_currency: str, *, rates: dict[date, float], ads_ratio: float
) -> Fundamentals:
    """Restate a filing in the currency its shares trade in, on an ADS basis.

    Money and per-share figures both move: the traded price is per ADS, so the
    EPS bridge's denominator and the share count behind market cap have to be
    ADS-equivalent too (TSM: 25.93bn common shares -> 5.19bn ADSs), otherwise
    `price x shares` overstates market cap by the ADS ratio.
    """
    filing = (fund.currency or "").upper()
    trading = trading_currency.upper()
    out = fund.model_copy(deep=True)

    # Which fiscal years fall outside the filing's own translation window.
    covered = {end.year for end in rates}
    used = {
        end.year
        for field in _MONEY_FIELDS + ("eps_diluted",)
        for end, _ in (getattr(out, field).points if getattr(out, field) else [])
    }
    approximated = sorted(used - covered)

    def restate(
        series: FactSeries | None, transform, unit: str | None = None
    ) -> FactSeries | None:
        if series is None or not series.points:
            return series
        points = []
        for end, value in series.points:
            rate = _rate_for(end, rates)
            if rate is None:
                continue
            points.append((end, transform(value, rate)))
        return FactSeries(tag=series.tag, unit=unit or trading, points=points)

    for field in _MONEY_FIELDS:
        setattr(out, field, restate(getattr(out, field), lambda value, rate: value / rate))

    # Per ordinary share -> per ADS, in the trading currency.
    out.eps_diluted = restate(
        out.eps_diluted, lambda value, rate: value / rate * ads_ratio, unit=f"{trading}/ADS"
    )

    shares = out.shares_diluted
    if shares is not None and shares.points:
        out.shares_diluted = FactSeries(
            tag=shares.tag,
            unit="shares",
            points=[(end, value / ads_ratio) for end, value in shares.points],
        )
    if out.shares_outstanding:
        out.shares_outstanding = out.shares_outstanding / ads_ratio
    # Keep the basis the count came from (SEC cover page, annual diluted series,
    # Eastmoney) so the converted number is still traceable to its source.
    out.shares_basis = (
        f"ADS-equivalent of {fund.shares_basis or 'annual diluted shares'}: "
        f"1 ADS = {ads_ratio:g} common shares"
    )

    out.filing_currency = filing
    out.currency = trading
    out.ads_ratio = ads_ratio
    out.fx_rates = {end.isoformat(): rate for end, rate in sorted(rates.items())}
    newest = max(rates)
    note = (
        f"converted {filing} -> {trading} at the rate in the issuer's own SEC filing "
        f"({filing} {rates[newest]:g} = 1 {trading}, FY{newest.year}); "
        f"1 ADS = {ads_ratio:g} common shares"
    )
    if approximated:
        years = ", ".join(f"FY{y}" for y in approximated)
        note += f"; {years} predate the filing's US$ column and reuse its earliest rate"
    out.notes.append(note)
    return out


def _restate_in_trading_currency(
    fund: Fundamentals, node_source: dict, trading_currency: str | None
) -> Fundamentals:
    """Convert when both an FX rate and an ADS ratio are available, else explain."""
    trading = (trading_currency or "").strip().upper()
    filing = (fund.currency or "").strip().upper()
    if not trading or trading == filing:
        return fund

    rates = _implied_fx_rates(node_source, filing, trading)
    ratio = settings.ads_ratio_map().get(fund.ticker.upper())

    if not rates:
        fund.notes.append(
            f"filed in {filing} but trades in {trading}, and this filing carries no "
            f"{trading} column, so price-based measures stay withheld"
        )
        return fund
    if not ratio:
        fund.notes.append(
            f"filed in {filing} but trades in {trading}; set FR_ADS_RATIOS to the common "
            f"shares per ADS (e.g. \"{fund.ticker}=5\") to convert, otherwise a {trading} "
            f"price cannot be divided into {filing} per-share figures"
        )
        return fund
    return convert_to_trading_currency(fund, trading, rates=rates, ads_ratio=ratio)


# --------------------------------------------------------------------------- #
# akshare fallback (Eastmoney financial statements)
# --------------------------------------------------------------------------- #
# Two traps are baked into this interface, and both of them bit TSM:
#
# 1. The *same* `ITEM_NAME` appears in more than one statement with a different
#    meaning. `净利润` is after-tax on the income statement, but on the cash-flow
#    statement it is the starting pre-tax figure (there it equals
#    `持续经营税前利润` -- TSM FY2025: NT$2,041.7bn vs the income statement's
#    NT$1,695.1bn, a NT$346.5bn tax charge apart). Flattening the three sheets
#    into one name -> value map therefore overwrote net income with the cash-flow
#    number, which silently corrupts net margin, ROE and cash conversion. Every
#    field below is resolved from the one statement that owns it.
#
# 2. Lookups must be exact, not substring. `负债及股东权益合计` (total liabilities
#    *and* equity) contains `负债`, so a "contains" match for debt would return
#    the entire balance sheet total.
#
# The names themselves are Eastmoney's fixed Chinese vocabulary for US filings
# (verified against TSM and MSFT), which is *not* the vocabulary this module
# originally guessed: it looked for `基本每股收益`, `稀释每股收益` and
# `购建固定资产、无形资产和其他长期资产支付的现金`, none of which this interface
# ever emits. That mismatch is why EPS, cost of revenue, capex, debt, SBC, D&A,
# tax and share counts all came back empty for TSM.
_AK_FIELDS: dict[str, tuple[str, tuple[str, ...]]] = {
    # -- 综合损益表 (income statement) --
    "revenue": ("income", ("营业收入", "主营收入", "营业总收入")),
    "gross_profit": ("income", ("毛利", "毛利润")),
    "cost_of_revenue": ("income", ("营业成本", "主营成本")),
    "operating_income": ("income", ("营业利润", "经营溢利")),
    # Parent-attributable income pairs with parent equity below, so ROE compares
    # like with like on issuers that carry non-controlling interests (TSM does).
    "net_income": (
        "income",
        ("归属于母公司股东净利润", "归属于普通股股东净利润", "净利润", "持续经营净利润"),
    ),
    # Per *ordinary share* on purpose: every other figure here is in the filing
    # currency, so mixing in the per-ADS line would silently scale TSM by 5.
    "eps_diluted": (
        "income",
        ("摊薄每股收益-普通股", "基本每股收益-普通股", "摊薄每股收益", "稀释每股收益"),
    ),
    "shares_diluted": ("income", ("摊薄加权平均股数-普通股", "基本加权平均股数-普通股")),
    "pretax_income": ("income", ("持续经营税前利润", "税前利润")),
    "tax_provision": ("income", ("所得税", "所得税费用")),
    # -- 现金流量表 (cash-flow statement) --
    "operating_cash_flow": (
        "cashflow",
        ("经营活动产生的现金流量净额", "经营业务现金净额"),
    ),
    "capex": (
        "cashflow",
        (
            "购建固定资产、无形资产和其他长期资产支付的现金",
            "购建固定资产无形资产和其他长期资产支付的现金",
            "购买固定资产",
        ),
    ),
    "sbc": ("cashflow", ("基于股票的补偿费", "股权激励")),
    "depreciation_amortization": ("cashflow", ("折旧及摊销", "折旧与摊销")),
    # -- 资产负债表 (balance sheet) --
    "assets": ("balance", ("总资产",)),
    "equity": ("balance", ("归属于母公司股东权益", "股东权益合计", "总权益")),
    "cash": ("balance", ("现金及现金等价物", "现金及银行存款")),
    "debt_current": ("balance", ("短期债务", "长期负债(本期部分)", "短期借款")),
    "debt_long": ("balance", ("长期负债", "资本租赁债务(非流动)")),
}

_AK_SHEETS = {
    "income": "综合损益表",
    "cashflow": "现金流量表",
    "balance": "资产负债表",
}

# Eastmoney's Chinese currency labels -> ISO codes. `CURRENCY_ABBR` already
# carries the ISO code on every row we have seen; this is the fallback when only
# the display name is present.
_AK_CURRENCIES = {
    "美元": "USD",
    "新台币": "TWD",
    "港元": "HKD",
    "人民币": "CNY",
    "日元": "JPY",
    "欧元": "EUR",
    "英镑": "GBP",
}


def _ak_frames(ticker: str) -> tuple[dict[str, list[dict]], list[str]]:
    """Yearly statements keyed by our own sheet name, plus failure notes.

    Sheets are kept apart (see the note above `_AK_FIELDS`) and a sheet that
    fails is reported instead of being silently dropped: a missing balance sheet
    alone removes assets, equity, cash and debt, which is exactly the kind of
    invisible hole this used to leave.
    """
    import akshare as ak

    frames: dict[str, list[dict]] = {}
    notes: list[str] = []
    for key, sheet in _AK_SHEETS.items():
        try:
            df = ak.stock_financial_us_report_em(stock=ticker, symbol=sheet, indicator="年报")
        except Exception as exc:  # noqa: BLE001 - one sheet must not lose the rest
            log.debug("akshare %s %s failed: %s", ticker, sheet, exc)
            notes.append(f"akshare {sheet} unavailable: {type(exc).__name__}: {exc}")
            continue
        if df is None or df.empty:
            notes.append(f"akshare {sheet} returned no rows")
            continue
        frames[key] = df.to_dict("records")
    return frames, notes


def _ak_meta(ticker: str) -> tuple[str | None, str | None]:
    """Reporting entity name and ISO currency, straight from Eastmoney.

    The statements themselves carry no currency column, and assuming USD is
    wrong for exactly the issuers this fallback exists to serve: TSMC's figures
    are in NT$. `metrics.compute_row` relies on this label to withhold
    price-based ratios when the filing currency is not the trading currency, so
    guessing here would publish USD market caps against NT$ revenue.
    """
    import akshare as ak

    df = ak.stock_financial_us_analysis_indicator_em(symbol=ticker, indicator="年报")
    if df is None or df.empty:
        return None, None
    latest = df.iloc[0]
    name = str(latest.get("SECURITY_NAME_ABBR") or "").strip() or None

    abbr = str(latest.get("CURRENCY_ABBR") or "").strip().upper()
    if len(abbr) == 3 and abbr.isalpha():
        return name, abbr
    return name, _AK_CURRENCIES.get(str(latest.get("CURRENCY") or "").strip())


def _ak_series(
    records: list[dict], names: tuple[str, ...], unit: str
) -> FactSeries | None:
    """First matching line item, as a newest-first annual series."""
    if not records:
        return None
    for name in names:
        rows = [r for r in records if str(r.get("ITEM_NAME", "")).strip() == name]
        if not rows:
            continue
        by_end: dict[date, float] = {}
        for r in rows:
            try:
                end = datetime.fromisoformat(str(r["REPORT_DATE"])[:19]).date()
                value = float(str(r["AMOUNT"]).strip())
            except (KeyError, TypeError, ValueError):
                continue
            # `float("nan")` is a successful conversion, so a blank cell would
            # otherwise travel all the way into the API response as NaN -- which
            # is not valid JSON and breaks the browser's parse.
            if value != value or value in (float("inf"), float("-inf")):
                continue
            by_end[end] = value
        points = sorted(by_end.items(), key=lambda kv: kv[0], reverse=True)
        if points:
            return FactSeries(tag=f"akshare:{name}", unit=unit, points=points)
    return None


def get_akshare_fundamentals(
    ticker: str, trading_currency: str | None = None
) -> Fundamentals | None:
    frames, notes = _ak_frames(ticker)
    if not frames:
        return None

    try:
        entity_name, currency = _ak_meta(ticker)
    except Exception as exc:  # noqa: BLE001 - the label is worth a request, not a failure
        log.debug("akshare analysis indicators failed for %s: %s", ticker, exc)
        entity_name, currency = None, None
    if currency is None:
        if settings.ads_ratio_map().get(ticker.upper()):
            # A configured ADS ratio means this ticker is known to be an ADR, so
            # its statements are in the issuer's home currency. Assuming USD here
            # would put a USD price against home-currency per-share figures, and
            # the cross-currency guard would never fire to catch it.
            currency = UNKNOWN_CURRENCY
            notes.append(
                "reporting currency could not be confirmed (Eastmoney indicators "
                "unavailable); price-based measures withheld rather than assumed to be USD"
            )
        else:
            # Keep the historical default, but say so rather than implying the
            # filing is known to be in USD.
            currency = "USD"
            notes.append("reporting currency could not be confirmed; assuming USD")

    fund = Fundamentals(
        ticker=ticker,
        entity_name=entity_name,
        currency=currency,
        source="akshare/Eastmoney financial statements",
        source_url="https://emweb.securities.eastmoney.com/",
        notes=list(notes),
    )

    for field, (sheet, names) in _AK_FIELDS.items():
        unit = "shares" if field == "shares_diluted" else currency
        series = _ak_series(frames.get(sheet, []), names, unit)
        if series:
            setattr(fund, field, series)

    if fund.shares_diluted:
        fund.shares_basis = "Approximation: annual diluted shares (Eastmoney)"

    # Eastmoney republishes the statements in the *filing* currency only, with no
    # US$ column, so unlike the SEC path there is no rate to derive here. Say so
    # explicitly instead of letting the guard's generic note stand: for an ADR
    # this is the difference between "the data is unavailable" and "we are on the
    # fallback source and need the filing".
    trading = (trading_currency or "").strip().upper()
    if trading and trading != (currency or "").upper() and settings.ads_ratio_map().get(
        ticker.upper()
    ):
        fund.notes.append(
            f"filed in {currency} but trades in {trading}; Eastmoney carries no "
            f"{trading} column, so the conversion needs the SEC filing's US$ figures"
        )

    # Revenue is the primary anchor; the other two may legitimately end later.
    for series in (fund.revenue, fund.net_income, fund.eps_diluted):
        if series and series.points:
            fund.fiscal_end = series.latest_end()
            break
    return fund if fund.is_usable else None


# --------------------------------------------------------------------------- #
def get_fundamentals(ticker: str, *, trading_currency: str | None = None) -> Fundamentals:
    """Resolve annual fundamentals, SEC first then akshare.

    `trading_currency` is the currency the security's price is quoted in. It is
    what lets a foreign private issuer's filing be restated onto the ADR basis
    instead of having its price-based figures withheld; pass it whenever the
    quote is already known (the pipeline does).
    """
    ticker = ticker.upper().strip()
    errors: list[str] = []

    try:
        sec = get_sec_fundamentals(ticker, trading_currency=trading_currency)
        if sec and sec.is_usable:
            return sec
        errors.append("SEC: incomplete")
    except Exception as exc:
        errors.append(f"SEC: {exc}")
        log.info("SEC fundamentals failed for %s: %s", ticker, exc)

    if settings.enable_akshare:
        try:
            ak_fund = get_akshare_fundamentals(ticker, trading_currency=trading_currency)
            if ak_fund and ak_fund.is_usable:
                return ak_fund
            errors.append("akshare: incomplete")
        except Exception as exc:
            errors.append(f"akshare: {exc}")

    raise FetchError(f"no fundamentals for {ticker} ({'; '.join(errors)})")
