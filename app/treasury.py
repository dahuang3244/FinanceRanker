"""US Treasury par yield curve, from the Treasury's own published feed.

The risk-free rate is the reference every other return is measured against: a
Sharpe ratio is meaningless without one, and an earnings yield cannot be judged
without knowing what a government bond pays instead.

Source is the Treasury's daily yield-curve XML — an official, zero-auth feed,
updated each business day. Two properties are handled carefully:

* the feed carries the whole year's rows, so the *latest* date is selected rather
  than assuming ordering;
* a value of `"."` is how the feed writes "this maturity did not trade", which is
  not a zero, so it is dropped rather than averaged in as one.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from app import cache
from app.config import settings
from app.http import fetch

log = logging.getLogger(__name__)

NS = "treasury_curve"
TTL = 12 * 3600

URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
       "pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value={year}")

# The feed's element names are verbose; these are the tenors worth keeping.
TENORS: dict[str, str] = {
    "BC_1MONTH": "1m",
    "BC_3MONTH": "3m",
    "BC_6MONTH": "6m",
    "BC_1YEAR": "1y",
    "BC_2YEAR": "2y",
    "BC_5YEAR": "5y",
    "BC_7YEAR": "7y",
    "BC_10YEAR": "10y",
    "BC_20YEAR": "20y",
    "BC_30YEAR": "30y",
}

_ENTRY = re.compile(r"<m:properties>(.*?)</m:properties>", re.S)
_DATE = re.compile(r"<d:NEW_DATE[^>]*>([^<]+)</d:NEW_DATE>")
_FIELD = re.compile(r"<d:([A-Z0-9_]+)[^>]*>([^<]*)</d:\1>")


def _parse(xml: str) -> list[dict]:
    """Each row of the feed as {date, 10y, 2y, ...}, oldest first."""
    rows: list[dict] = []
    for block in _ENTRY.findall(xml):
        stamp = _DATE.search(block)
        if not stamp:
            continue
        try:
            when = date.fromisoformat(stamp.group(1)[:10])
        except ValueError:
            continue
        row: dict = {"date": when}
        for name, value in _FIELD.findall(block):
            tenor = TENORS.get(name)
            if not tenor:
                continue
            # "." means the maturity did not trade; that is not a zero.
            try:
                row[tenor] = float(value)
            except (TypeError, ValueError):
                continue
        if len(row) > 1:
            rows.append(row)
    rows.sort(key=lambda r: r["date"])
    return rows


def get_yield_curve(reference: date | None = None) -> dict:
    """The latest par yields, plus the curve's shape.

    `spread_10y_2y` is included because an inverted curve is a recession signal
    that is invisible in any single tenor, and computing it here keeps the
    definition in one place rather than in each caller.
    """
    today = reference or date.today()
    key = f"{today.year}"
    hit = cache.get(NS, key, TTL)
    if hit:
        rows = [{**row, "date": date.fromisoformat(row["date"])} for row in hit]
    else:
        try:
            xml = fetch(
                URL.format(year=today.year),
                headers={"User-Agent": settings.sec_user_agent or "FinanceRanker/0.1"},
                namespace=NS,
                ttl=TTL,
                retries=2,
            )
        except Exception as exc:  # noqa: BLE001 - the block degrades, nothing else
            log.debug("treasury fetch failed: %s", exc)
            return {}
        if isinstance(xml, bytes):
            xml = xml.decode("utf-8", "replace")
        rows = _parse(str(xml))
        cache.put(NS, key, [{**row, "date": row["date"].isoformat()} for row in rows])

    if not rows:
        return {}

    # The feed carries the whole year, so take the newest row at or before today.
    usable = [r for r in rows if r["date"] <= today] or rows
    latest = usable[-1]
    result = {k: v for k, v in latest.items() if k != "date"}
    result["date"] = latest["date"].isoformat()
    if "10y" in result and "2y" in result:
        result["spread_10y_2y"] = result["10y"] - result["2y"]
    result["source"] = "US Treasury daily par yield curve"
    return result


def risk_free_rate(reference: date | None = None) -> float | None:
    """The 10-year par yield, as a percentage (4.15 means 4.15%).

    Returned in percent to match how the field itself is published and how the
    UI labels it; callers converting to a decimal do so explicitly.
    """
    curve = get_yield_curve(reference)
    return curve.get("10y")
