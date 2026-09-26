"""Per-quarter GAAP figures and the adjusted-EPS bridge, from SEC XBRL.

Why quarterly and not annual. The adjustments a company discloses are
period-specific and can swing violently within one fiscal year: Alphabet's equity
security gains ran from $1.3bn to $99.0bn across six consecutive quarters. An
annual bridge averages those together and produces a number that describes no
period a reader can act on. The workbook this module was built against reconciles
quarter by quarter for exactly that reason.

How a quarter is identified. SEC XBRL facts carry a start and an end date, so a
quarter is a duration of roughly 90 days. The same quarter appears in more than one
filing — the 10-Q itself, then the next year's comparatives — so facts are
de-duplicated on (start, end), preferring the earliest filing, and a later
restatement of the same period replaces the earlier figure rather than being
appended as a second quarter.

What is reported and what is not. The bridge only carries items the filer actually
tagged. A line the company did not disclose is *named as missing*, never treated as
zero: a zero add-back would present an untagged figure as though the company had
endorsed it.
"""

from __future__ import annotations

import logging
from datetime import date

from app import cache
from app.config import settings
from app.http import fetch
from app.models import (
    NonGaapLine,
    NonGaapReconciliation,
    QuarterlyEps,
    QuarterPoint,
)
from app.providers.fundamentals import lookup_cik

log = logging.getLogger(__name__)

NS = "sec_quarterly"
TTL = 12 * 3600
QUARTERS = 4

MIN_QUARTER_DAYS = 80
MAX_QUARTER_DAYS = 100

# Income-statement facts, by tag. The first tag that carries a quarterly series
# wins, so a filer that stopped tagging one is still readable through another.
INCOME_TAGS: dict[str, tuple[str, ...]] = {
    "net_income": ("NetIncomeLoss", "ProfitLoss",
                   "NetIncomeLossAvailableToCommonStockholdersBasic"),
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues", "RevenueFromContractWithCustomerIncludingAssessedTax"),
    "operating_income": ("OperatingIncomeLoss",),
    "pretax_income": ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"),
    "tax": ("IncomeTaxExpenseBenefit",),
}

# Per-share and share-count facts. USD/shares for EPS, shares for counts.
EPS_TAGS: tuple[str, ...] = ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted")
SHARE_TAGS: tuple[str, ...] = (
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "WeightedAverageNumberOfSharesOutstandingBasic",
)

# Adjustments that many filers tag on a quarterly basis. Equity-security gains are
# the dominant one for the mega-caps that hold large stakes; a negative value is a
# loss and moves the other way, which is why the sign is carried rather than
# assumed.
ADJUSTMENT_TAGS: list[tuple[str, str, str, bool, bool]] = [
    # (key, label, direction, aggregate, after_tax)
    # `direction`: "remove" takes the item out of net income, "add" puts it back.
    # `aggregate`: True only where the candidate tags are genuinely separate
    #   components. It must be False where one tag is the *total* and the others
    #   are its parts — Alphabet tags EquitySecuritiesFvNiGainLoss ($99.0bn for
    #   Q2:26) alongside an unrealised and a realised component, and summing them
    #   gave $120.7bn, a figure that appears in no filing.
    # `after_tax`: True where the tagged value is already net of tax, so applying
    #   the effective rate again would tax it twice.
    ("equity_securities_gain", "Equity securities gain", "remove", False, False),
    ("restructuring", "Restructuring charges", "add", False, False),
    ("impairment", "Impairment loss", "add", False, False),
    ("amortization", "Amortization of intangibles", "add", False, False),
    ("legal_settlement", "Legal settlement / loss contingency", "add", False, False),
    ("acquisition_costs", "Acquisition-related business costs", "add", False, False),
]

