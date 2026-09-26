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

# The shortest span that may be differenced to recover a quarter. A single quarter
# must be excluded from that pool: subtracting one quarter from the next is
# meaningless, and doing it produced a Meta Q2 of 7.13 (Q2 minus Q1 instead of
# Q2 alone). Six months is the shortest genuinely cumulative period.
CUMULATIVE_MIN_DAYS = 170

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


def _quarter_series(facts: dict) -> tuple[list[tuple[str, str]], list[str]]:
    """The quarters, oldest last, each marked "stated" or "derived".

    Driven by the EPS series, because a quarter with no EPS is not a quarter this
    module can show a per-share figure for. The marker matters: a stated quarter has
    its own three-month fact and can be read directly, while a derived one exists
    only as the difference of two cumulative periods and must be read from there.
    """
    for tag in EPS_TAGS:
        node = facts.get(tag)
        if not node:
            continue
        rows = [r for r in _unit_series(node, ("USD/shares",)) if _is_quarter(r)]
        if rows:
            raw: list[tuple[str, str]] = []
            for row in rows:
                pair = (row["start"], row["end"])
                if pair not in raw:
                    raw.append(pair)
            raw.sort(key=lambda pair: pair[1])

            # One period per quarter, and the right variant of it. XBRL carries the
            # same quarter more than once with a shifted start — Meta holds both
            # 2011-06-30→09-30 (92 days) and 2011-07-01→09-30 (91 days) — and the
            # shifted one is the *longer* span, so preferring the longest span keeps
            # exactly the wrong variant. That variant reaches back a day too far, so
            # it appears to overlap the preceding quarter and the derivation then
            # refuses to fill a genuinely missing quarter.
            #
            # The right choice is the period that begins immediately after the
            # previous quarter ends: a quarter starts where its predecessor stopped.
            # Taking the latest start per end date does that, because the shifted
            # variant always starts earliest.
            best: dict[str, tuple[str, str]] = {}
            for pair in raw:
                try:
                    days = (date.fromisoformat(pair[1])
                            - date.fromisoformat(pair[0])).days
                except ValueError:
                    continue
                if not (MIN_QUARTER_DAYS <= days <= MAX_QUARTER_DAYS):
                    continue
                current = best.get(pair[1])
                if current is None or pair[0] > current[0]:
                    best[pair[1]] = pair
            clean: list[tuple[str, str]] = sorted(best.values(), key=lambda p: p[1])

            # Add the quarters the filer only tagged cumulatively, so the series is
            # actually consecutive instead of breaking at every year boundary. A
            # derived pair that ends where a stated quarter already ends is skipped:
            # the stated one is the better record of that period.
            # Add the quarters the filer only tagged cumulatively, so the series is
            # actually consecutive instead of breaking at every year boundary.
            #
            # The derivation is given only the *stated* quarters as "known". A
            # cumulative span may end on the same day a quarter does — Meta's
            # nine-month figure ends 2025-09-30 exactly as its third quarter does —
            # so passing those spans in would make the derivation believe the fourth
            # quarter already existed and skip it, leaving the quarter blank.
            stated_set = set(clean)
            extra = _derive_quarters(facts, clean)
            series = list(clean) + [p for p in extra if p not in stated_set]
            series.sort(key=lambda pair: pair[1])
            marked = [("derived" if (s, e) not in stated_set else "stated")
                      for s, e in series]
            return series, marked
    return [], []


