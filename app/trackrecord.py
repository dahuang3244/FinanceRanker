"""The dated company record and the catalysts that are not earnings dates.

Two things a reader asks about a company that the financials do not answer:

* **what management has actually done** — the acquisitions, agreements, officer
  changes and capital actions on file, newest first. A spin-off in 2022 and a
  supply agreement in 2025 are a strategy; the same facts as four rows of a
  schedule are not.
* **what is coming** — and not only the earnings date. A power-purchase agreement,
  a completed acquisition or a regulatory milestone is a dated event that moves a
  stock, and none of them appears as an earnings date.

Both are built from the SEC submissions index, so every row is a dated filing
that can be opened and checked. Nothing here reads filing prose, which is why the
"what to watch" column is written as a question rather than as a claim.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from app.models import CatalystRow, CompanyEvent, Milestone

log = logging.getLogger(__name__)

# How far back the record reaches. Long enough for a spin-off or a founding
# merger, short enough to stay about the current strategy.
RECORD_YEARS = 6
RECORD_LIMIT = 14
CATALYST_LIMIT = 10

# What to watch for each class of event. Phrased as a question, because the filing
# index says what happened, not how it turned out — answering that would need the
# filing itself.
WATCH_FOR: dict[str, tuple[str, str]] = {
    "acquisition": (
        "Does the acquired revenue and margin survive the integration?",
        "Follow the next two quarters for consolidated margin",
    ),
    "agreement": (
        "How long is the contract, and who is the counterparty?",
        "Length and counterparty set how durable the revenue is",
    ),
    "obligation": (
        "Is the new debt funding growth or covering a shortfall?",
        "Compare the obligation with operating cash flow",
    ),
    "management": (
        "Did the strategy change with the person, or continue?",
        "A strategy change usually shows in the next filing's segment detail",
    ),
    "impairment": (
        "Was the written-down asset bought recently, and at what price?",
        "An early impairment is evidence about the prior capital allocation",
    ),
    "disposal": (
        "Was the business sold at a gain, and what replaces the earnings?",
        "Check whether the proceeds are reinvested or returned",
    ),
    "strategic": (
        "What did the company disclose, and does it change the outlook?",
        "Read the filing for the substance",
    ),
    "results": (
        "Did revenue and margin confirm or contradict the trend?",
        "Compare against the prior year's same quarter",
    ),
    "governance": (
        "Which resolutions passed, and was any opposed by a meaningful minority?",
        "A large vote against a pay package is a signal about oversight",
    ),
    # A 6-K states nothing about its own subject, so the honest instruction is to
    # open it rather than to pretend the form classifies it.
    "disclosure": (
        "A 6-K says only that the issuer made something public. Open it to see what.",
        "Foreign private issuers report material news this way instead of an 8-K",
    ),
    "other": ("", ""),
}


def _watch_for(event: CompanyEvent) -> tuple[str, str]:
    """What to look at, chosen by the class of filing."""
    if event.kind == "disclosure":
        return WATCH_FOR["disclosure"]
    if event.kind == "acquisition":
        return WATCH_FOR["acquisition"]
    if event.kind == "management":
        return WATCH_FOR["management"]
    if "5.07" in set(event.items or []):
        return WATCH_FOR["governance"]
    if event.kind == "strategic":
        codes = set(event.items or [])
        if "1.01" in codes or "1.02" in codes:
            return WATCH_FOR["agreement"]
        if "2.03" in codes:
            return WATCH_FOR["obligation"]
        if "2.06" in codes:
            return WATCH_FOR["impairment"]
        if "2.05" in codes:
            return WATCH_FOR["disposal"]
        return WATCH_FOR["strategic"]
    if event.kind == "results":
        return WATCH_FOR["results"]
    return WATCH_FOR["other"]


def _event_title(event: CompanyEvent) -> str:
    """A short human label for the filing, from its item codes."""
    codes = set(event.items or [])
    if event.kind == "disclosure":
        return "Foreign issuer disclosure"
    if event.kind == "management":
        return "Management change"
    if event.kind == "acquisition":
        return "Acquisition completed"
    if "1.01" in codes:
        return "Material agreement entered"
    if "1.02" in codes:
        return "Material agreement terminated"
    if "2.03" in codes:
        return "New financial obligation"
    if "2.05" in codes:
        return "Exit or disposal costs"
    if "2.06" in codes:
        return "Material impairment"
    if "5.07" in codes:
        return "Shareholder vote"
    if event.kind == "results":
        return "Results of operations"
    if event.labels:
        return event.labels[0]
    return "Filing"


def build_record(events: list[CompanyEvent] | None, *,
                 limit: int = RECORD_LIMIT, years: int = RECORD_YEARS) -> list[Milestone]:
    """The dated record, newest first.

    Routine quarterly prints are excluded: four earnings a year would bury the
    decisions that shaped the company. The earnings history has its own place on
    the page.
    """
    cutoff = date.today() - timedelta(days=365 * years)
    out: list[Milestone] = []
    for event in events or []:
        if event.is_routine or event.date < cutoff:
            continue
        codes = set(event.items or [])
        kind = event.kind
        if "1.01" in codes or "1.02" in codes:
            kind = "agreement"
        elif "2.03" in codes:
            kind = "obligation"
        out.append(Milestone(
            date=event.date, kind=kind, title=_event_title(event),
            detail=" · ".join(event.labels[:3]), source="sec-8k", url=event.url,
        ))
    out.sort(key=lambda m: m.date, reverse=True)
    return out[:limit]


def build_catalysts(events: list[CompanyEvent] | None, analyst=None, *,
                    limit: int = CATALYST_LIMIT) -> list[CatalystRow]:
    """Dated things that could move the stock, scheduled and already filed.

    The scheduled release comes first because it is the only row with a date in the
    future; the rest are events that have happened and whose consequences are still
    running. Nothing here is a forecast.
    """
    rows: list[CatalystRow] = []

    next_earnings = getattr(analyst, "next_earnings_date", None) if analyst else None
    if next_earnings:
        try:
            when = date.fromisoformat(str(next_earnings)[:10])
            rows.append(CatalystRow(
                date=when, period=str(next_earnings)[:10],
                title="Next earnings release",
                watch="Did revenue and margin confirm the trend the ranking shows?",
                key_figures="Compare with the prior-year same quarter",
                status="pending", source="analyst-estimate", kind="results",
                scheduled=True,
            ))
        except (ValueError, TypeError):
            pass

    for event in events or []:
        if event.is_routine:
            continue
        watch, figures = _watch_for(event)
        rows.append(CatalystRow(
            date=event.date,
            period=event.date.isoformat(),
            title=_event_title(event),
            watch=watch,
            key_figures=figures,
            # Whether a filed event played out is not something the filing index
            # can say, so the column stays honest about that rather than implying
            # it was verified.
            status="filed",
            source="sec-8k",
            kind=event.kind,
            url=event.url,
        ))

    rows.sort(key=lambda r: (not r.scheduled, -r.date.toordinal()))
    return rows[:limit]
