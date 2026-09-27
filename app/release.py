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
from datetime import date, datetime

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


def _table_grid(table_html: str) -> list[list[str]]:
    """One table as a grid of cell text, honouring `colspan`.

    Honouring the spans is what makes the columns identifiable. A header cell reading
    "Three Months Ended" carries `colspan="2"` because it governs two numeric columns,
    and "Six Months Ended" governs two more; a reader that treats each cell as one
    column loses that correspondence and cannot say which figure belongs to a quarter.
    A spanned cell is therefore repeated across the columns it covers.
    """
    grid: list[list[str]] = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.I | re.S):
        cells: list[str] = []
        for attributes, inner in re.findall(r"<t[dh]([^>]*)>(.*?)</t[dh]>",
                                            row_html, re.I | re.S):
            span = 1
            found = re.search(r'colspan\s*=\s*"?(\d+)', attributes, re.I)
            if found:
                try:
                    span = max(1, int(found.group(1)))
                except ValueError:
                    span = 1
            text = re.sub(r"<[^>]+>", " ", inner)
            for entity, replacement in (("&#160;", " "), ("&nbsp;", " "),
                                        ("&amp;", "&"), ("&#8217;", "'"),
                                        ("&#8212;", "-"), ("&#8211;", "-")):
                text = text.replace(entity, replacement)
            cells.extend([re.sub(r"\s+", " ", text).strip()] * span)
        if cells:
            grid.append(cells)
    return grid


def _tables(html: str) -> list[list[list[str]]]:
    """The document's tables as grids of cell text.

    The income statement has to be read as a table, not as a run of text. Flattened,
    Alphabet's diluted line becomes "per common share $ 2.31 $ 2.84 $ 2.30 $ 9.11",
    and a pattern over that cannot tell which of the four figures is the quarter being
    read rather than the year-to-date column beside it. Cell by cell, the same line is
    four cells, and which of them is the quarter follows from the column headers.
    """
    out: list[list[list[str]]] = []
    for table_html in re.findall(r"<table[^>]*>(.*?)</table>", html, re.I | re.S):
        grid = _table_grid(table_html)
        if grid:
            out.append(grid)
    return out


# A per-share figure: `2.31`, `1.87`, `(0.73)`, `24.67`. Two decimals, and a loss
# may be bracketed rather than signed.
_NUMBER = re.compile(r"^\(?\$?\s*([0-9]+(?:\.[0-9]+)?)\s*\)?$")


def _cell_number(text: str) -> float | None:
    """The value of a numeric cell, or None if the cell is a label or a unit."""
    stripped = text.strip().replace(",", "")
    if not stripped:
        return None
    negative = stripped.startswith("(") and stripped.endswith(")")
    match = _NUMBER.match(stripped.replace("(", "").replace(")", ""))
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    return -value if negative else value


# ---------------------------------------------------------------------------
# Sources surveyed for a quarter the filer did not tag in XBRL
#
# Recorded because the survey took real work and its conclusion is not obvious: no
# free source states a missing quarter's *GAAP* EPS per share.
#
# SEC-derived sources cannot, by construction — they are all built from the same XBRL,
# so an untagged fact is absent from all of them. Verified rather than assumed:
#
#   frames API, EarningsPerShareDiluted, CY2025Q3  -> 4429 filers, 0 for Qualcomm
#   companyconcept for Qualcomm                    -> no quarter-length fact between
#                                                     2025-06-29 and 2025-12-28
#
# That is the whole point: Qualcomm's quarter ending 2025-09-28 is in its release and
# its 10-K income statement, and nowhere in the structured data.
#
# Two keyless sources do carry it, and both were checked:
#
#   api.nasdaq.com/api/quote/{T}/eps
#       20/20 pool tickers, four periods each, with a consensus beside every actual.
#       Keyless and free. Its figures are the adjusted ones companies guide on rather
#       than GAAP: Micron's Aug-2025 quarter is 2.86, its non-GAAP figure.
#
#   the analyst feed already used for TSMC
#       42 quarters across the pool beyond what XBRL supplies, covering all the gaps
#       above, and it needs no new source or licence.
#
# Neither may be merged into the GAAP bridge. Both report an adjusted actual against a
# non-GAAP consensus, and measured against the app's GAAP figure they disagree on 20 of
# the 50 quarters both carry — Qualcomm's March 2026 quarter reads 2.65 in the feed
# against a GAAP 6.88 that a tax benefit distorted. That is the same mixed-basis
# comparison that produced a 169% "beat" earlier in this project, so the two bases are
# kept apart deliberately: a filled quarter must be labelled an adjusted measure and
# never presented as the GAAP bridge.
#
# One caution. The two sources disagree with each other on historical quarters — Meta's
# September 2025 is 1.05 in the analyst feed and 7.25 on Nasdaq — which points at
# restated figures on one side or both. Filling a *recent* gap is low risk, since the
# three agree there for most filers. Back-filling history from either is not, and was
# not attempted.
# ---------------------------------------------------------------------------