def _consecutive(series: list[tuple[str, str]], count: int) -> list[tuple[str, str]]:
    """The newest `count` quarters that are actually adjacent to each other.

    XBRL does not hold every quarter for every filer: Micron is missing 2025 Q3,
    NVIDIA 2025 Q1 and 2026 Q1, Apple 2024 Q3 and 2025 Q3. Taking "the last four
    entries" therefore filled the hole with a quarter from the previous year —
    Micron showed 2025 Q2 and no 2025 Q3, which is not its latest four quarters at
    all.

    So the series is walked backwards from the newest period and stops at the first
    real gap. A gap is a missing ninety-day window: two consecutive entries are
    adjacent when the older one ends within a fortnight of where the newer one
    starts. Returning fewer quarters than asked for is the honest outcome, and the
    caller reports how many months are actually covered.
    """
    if not series:
        return []
    ordered = sorted(series, key=lambda pair: pair[1], reverse=True)
    picked = [ordered[0]]
    for start, end in ordered[1:]:
        if len(picked) >= count:
            break
        newer_start = picked[-1][0]
        try:
            gap = (date.fromisoformat(newer_start) - date.fromisoformat(end)).days
            same_period = (date.fromisoformat(picked[-1][1])
                           - date.fromisoformat(end)).days
        except ValueError:
            continue
        # XBRL holds the same quarter more than once with a shifted start — Coca-Cola
        # carries both 2025-03-29→06-27 and 2025-03-28→06-27 — so a second variant of
        # a quarter already chosen has to be skipped or it displaces a real quarter
        # further back and the four "latest" quarters come out wrong.
        if 0 <= same_period <= 7:
            continue
        # A week or two of slack absorbs a 52/53-week fiscal calendar; a missing
        # quarter leaves roughly ninety days unfilled. A negative gap means the
        # older period runs past where the newer one begins, so the two overlap and
        # neither is a clean predecessor — treated as a gap rather than spliced.
        if 0 <= gap <= 21:
            picked.append((start, end))
    return list(reversed(picked))


