"""Read the non-GAAP reconciliation the company itself disclosed.

Why this exists. The quarterly bridge could not reproduce a filer's own figures,
and the gap was not a method error: the tax on an equity-securities gain, and
one-off items a company excludes, are **not XBRL facts**. Alphabet's 2025 Q3 release
says the gain of $10.7bn increased the provision for income tax, net income and
diluted EPS by $2.2bn, $8.3bn and $0.68. None of those three numbers is tagged, so
no amount of arithmetic over the structured data can recover them.

They are, however, stated in the earnings release, which the company files as an 8-K
exhibit. So this module reads that sentence.

The distinction that matters for tax. A fine is generally not deductible, so adding
it back does **not** create a tax benefit — the add-back is the full pre-tax amount.
A gain that was taxed is removed at its **after-tax** amount, which the release
states directly. Treating both the same way is what made the earlier figures wrong
in both directions, and it is why the released figures are preferred to any rate the
app might apply.

Nothing here is guessed. The patterns are written against the wording of real
releases, a figure that cannot be found is reported as absent, and a quarter with no
extracted disclosure keeps the calculated bridge.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date

from app import cache
from app.config import settings
from app.http import fetch
from app.models import DisclosedAdjustment

log = logging.getLogger(__name__)

NS = "sec_release_text"
TTL = 24 * 3600

# The parse is versioned along with the cache, because the *document* is cached
# under its own key and a change to the patterns would otherwise keep returning what
# an earlier version of this module read out of it. That masked a fix once already:
# corrected patterns were in place and the old figures kept coming back.
PARSE_VERSION = 2

# `$9.8 billion`, `$253 million`, `$1.3bn`, `3,457`. Two traps here, both found by
# testing against the real releases:
#
#   * a greedy unit word swallowed the *next* amount, so "of $10.7 billion and the
#     performance fees ... of $174 million increased" captured the fee and then
#     failed at "increased";
#   * a lazy unit word skipped the unit entirely, leaving `billion` unmatched — and
#     since a missing unit is read as millions, $10.7bn became $10.7m.
#
# The unit is therefore greedy and mandatory when a unit word is present, and the
# caller decides what a unitless figure means from its size.
_MONEY = r"\$?\s*([0-9][0-9,.]*)\s*(?:(billion|million|bn|mm)|(?![a-z]))"

# Alphabet and the many filers that use its phrasing: the effect of a gain on
# equity securities, stated as a triple of tax, net income and per-share amounts.
# 2025 releases include performance fees in the same sentence; 2026 releases do not,
# so that part is optional. Tags are stripped before matching, but the released HTML
# leaves stray spaces inside words ("increased th e provision"), so the separators
# are deliberately loose.
_GAIN_EFFECT = re.compile(
    # `\s*` between the phrase and each amount. `_MONEY` begins with an optional
    # `$`, not with whitespace, so "securities of" butted straight against it and
    # demanded the amount to start immediately — "of$10.7" — which no release
    # contains. Two further separators are not just whitespace: after "provision for
    # income tax" the sentence continues ", net income, and diluted net income per
    # share by", and after the answer the phrasing moves on to the next figure. Each
    # gap therefore allows a short intervening clause.
    r"gain on equity securities of\s*" + _MONEY +
    r".{0,180}?(?:increased|decreased)\s+(?:th\s*e\s+)?provision for income tax"
    r".{0,90}?" + _MONEY +
    r".{0,70}?" + _MONEY +
    r".{0,80}?" + _MONEY,
    re.I | re.S,
)

# The performance-fee piece, when the release mentions it. Alphabet attaches the
# fee to the gain inside the same sentence — "the gain on equity securities of $10.7
# billion and the performance fees related to certain investments of $174 million
# increased ..." — so the fee is the *second* amount in that span, not one that
# follows the phrase directly.
_FEES = re.compile(
    r"gain on equity securities of\s*" + _MONEY +
    r".{0,120}?performance fees.{0,80}?" + _MONEY,
    re.I | re.S,
)

# A per-share amount: `$0.68`, `$6.26`, `$2.35`. Written with cents, so neither
# `billion` nor `million` follows and the money pattern's unit branch cannot be used.
#
# Found by taking the cents figures from the *whole sentence*, because the triple is
# ordered tax, net income, per share — "by $2.2 billion, $8.3 billion, and $0.68" —
# and any pattern that anchors on the first `$` after "per share" returns the tax
# figure, which is how $2.2 was once read as the per-share effect.
_EPS_SENTENCE = re.compile(
    r"gain on equity securities of.{0,600}?(?:per\s+share|per\s+common\s+share)\s+by"
    r"(?P<amounts>.{0,160}?)respectively",
    re.I | re.S,
)
# A per-share amount: `$0.68`, `$6.26`. Written with cents, so no unit word follows —
# and that is the only thing distinguishing it from the two billion figures in the
# same clause. A trailing delimiter is required, because a bare pattern backtracks:
# against "$2.2 billion" it would match "2.2" and stop before the disqualifying word.
_CENTS = re.compile(r"\$?\s*([0-9]+\.[0-9]{1,2})(?=\s*(?:,|\.|respectively|$))", re.I)

# A one-off the company adds back, taken from the reconciliation table itself.
#
# The table is what to read, not the prose. Alphabet's release says "an EC fine of
# $3.5 billion" in one sentence and "add: EC fine 0 3,457" in the reconciliation, and
# only the second is the figure the company actually used — a rounded article in a
# news paragraph is not an accounting amount. The line carries the current and prior
# periods, so the add-back is the *second* number; the first is the comparative
# quarter and is zero.
_FINE = re.compile(
    r"add:\s*"
    r"(?P<label>[A-Za-z][A-Za-z ]{0,40}?(?:fine|charge|penalty|settlement))"
    r"[\s:]*" + _MONEY +
    r"[\s,]*" + _MONEY,
    re.I | re.S,
)

_UNITS = {"billion": 1e9, "bn": 1e9, "million": 1e6, "mm": 1e6}


def _amount(match: re.Match, index: int, *, base: int = 1) -> float | None:
    """The value of the `index`-th number group, scaled by its unit word.

    `base` is the group number of that number's first capture, because a pattern
    may carry its own leading groups — the fine pattern names what it matched
    before it names an amount.
    """
    digits_group = base + (index - 1) * 2
    if digits_group > (match.re.groups or 0):
        return None
    digits = match.group(digits_group)
    unit = match.group(digits_group + 1) if digits_group + 1 <= match.re.groups else None
    if not digits:
        return None
    try:
        value = float(digits.replace(",", ""))
    except ValueError:
        return None
    if unit:
        return value * _UNITS.get(unit.lower(), 1.0)
    # No unit word: a figure this large is written in dollars, a small one in
    # millions as the tables do.
    return value if value > 1e6 else value * 1e6


def _flatten(html: str) -> str:
    """The document as searchable text.

    Tags become spaces so table cells do not run together, and the HTML entities
    SEC uses for punctuation are decoded — the released files write an apostrophe as
    `&#8217;` and a bullet as `&#8226;`, which would otherwise sit in the middle of
    the sentences being matched.
    """
    text = re.sub(r"<[^>]+>", " ", html)
    for entity, replacement in (
        ("&#160;", " "), ("&nbsp;", " "), ("&#8217;", "'"), ("&#8216;", "'"),
        ("&#8220;", '"'), ("&#8221;", '"'), ("&#8226;", "*"), ("&#58;", ":"),
        ("&#59;", ";"), ("&amp;", "&"),
    ):
        text = text.replace(entity, replacement)
    return re.sub(r"\s+", " ", text)


def _period_label(start: str, end: str) -> str:
    try:
        when = date.fromisoformat(end)
    except ValueError:
        return ""
    month = when.month - 1 if when.day <= 20 else when.month
    year = when.year if month > 0 else when.year - 1
    month = month or 12
    return f"{year} Q{(month - 1) // 3 + 1}"


def extract_disclosures(html: str, *, quarter: str = "",
                        period_end: str = "") -> DisclosedAdjustment | None:
    """What a release states about its own adjustments, or None if nothing matched.

    Returning None is a real answer, not a failure: a filer that does not word its
    release this way keeps the calculated bridge rather than being given figures
    read out of the wrong sentence.
    """
    text = _flatten(html)
    result = DisclosedAdjustment(
        quarter=quarter, period_end=period_end, source="8-K exhibit (release text)"
    )
    found = False

    effect = _GAIN_EFFECT.search(text)
    if effect:
        result.equity_gain = _amount(effect, 1)
        result.tax_on_gain = _amount(effect, 2)
        # The third figure is the after-tax net-income effect, which is what may be
        # removed. Taken as stated rather than recomputed, because the release's own
        # arithmetic already reflects the item's actual tax treatment.
        result.gain_after_tax = _amount(effect, 3)
        result.eps_effect = _amount(effect, 4)
        found = found or result.gain_after_tax is not None

    # The per-share effect is the cents figure in that sentence — the last of the
    # three amounts, since the order is tax, net income, per share.
    per_share = _EPS_SENTENCE.search(text)
    if per_share:
        cents = _CENTS.findall(per_share.group("amounts"))
        if cents:
            try:
                result.eps_effect = float(cents[-1])
            except ValueError:
                pass
            found = True

    fees = _FEES.search(text)
    if fees:
        result.performance_fees = _amount(fees, 2)
        found = found or result.performance_fees is not None

    fine = _FINE.search(text)
    if fine:
        # Group 1 is the label, so the amount groups start at 2, and the add-back is
        # the second amount — the current period, not the comparative one.
        amount = _amount(fine, 2, base=2)
        if amount:
            result.non_deductible_items = amount
            result.non_deductible_label = fine.group("label").strip()
            found = True

    if not found:
        return None
    return result


def _eight_k_accessions(ticker: str) -> list[tuple[str, str]]:
    """The company's 8-K accessions with their filing dates, newest first.

    The accession on an XBRL fact points at the 10-Q, and the earnings release is not
    in that filing — it is filed on an 8-K made the same day. So the release has to be
    reached through the 8-K, which this lists from the submissions index the events
    module already reads.
    """
    from app.config import settings
    from app.providers.fundamentals import lookup_cik

    cik = lookup_cik(ticker)
    if cik is None:
        return []
    try:
        payload = fetch(
            f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json",
            headers={"User-Agent": settings.sec_user_agent},
            namespace="sec_submissions", ttl=TTL, expect_json=True, retries=2,
        )
    except Exception as exc:  # noqa: BLE001 - the calculated bridge stands in
        log.debug("submissions index failed for %s: %s", ticker, exc)
        return []

    recent = (payload.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accessions = recent.get("accessionNumber") or []
    out: list[tuple[str, str]] = []
    for index, form in enumerate(forms):
        if form == "8-K" and index < len(accessions) and index < len(dates):
            out.append((dates[index], accessions[index]))
    out.sort(reverse=True)
    return out


def find_release_accession(ticker: str, filed: str) -> str | None:
    """The 8-K filed around the same time as this quarter's 10-Q.

    A company files the release on an 8-K the same day it files the 10-Q, so the
    nearest 8-K to that date is the one carrying this quarter's release. A window of
    a few days is allowed because the two are not always dated identically.
    """
    if not filed:
        return None
    candidates = _eight_k_accessions(ticker)
    if not candidates:
        return None
    try:
        target = date.fromisoformat(filed)
    except ValueError:
        return None
    for when, accession in candidates:
        try:
            delta = abs((date.fromisoformat(when) - target).days)
        except ValueError:
            continue
        if delta <= 5:
            return accession
    return None


def get_disclosures(ticker: str, *, accession: str | None = None,
                    quarter: str = "", period_end: str = "",
                    allow_network: bool = True) -> DisclosedAdjustment | None:
    """The disclosed adjustments for one quarter, cached.

    `accession` is the filing the figures come from. Without one there is nothing to
    read, because the values live in the exhibit rather than in the structured data.
    """
    if not accession:
        return None
    key = f"{ticker}:{accession}:{PARSE_VERSION}"
    hit = cache.get(NS, key, TTL)
    if hit:
        try:
            return DisclosedAdjustment.model_validate(hit)
        except Exception:  # pragma: no cover - a stale cache shape is not fatal
            return None
    if not allow_network:
        return None

    from app.providers.fundamentals import lookup_cik

    cik = lookup_cik(ticker)
    if cik is None:
        return None
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}"

    # The exhibit's file name is not in the submission index, so the filing's own
    # `index.json` is read — note the lower-case name, since `Index.json` returns
    # 404 — and the earnings-release exhibit chosen from the files it lists.
    try:
        listing = fetch(
            f"{base}/index.json",
            headers={"User-Agent": settings.sec_user_agent},
            namespace="sec_edgar_html", ttl=TTL, retries=2,
        )
    except Exception as exc:  # noqa: BLE001 - the calculated bridge stands in
        log.debug("EDGAR filing index failed for %s/%s: %s", ticker, accession, exc)
        return None

    names: list[str] = []
    if isinstance(listing, bytes):
        listing = listing.decode("utf-8", "replace")
    try:
        index = json.loads(listing) if isinstance(listing, str) else listing
    except (ValueError, TypeError):
        index = None
    if isinstance(index, dict):
        for item in ((index.get("directory") or {}).get("item")) or []:
            name = str(item.get("name") or "")
            if name.lower().endswith((".htm", ".html")) and "index" not in name.lower():
                names.append(name)
    if not names:
        # A filing that does not serve index.json: fall back to the directory page.
        for match in re.finditer(r'href="([^"]+\.html?)"', str(listing), re.I):
            name = match.group(1).rsplit("/", 1)[-1]
            if "index" not in name.lower() and not name.startswith("R"):
                names.append(name)

    # The exhibit is preferred over the cover page: the cover page never carries the
    # reconciliation, and it sorts first in the listing.
    names.sort(key=lambda name: (("ex" not in name.lower()), name))

    for name in names:
        try:
            body = fetch(f"{base}/{name}",
                         headers={"User-Agent": settings.sec_user_agent},
                         namespace="sec_edgar_html", ttl=TTL, retries=2)
        except Exception:  # noqa: BLE001 - try the next candidate
            continue
        if isinstance(body, bytes):
            body = body.decode("utf-8", "replace")
        result = extract_disclosures(body, quarter=quarter, period_end=period_end)
        if result is not None:
            cache.put(NS, key, result.model_dump(mode="json"))
            return result
    return None
