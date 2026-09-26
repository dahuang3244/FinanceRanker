"""Dated corporate events from the SEC submissions index.

An 8-K is the filing a company makes when something happens, and its *item codes*
say what: `5.02` an officer change, `2.01` a completed acquisition, `1.01` a
material agreement. That makes the index a dated, factual record of what a company
actually did — which is what a track-record timeline and a catalyst list are built
from, without needing filing prose or a paid feed.

Only the index is read, never the filing text. The event is therefore reported as
"material agreement, filed 2026-09-03", which is verifiable, rather than as a
summary of what the agreement said, which would not be.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from app import cache
from app.config import settings
from app.http import fetch
from app.models import CompanyEvent
from app.providers.fundamentals import lookup_cik

log = logging.getLogger(__name__)

NS = "sec_events"
TTL = 6 * 3600

# 8-K item codes, and what each one means. Only codes that bear on a company's
# story are named; anything else is reported as "other" rather than being dropped
# or given an invented meaning.
ITEM_LABELS: dict[str, str] = {
    "1.01": "Entered a material agreement",
    "1.02": "Terminated a material agreement",
    "2.01": "Completed an acquisition or disposal",
    "2.02": "Reported results of operations",
    "2.03": "Created a direct financial obligation",
    "2.05": "Costs associated with exit or disposal",
    "2.06": "Material impairment",
    "3.01": "Delisting or listing failure",
    "4.01": "Changed certifying accountant",
    "4.02": "Non-reliance on previously issued financials",
    "5.01": "Changed control of the company",
    "5.02": "Management change",
    "5.03": "Amended bylaws or fiscal year",
    "5.07": "Shareholder vote results",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other material event",
    "9.01": "Financial statements and exhibits",
}

# Items that are routine reporting rather than news. 2.02 is the quarterly print
# and 9.01 the exhibit list; on their own neither is an event worth a timeline row.
ROUTINE_ITEMS = {"2.02", "7.01", "9.01"}
MANAGEMENT_ITEMS = {"5.02", "5.01", "4.01"}
STRATEGIC_ITEMS = {"1.01", "1.02", "2.01", "2.03", "2.05", "2.06", "5.03", "8.01"}


def _classify(codes: list[str], form: str = "8-K") -> str:
    """Bucket a filing by subject, for the timeline's grouping.

    A 6-K carries no item codes at all: a foreign private issuer reports "whatever
    it made public" and the form says nothing about what that was. So a 6-K is
    classified as a disclosure, and the UI labels it that way rather than implying
    a category the filing does not state.
    """
    if form.startswith("6-K"):
        return "disclosure"
    if any(c in MANAGEMENT_ITEMS for c in codes):
        return "management"
    if "2.01" in codes:
        return "acquisition"
    if codes and all(c in ROUTINE_ITEMS for c in codes):
        return "results"
    if any(c in STRATEGIC_ITEMS for c in codes):
        return "strategic"
    return "other"


def get_company_events(ticker: str, *, limit: int = 14,
                       max_age_days: int = 365 * 6) -> list[CompanyEvent]:
    """Dated 8-K events for one company, newest first.

    Returns an empty list for a filer with no CIK — a foreign private issuer, or a
    ticker outside the SEC map. That is a real answer, not a failure, and the UI
    says so rather than showing an empty table with no explanation.
    """
    cik = lookup_cik(ticker)
    if cik is None:
        return []

    key = f"{ticker}:{limit}"
    hit = cache.get(NS, key, TTL)
    if hit:
        try:
            return [CompanyEvent.model_validate(item) for item in hit]
        except Exception:  # pragma: no cover - a stale cache shape is not fatal
            pass

    try:
        payload = fetch(
            f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json",
            headers={"User-Agent": settings.sec_user_agent},
            namespace="sec_submissions",
            ttl=TTL,
            expect_json=True,
            retries=2,
        )
    except Exception as exc:  # noqa: BLE001 - events are additive, never fatal
        log.debug("submissions fetch failed for %s: %s", ticker, exc)
        return []

    recent = (payload.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accessions = recent.get("accessionNumber") or []
    documents = recent.get("primaryDocument") or []
    items = recent.get("items") or []

    cutoff = date.today() - timedelta(days=max_age_days)
    events: list[CompanyEvent] = []
    for index, form in enumerate(forms):
        # 8-K is the domestic current report; 6-K is what a foreign private issuer
        # files instead. Ignoring 6-K left every such filer — TSMC, for one — with
        # an empty record despite having a CIK and filing regularly.
        if form not in ("8-K", "8-K/A", "6-K", "6-K/A"):
            continue
        try:
            filed = date.fromisoformat(dates[index])
        except (IndexError, ValueError, TypeError):
            continue
        if filed < cutoff:
            continue

        raw = items[index] if index < len(items) else ""
        if isinstance(raw, list):
            raw = ",".join(str(x) for x in raw)
        codes = [c.strip() for c in str(raw or "").split(",") if c.strip()]
        labels = [ITEM_LABELS.get(c, f"Item {c}") for c in codes]
        # A 6-K has no items, so "routine" cannot be judged from codes; it is
        # treated as substantive, because the issuer chose to file it.
        is_foreign = form.startswith("6-K")
        meaningful = [c for c in codes if c not in ROUTINE_ITEMS]
        if is_foreign and not codes:
            labels = ["Foreign issuer disclosure (Form 6-K)"]

        accession = (accessions[index] if index < len(accessions) else "") or ""
        document = (documents[index] if index < len(documents) else "") or ""
        events.append(CompanyEvent(
            date=filed,
            form=form,
            items=codes,
            labels=labels,
            kind=_classify(codes, form),
            is_routine=bool(meaningful) is False and not is_foreign,
            url=(f"https://www.sec.gov/Archives/edgar/data/{cik}/"
                 f"{accession.replace('-', '')}/{document}" if accession and document else ""),
        ))

    events.sort(key=lambda e: e.date, reverse=True)
    events = events[:limit]
    cache.put(NS, key, [e.model_dump(mode="json") for e in events])
    return events