# Header language that says how long the columns beneath it run.
_QUARTER_HEADER = re.compile(
    r"three\s+months|3\s+months|quarter\s+ended|3rd\s+qtr|2nd\s+qtr|1st\s+qtr|"
    r"4th\s+qtr|first\s+quarter|second\s+quarter|third\s+quarter|fourth\s+quarter", re.I)
_CUMULATIVE_HEADER = re.compile(
    r"six\s+months|nine\s+months|year[\s-]*to[\s-]*date|twelve\s+months|"
    r"six\s+month|nine\s+month|\bytd\b", re.I)
# A date inside a header, used to tell the current period from the comparative one.
_HEADER_DATE = re.compile(
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s*\d{4}"
    r"|\d{1,2}/\d{1,2}/\d{2,4}", re.I)


def _column_kinds(grid: list[list[str]]) -> list[str]:
    """For each column, "quarter", "cumulative" or "unknown".

    Read from the header rows, which is the only place the distinction is stated. A
    table may carry both — Alphabet's income statement has a quarter pair followed by
    a year-to-date pair — and the two are separated only by the span above them.
    """
    width = max((len(row) for row in grid), default=0)
    kinds = ["unknown"] * width
    for row in grid[:4]:
        joined = " ".join(row)
        if not (_QUARTER_HEADER.search(joined) or _CUMULATIVE_HEADER.search(joined)):
            continue
        for index in range(min(len(row), width)):
            cell = row[index]
            if _CUMULATIVE_HEADER.search(cell):
                kinds[index] = "cumulative"
            elif _QUARTER_HEADER.search(cell):
                kinds[index] = "quarter"
    return kinds


def _header_period_columns(grid: list[list[str]], period_end: str) -> set[int]:
    """The columns whose header names `period_end`.

    The strongest identification available, because it does not rely on column order
    at all. Not every release prints the date, so this refines a choice rather than
    making it.
    """
    out: set[int] = set()
    if not period_end:
        return out
    try:
        wanted = date.fromisoformat(period_end)
    except ValueError:
        return out
    for row in grid[:4]:
        for index, cell in enumerate(row):
            for found in _HEADER_DATE.finditer(cell):
                text = found.group(0).replace(".", "")
                for pattern in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",
                                "%m/%d/%Y", "%m/%d/%y"):
                    try:
                        parsed = datetime.strptime(text, pattern).date()
                    except ValueError:
                        continue
                    # Within a week, since a period end and the date a release names
                    # for it can differ by a day or two on a 52/53-week calendar.
                    if abs((parsed - wanted).days) <= 7:
                        out.add(index)
                    break
    return out


def extract_quarterly_eps(html: str, *, period_end: str = "") -> float | None:
    """NOT SHIPPED — two implementations measured and both rejected.

    The quarter's EPS is plainly in the release. Identifying *which* figure on the
    diluted line is the quarter has now been attempted twice, and scored both times
    against figures already known from XBRL, on the same sample of 40 filings:

        first numeric value on the diluted line : 17/40 correct, 23 not verified
        last  numeric value on the diluted line :  3/40 correct
        header-based column classification      : 10/40 correct, 22 none, 8 WRONG

    Neither is usable, and the header-based attempt is the more dangerous of the two.
    Its failures are silent and plausible: Meta's June 2026 quarter came back as 7.14,
    which is the prior-year comparative on the same line, and Eli Lilly's came back as
    1.07 against an actual 6.21. A reader cannot tell either from a correct figure.

    The reason is that the statements do not share a structure to key on. Alphabet puts
    its quarter pair beside its year-to-date pair, so the line reads
    [2.12, 2.87, 5.9, 7.99]; Meta leads with the current quarter and trails a
    percentage-change column; Micron's reconciliation carries a GAAP and a non-GAAP
    figure where the non-GAAP one is last; and several filers print no date in the
    header at all, which is what `period_end` matching needs.

    So this returns nothing, and a test asserts it goes on doing so. A quarter absent
    from the series is visibly absent; a quarter taken from the wrong column is not.
    """
    raise NotImplementedError(
        "quarterly EPS extraction has been measured twice and is not reliable enough "
        "to return a figure: the header-based attempt was wrong for 8 of 40 filings, "
        "silently returning the comparative column"
    )


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