def _derive_quarters(facts: dict, known: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    """Reconstruct quarters a filer only tagged cumulatively.

    Most filers do not tag every three-month period. In a 10-K, iXBRL requires
    year-to-date figures, so the nine-month and twelve-month spans appear and the
    three-month fourth quarter does not; and some filers, Coca-Cola among them,
    tag the nine-month cumulative but not the three-month third quarter.

    Because these figures accumulate, a missing quarter is the gap between the
    period that ends where it ends and the period that ends where it starts:
    Q4 = full year − nine months, and a missing Q3 = nine months − six months. EPS
    and net income accumulate exactly, so both are recovered by subtraction rather
    than estimated. This is what makes the series genuinely consecutive: every
    company's history broke at the year boundary, and Micron's latest four quarters
    came out as Q2, Q4, Q1, Q2 with Q3 absent purely because Q4 was the hole.
    """
    derived: dict[tuple[str, str], dict] = {}
    known_set = set(known)

    def spans(tags: tuple[str, ...], units: tuple[str, ...]) -> list[dict]:
        out: list[dict] = []
        for tag in tags:
            node = facts.get(tag)
            if not node:
                continue
            for row in _unit_series(node, units):
                start, end = row.get("start"), row.get("end")
                if not start or not end or row.get("val") is None:
                    continue
                try:
                    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
                except ValueError:
                    continue
                # Only *cumulative* periods may be differenced. An individual
                # quarter must be excluded or the subtraction is meaningless: with
                # quarters in the pool, Meta's Q2 came out as 7.13 because Q1's
                # figure was subtracted from Q2's rather than added to it. Two
                # quarters is the shortest cumulative span (six months).
                if days < CUMULATIVE_MIN_DAYS:
                    continue
                out.append({"start": start, "end": end, "days": days})
        return out

    eps_spans = spans(EPS_TAGS, ("USD/shares",))
    income_spans = spans(INCOME_TAGS["net_income"], ("USD",))
    if not eps_spans:
        return derived

    # Every distinct period end, newest last.
    ends: list[str] = []
    for row in eps_spans:
        if row["end"] not in ends:
            ends.append(row["end"])
    ends.sort()

    # The starting date of every cumulative period, so a gap can be attributed to
    # the two periods whose difference it is.
    starts: list[str] = []
    for row in eps_spans:
        if row["start"] not in starts:
            starts.append(row["start"])
    starts.sort()

    def derive(earlier_start: str, end: str, later_start: str, later: str):
        """The gap as a difference of two cumulative periods, or None."""
        later_eps = _value_for(facts, EPS_TAGS, later_start, later, ("USD/shares",))
        earlier_eps = _value_for(facts, EPS_TAGS, earlier_start, end, ("USD/shares",))
        later_income = _value_for(facts, INCOME_TAGS["net_income"], later_start, later, ("USD",))
        earlier_income = _value_for(facts, INCOME_TAGS["net_income"], earlier_start, end, ("USD",))

        eps_value = (later_eps - earlier_eps
                     if later_eps is not None and earlier_eps is not None else None)
        income_value = (later_income - earlier_income
                        if later_income is not None and earlier_income is not None else None)
        if eps_value is None and income_value is None:
            return None
        return {
            "gaap_eps": eps_value,
            "net_income": income_value,
            # The diluted count is a weighted average, so it is not additive:
            # subtracting one average from another would invent a number. The
            # earlier period's count is carried instead — the closest real
            # observation — and the quarter is flagged as derived.
            "shares": _value_for(facts, SHARE_TAGS, earlier_start, end, ("shares",)),
            "derived": True,
        }

    for index, end in enumerate(ends):
        for later in ends[index + 1:]:
            if (end, later) in known_set:
                continue
            try:
                gap_days = (date.fromisoformat(later) - date.fromisoformat(end)).days
            except ValueError:
                continue
            if not (MIN_QUARTER_DAYS <= gap_days <= MAX_QUARTER_DAYS):
                continue

            # Case 1 — the same cumulative series, extended: full year minus nine
            # months, or nine months minus six. Both periods share a start date.
            shared = [s for s in starts
                      if _value_for(facts, EPS_TAGS, s, end, ("USD/shares",)) is not None
                      and _value_for(facts, EPS_TAGS, s, later, ("USD/shares",)) is not None]
            if shared:
                value = derive(shared[0], end, shared[0], later)
                if value:
                    derived[(end, later)] = value
                    continue

            # Case 2 — consecutive year-to-date spans: the six-month cumulative
            # ending where the nine-month one starts. Coca-Cola tags the nine-month
            # figure but not the three-month third quarter, so this is the only way
            # that quarter is recoverable from an official source.
            for earlier_start in starts:
                if earlier_start >= later:
                    continue
                if _value_for(facts, EPS_TAGS, earlier_start, later, ("USD/shares",)) is None:
                    continue
                same = _value_for(facts, EPS_TAGS, earlier_start, end, ("USD/shares",))
                if same is None:
                    continue
                value = derive(earlier_start, end, earlier_start, later)
                if value:
                    derived[(end, later)] = value
                    break

    return derived


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
    """A label a reader recognises: the calendar quarter the period belongs to.

    Neither date works alone. Labelling by the end month is wrong when a fiscal
    calendar spills into the next month — Coca-Cola's first quarter of 2026 runs
    2026-01-01 to 2026-04-03, and reading the end month called it "2026 Q2", so the
    card showed 2026 Q2, 2025 Q4, 2025 Q3, 2025 Q2 and looked as though a quarter
    were missing. Labelling by the start month is wrong for an offset fiscal year:
    NVIDIA's 2026-01-26 to 2026-04-26 is its first quarter, but it is the second
    calendar quarter, and every period shifts.

    So the end date decides, with one correction: a period that closes in the first
    weeks of a month is the quarter that just ended, not the one that just began.
    A period ending on the 30th of a month belongs to that month's quarter; one
    ending on the 3rd belongs to the previous quarter.
    """
    try:
        when = date.fromisoformat(end)
    except ValueError:
        return end
    month = when.month
    if when.day <= 20:
        # Closes early in the month, so the quarter ended the month before.
        month -= 1
        if month == 0:
            month = 12
            when = when.replace(year=when.year - 1)
    return f"{when.year} Q{(month - 1) // 3 + 1}"


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
    series, marks = _quarter_series(facts) if facts else ([], [])
    # A quarter marked "derived" has no three-month fact, so its figures come from
    # the cumulative subtraction rather than a direct lookup.
    is_derived = dict(zip(series, marks))

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
    # Quarters reconstructed from the cumulative facts, keyed by their period.
    derived_index = _derive_quarters(
        facts, [pair for pair in series if is_derived.get(pair) == "stated"])

    out: list[QuarterlyEps] = []
    # Only quarters adjacent to each other, so a hole in the filings cannot be
    # filled with a stale quarter from the previous year.
    for start, end in reversed(_consecutive(series, quarters)):
        # A derived quarter has no three-month fact of its own; its figures come
        # from the cumulative subtraction, so they are read from there.
        derived = derived_index.get((start, end), {}) if is_derived.get((start, end)) == "derived" else {}
        gaap_eps = (derived.get("gaap_eps") if derived
                    else _value_for(facts, EPS_TAGS, start, end, ("USD/shares",)))
        net_income = (derived.get("net_income") if derived
                      else _value_for(facts, INCOME_TAGS["net_income"], start, end, ("USD",)))
        shares = (derived.get("shares") if derived
                  else _value_for(facts, SHARE_TAGS, start, end, ("shares",)))
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