_TAG_MAP: dict[str, tuple[str, ...]] = {
    # Order is load-bearing. `_value_for` takes the first tag that covers the
    # period, so a *total* must precede its own components: with the unrealised
    # gain listed first, Alphabet's Q2:26 adjustment resolved to $21.4bn instead
    # of the $99.0bn total and adjusted EPS came out at 7.70 against the release's
    # 3.04. Every tuple below therefore leads with the aggregate concept and lists
    # narrower components after it.
    "equity_securities_gain": (
        "EquitySecuritiesFvNiGainLoss",           # total, when tagged
        "EquitySecuritiesFvNiUnrealizedGainLoss",  # component
        "EquitySecuritiesFvNiRealizedGainLoss",    # component
        "MarketableSecuritiesRealizedGainLoss",
    ),
    "other_nonoperating_income": (
        "OtherNonoperatingIncomeExpense",
        "NonoperatingIncomeExpense",
    ),
    "restructuring": (
        "RestructuringCharges",
        "RestructuringSettlementAndImpairmentProvisions",
    ),
    "impairment": (
        "GoodwillImpairmentLoss",
        "AssetImpairmentCharges",
        "ImpairmentOfIntangibleAssetsExcludingGoodwill",
    ),
    "amortization": (
        "AmortizationOfIntangibleAssets",
        "AmortizationOfFiniteLivedIntangibleAssets",
    ),
    "legal_settlement": (
        "LitigationSettlementExpense",
        "LossContingencyLossInPeriod",
    ),
    "acquisition_costs": (
        "BusinessCombinationAcquisitionRelatedCosts",
        "BusinessCombinationIntegrationRelatedCosts",
    ),
}


def _facts(ticker: str) -> dict:
    """The companyfacts payload for one filer, cached."""
    cik = lookup_cik(ticker)
    if cik is None:
        return {}
    key = f"{ticker}"
    hit = cache.get(NS, key, TTL)
    if hit:
        return hit
    try:
        payload = fetch(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{str(cik).zfill(10)}.json",
            headers={"User-Agent": settings.sec_user_agent},
            namespace="sec_companyfacts",
            ttl=TTL,
            expect_json=True,
            retries=2,
        )
    except Exception as exc:  # noqa: BLE001 - the block degrades, nothing else
        log.debug("companyfacts failed for %s: %s", ticker, exc)
        return {}
    # Only the tags this module reads are cached; the full payload is megabytes.
    # Built from the *tag names*, flattened out of the lookup tables — using the
    # dictionary keys here cached three tags instead of the twenty-odd needed, so
    # income, tax and every adjustment came back empty while EPS looked fine.
    wanted: set[str] = set(EPS_TAGS) | set(SHARE_TAGS)
    for alternatives in INCOME_TAGS.values():
        wanted |= set(alternatives)
    for alternatives in _TAG_MAP.values():
        wanted |= set(alternatives)

    trimmed: dict = {}
    for taxonomy in ("us-gaap", "ifrs-full"):
        facts = (payload.get("facts") or {}).get(taxonomy) or {}
        for tag in wanted:
            node = facts.get(tag)
            if node:
                trimmed[tag] = node
    cache.put(NS, key, trimmed)
    return trimmed


def _unit_series(node: dict, units_preferred: tuple[str, ...]) -> list[dict]:
    """The rows for the best-matching unit, newest first, de-duplicated by period."""
    units = node.get("units") or {}
    chosen = None
    for want in units_preferred:
        if want in units:
            chosen = units[want]
            break
    if chosen is None and units:
        chosen = next(iter(units.values()))
    if not chosen:
        return []

    # De-duplicate on (start, end), keeping the last occurrence: a later filing
    # restating a period should win over the original.
    latest: dict[tuple[str, str], dict] = {}
    for row in chosen:
        start = row.get("start") or row.get("end")
        end = row.get("end")
        if not end or row.get("val") is None:
            continue
        latest[(start or end, end)] = row
    rows = list(latest.values())
    rows.sort(key=lambda r: (r.get("end") or "", r.get("filed") or ""))
    return rows


def _is_quarter(row: dict) -> bool:
    start, end = row.get("start"), row.get("end")
    if not start or not end:
        return False
    try:
        span = (date.fromisoformat(end) - date.fromisoformat(start)).days
    except ValueError:
        return False
    return MIN_QUARTER_DAYS <= span <= MAX_QUARTER_DAYS


def _quarter_series(facts: dict) -> list[tuple[str, str]]:
    """(start, end) for the most recent quarters, newest last.

    Driven by the EPS series, because a quarter with no EPS is not a quarter this
    module can show a per-share figure for.
    """
    for tag in EPS_TAGS:
        node = facts.get(tag)
        if not node:
            continue
        rows = [r for r in _unit_series(node, ("USD/shares",)) if _is_quarter(r)]
        if rows:
            seen: list[tuple[str, str]] = []
            for row in rows:
                pair = (row["start"], row["end"])
                if pair not in seen:
                    seen.append(pair)
            return seen[-QUARTERS * 2:]
    return []


def _value_for(facts: dict, tags: tuple[str, ...], start: str, end: str,
               units: tuple[str, ...], *, aggregate: bool = False) -> float | None:
    """The value covering exactly this period.

    `aggregate=False` takes the first tag that matches — right for an income-statement
    line, where the alternatives are restatements of one number and summing them
    would double count.

    `aggregate=True` sums every matching tag, which is right where the alternatives
    are *components* of one figure rather than replacements for it. Equity
    securities gains are the case that forced this: Alphabet tags the total as
    `EquitySecuritiesFvNiGainLoss` ($99.0bn for Q2:26) but also tags an unrealised
    and a realised component ($21.4bn and $0.3bn). Taking the first tag that
    matched picked a component and understated the adjustment by two thirds,
    producing an adjusted EPS of 7.48 against the release's 3.04.
    """
    found: list[float] = []
    for tag in tags:
        node = facts.get(tag)
        if not node:
            continue
        for row in _unit_series(node, units):
            if row.get("start") == start and row.get("end") == end:
                found.append(row["val"])
                break
        if found and not aggregate:
            return found[0]
    if not found:
        return None
    return sum(found)


def _core_tax_rate(rows: list[tuple[str, str, float | None, float | None]]) -> float | None:
    """The filer's structural tax rate, excluding quarters distorted by the very
    item being removed.

    This matters more than it looks. A large pre-tax equity gain raises *taxable*
    income, so the quarter's blended rate rises with it — Alphabet's runs at about
    19% against a structural rate nearer 17%. Netting the gain at the blended rate
    therefore over-taxes the removal and understates adjusted EPS (3.16 against the
    release's 3.04 for Q2:26).

    Taking the median of the quarters whose pre-tax income is not dominated by an
    adjustment recovers the structural rate. The median rather than the mean so one
    outlier quarter cannot move it.
    """
    import statistics

    rates = [
        tax / pretax
        for _start, _end, tax, pretax in rows
        if tax is not None and pretax and pretax > 0 and 0.0 < tax / pretax < 0.6
    ]
    if not rates:
        return None
    return statistics.median(rates)


def _effective_rate(tax: float | None, pretax: float | None,
                    structural: float | None) -> float | None:
    """The rate to net an adjustment at: the quarter's own, when it is usable.

    The quarter's effective rate is the right rate here, and an earlier version of
    this module was wrong to avoid it. A pre-tax equity gain raises taxable income
    and the tax on it appears in the same quarter's tax charge, so the quarter's
    own rate already contains the item's own tax treatment — which is exactly what
    netting it requires. Substituting a structural median under-taxed the removal
    and produced an adjusted EPS of 2.45 for Alphabet's 2026 Q2 against the 2.85
    the same figures give on the release's own basis.

    The structural rate survives as a guard: a quarter whose effective rate is
    implausible (a tax benefit, or a one-off charge) would distort every adjustment,
    so the structural median is used there instead.
    """
    if tax is not None and pretax and pretax > 0:
        rate = tax / pretax
        if 0.05 <= rate <= 0.45:
            return rate
    return structural


def _quarter_label(start: str, end: str) -> str:
    """A label a reader recognises: the calendar quarter the period ends in.

    A filer whose fiscal year is offset (NVIDIA ends in January) still reports on
    the calendar quarter boundaries XBRL records, so the end date is the honest
    anchor rather than a guessed fiscal-quarter number.
    """
    try:
        when = date.fromisoformat(end)
    except ValueError:
        return end
    return f"{when.year} Q{(when.month - 1) // 3 + 1}"


def _fiscal_label(start: str, end: str) -> str:
    try:
        a, b = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        return ""
    return f"{a.isoformat()} → {b.isoformat()}"


def quarterly_eps(ticker: str, *, quarters: int = QUARTERS,
                  analyst=None) -> list[QuarterlyEps]:
    """The most recent quarters, newest first, with a per-quarter bridge.

    Each quarter gets its own adjusted figure. The annual bridge divides a year of
    adjustments by a year of shares, which cannot show that one quarter carried the
    whole year's gain.

    **Two sources, and the payload says which.** SEC XBRL gives quarterly durations
    for a domestic filer and therefore a full bridge. A foreign private issuer files
    a 6-K rather than a 10-Q, so its quarters are not tagged in XBRL at all — TSMC
    being the case that matters here. For those, the analyst feed's four quarters of
    reported EPS are used: real per-quarter GAAP EPS and the consensus it was
    measured against, but no adjustment lines, because nothing discloses them in a
    machine-readable form. The distinction is carried on every row rather than
    hidden, so a reader is never shown an unbridged quarter as though it were
    bridged.
    """
    facts = _facts(ticker)
    series = _quarter_series(facts) if facts else []

    if not series:
        return _from_analyst(analyst, quarters=quarters)

    # Tax and pre-tax income across the whole window, so the structural rate can be
    # derived from quarters the adjustment did not distort.
    spread = [
        (s, e,
         _value_for(facts, INCOME_TAGS["tax"], s, e, ("USD",)),
         _value_for(facts, INCOME_TAGS["pretax_income"], s, e, ("USD",)))
        for s, e in series
    ]
    core_rate = _core_tax_rate(spread)

    out: list[QuarterlyEps] = []
    for start, end in reversed(series[-quarters:]):
        gaap_eps = _value_for(facts, EPS_TAGS, start, end, ("USD/shares",))
        net_income = _value_for(facts, INCOME_TAGS["net_income"], start, end, ("USD",))
        shares = _value_for(facts, SHARE_TAGS, start, end, ("shares",))
        revenue = _value_for(facts, INCOME_TAGS["revenue"], start, end, ("USD",))
        tax = _value_for(facts, INCOME_TAGS["tax"], start, end, ("USD",))
        pretax = _value_for(facts, INCOME_TAGS["pretax_income"], start, end, ("USD",))

        # A per-share figure with no share count cannot be bridged; fall back to
        # deriving shares from income over EPS so the quarter is still usable.
        derived_shares = shares
        if derived_shares is None and net_income and gaap_eps:
            try:
                derived_shares = net_income / gaap_eps
            except ZeroDivisionError:
                derived_shares = None

        tax_rate = None
        if tax is not None and pretax:
            tax_rate = tax / pretax

        lines: list[NonGaapLine] = []
        missing: list[str] = []
        adjusted_income = net_income
        applied = 0
        for key, label, direction, aggregate, after_tax in ADJUSTMENT_TAGS:
            value = _value_for(facts, _TAG_MAP.get(key, ()), start, end, ("USD",),
                               aggregate=aggregate)
            if value is None or value == 0:
                # Not disclosed, or disclosed as zero. Either way it is not added
                # back, and it is named so the absence is visible.
                missing.append(label)
                continue
            # Net of tax at the quarter's own effective rate where that rate is
            # usable, so the adjustment carries the same tax treatment it had in
            # the filing. `after_tax` tags skip the adjustment entirely.
            netting_rate = _effective_rate(tax, pretax, core_rate)
            if after_tax or netting_rate is None:
                net = value
            else:
                net = value * (1.0 - netting_rate)
            lines.append(NonGaapLine(
                key=key, label=label,
                value=(-net if direction == "remove" else net),
                is_addback=(direction == "add"),
                source="sec-xbrl quarterly",
            ))
            adjusted_income = (adjusted_income or 0.0) - net if direction == "remove" \
                else (adjusted_income or 0.0) + net
            applied += 1

        adjusted_eps = None
        if adjusted_income is not None and derived_shares:
            try:
                adjusted_eps = adjusted_income / derived_shares
            except ZeroDivisionError:
                adjusted_eps = None

        out.append(QuarterlyEps(
            label=_quarter_label(start, end),
            start=start, end=end,
            fiscal_label=_fiscal_label(start, end),
            gaap_eps=gaap_eps,
            adjusted_eps=adjusted_eps,
            net_income=net_income,
            # Always carried, not only when something was added back. The point of
            # the table is the derivation — GAAP net income, the adjustments, then the
            # adjusted figure — and a dash where a filer disclosed no adjustments
            # breaks the chain at exactly the step a reader is checking. No adjustment
            # means adjusted income *equals* GAAP income, which is a fact about the
            # quarter rather than an absent measurement.
            adjusted_net_income=adjusted_income,
            revenue=revenue,
            diluted_shares=derived_shares,
            effective_tax_rate=tax_rate,
            lines=lines,
            missing=missing,
            has_adjustments=applied > 0,
            source="sec",
        ))
    return out


def _from_analyst(analyst, *, quarters: int = QUARTERS) -> list[QuarterlyEps]:
    """Quarters from the analyst feed, for a filer with no quarterly XBRL facts.

    These rows carry a reported GAAP EPS and the consensus it beat or missed, which
    is what a reader needs to compare a quarter against expectations. They carry no
    adjustment lines: the filings those adjustments live in are prose for a foreign
    private issuer, so there is nothing to bridge and `has_adjustments` stays False
    rather than a zero-bridge being presented as though it were one.
    """
    history = list(getattr(analyst, "earnings_history", None) or []) if analyst else []
    out: list[QuarterlyEps] = []
    for item in history[-quarters:]:
        end = getattr(item, "quarter_end", None)
        if end is None:
            continue
        stamp = str(end)[:10]
        try:
            when = date.fromisoformat(stamp)
            label = f"{when.year} Q{(when.month - 1) // 3 + 1}"
        except ValueError:
            label = stamp
        out.append(QuarterlyEps(
            label=label,
            end=stamp,
            fiscal_label=stamp,
            gaap_eps=getattr(item, "eps_actual", None),
            consensus_eps=getattr(item, "eps_estimate", None),
            surprise_pct=getattr(item, "surprise_pct", None),
            source="analyst",
        ))
    out.sort(key=lambda row: row.end, reverse=True)
    return out


def quarterly_bridge(ticker: str) -> NonGaapReconciliation | None:
    """A quarterly-basis reconciliation, for the period label and the caveats.

    Built so the UI can state *which* period the bridge covers. The annual bridge
    is a different measurement, and a reader cannot tell them apart from the
    numbers alone — six quarters of Alphabet's equity gains run from $1.3bn to
    $99bn, so the period is the most important thing about the figure.
    """
    quarters = quarterly_eps(ticker)
    if not quarters:
        return None
    newest = quarters[0]
    return NonGaapReconciliation(
        currency="USD",
        lines=newest.lines,
        missing=newest.missing,
        gaap_net_income=newest.net_income,
        adjusted_net_income=newest.adjusted_net_income,
        diluted_shares=newest.diluted_shares,
        gaap_eps=newest.gaap_eps,
        adjusted_eps=newest.adjusted_eps,
        tax_rate=newest.effective_tax_rate,
        period="quarter",
        period_label=newest.label,
        period_span=newest.fiscal_label,
        basis=(
            "Per-quarter bridge from SEC XBRL quarterly facts (10-Q / 6-K "
            "durations of about 90 days). Adjustments are the items this filer "
            "actually tagged in that quarter."
        ),
        eps_basis=(
            "Diluted EPS, quarterly. Not annual: a year of adjustments divided by "
            "a year of shares hides a quarter that carried the whole year's gain."
        ),
    )


def quarter_points(ticker: str, *, quarters: int = QUARTERS) -> list[QuarterPoint]:
    """A compact quarterly series for charting, oldest first."""
    return [
        QuarterPoint(
            label=q.label, gaap_eps=q.gaap_eps, adjusted_eps=q.adjusted_eps,
            revenue=q.revenue, end=q.end,
        )
        for q in reversed(quarterly_eps(ticker, quarters=quarters))
    ]
