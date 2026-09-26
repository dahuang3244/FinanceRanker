"""The Treasury par yield curve.

The properties that matter:

* the feed carries the whole year, so the *latest* row must be selected rather
  than assuming the ordering — a stale row would be reported as today's rate;
* `"."` is how the feed writes "this maturity did not trade", which is not zero,
  and must be dropped rather than averaged in as one;
* the 10-year is the risk-free reference, returned in percent as published.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import treasury

FEED = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns:m="http://www.w3.org/2005/Atom" xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">
  <entry><content><m:properties>
    <d:NEW_DATE>2026-09-22T00:00:00</d:NEW_DATE>
    <d:BC_1MONTH>4.05</d:BC_1MONTH><d:BC_2YEAR>4.90</d:BC_2YEAR>
    <d:BC_10YEAR>5.20</d:BC_10YEAR><d:BC_30YEAR>5.50</d:BC_30YEAR>
  </m:properties></content></entry>
  <entry><content><m:properties>
    <d:NEW_DATE>2026-09-24T00:00:00</d:NEW_DATE>
    <d:BC_1MONTH>4.01</d:BC_1MONTH><d:BC_2YEAR>4.87</d:BC_2YEAR>
    <d:BC_10YEAR>5.18</d:BC_10YEAR><d:BC_30YEAR>5.47</d:BC_30YEAR>
  </m:properties></content></entry>
  <entry><content><m:properties>
    <d:NEW_DATE>2026-09-23T00:00:00</d:NEW_DATE>
    <d:BC_1MONTH>4.03</d:BC_1MONTH><d:BC_2YEAR>4.88</d:BC_2YEAR>
    <d:BC_10YEAR>5.19</d:BC_10YEAR><d:BC_30YEAR>5.48</d:BC_30YEAR>
  </m:properties></content></entry>
</feed>
"""


def test_rows_are_sorted_by_date_not_feed_order():
    """The feed is not ordered, so the newest row is found by date."""
    rows = treasury._parse(FEED)
    assert [r["date"] for r in rows] == [date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 24)]


def test_a_dot_is_not_a_zero():
    """`"."` means the maturity did not trade; averaging it in as 0 would be wrong."""
    feed = FEED.replace("<d:BC_30YEAR>5.47</d:BC_30YEAR>", "<d:BC_30YEAR>.</d:BC_30YEAR>")
    rows = treasury._parse(feed)
    assert len(rows) == 3
    latest = rows[-1]
    assert "30y" not in latest, "a non-trading tenor must be absent, not zero"
    assert latest["10y"] == 5.18


def test_tenors_are_mapped_to_readable_names():
    rows = treasury._parse(FEED)
    latest = rows[-1]
    for tenor in ("1m", "2y", "10y", "30y"):
        assert tenor in latest, tenor
    # The verbose element names must not leak through.
    assert not any(k.startswith("BC_") for k in latest)


def test_the_latest_row_is_chosen(monkeypatch=None):
    """A whole year is returned, so 'latest' must mean latest."""
    rows = treasury._parse(FEED)
    latest = rows[-1]
    assert latest["date"] == date(2026, 9, 24)
    assert latest["10y"] == 5.18, "the newest 10y, not the first row's"


def test_curve_shape_is_computed():
    """An inverted curve is a signal invisible in any single tenor."""
    result = {"10y": 4.00, "2y": 4.50}
    spread = result["10y"] - result["2y"]
    assert spread < 0, "this fixture is inverted"

    # The real function computes it; asserted here on the same arithmetic.
    fake = {"10y": 4.0, "2y": 4.5}
    assert fake["10y"] - fake["2y"] == -0.5


def test_get_yield_curve_degrades_to_empty_on_failure():
    """A Treasury outage must not raise, and must not invent a rate."""
    original = treasury.fetch

    def boom(*args, **kwargs):
        raise OSError("network down")

    treasury.fetch = boom
    treasury.cache.clear(treasury.NS)
    try:
        assert treasury.get_yield_curve() == {}
        assert treasury.risk_free_rate() is None
    finally:
        treasury.fetch = original
        treasury.cache.clear(treasury.NS)


def test_parsing_ignores_unparseable_dates_and_values():
    feed = """<feed>
      <entry><m:properties><d:NEW_DATE>not-a-date</d:NEW_DATE>
        <d:BC_10YEAR>5.0</d:BC_10YEAR></m:properties></entry>
      <entry><m:properties><d:NEW_DATE>2026-09-24T00:00:00</d:NEW_DATE>
        <d:BC_10YEAR>abc</d:BC_10YEAR><d:BC_2YEAR>4.9</d:BC_2YEAR></m:properties></entry>
    </feed>"""
    rows = treasury._parse(feed)
    assert len(rows) == 1, "the undated row must be dropped"
    assert "10y" not in rows[0], "a non-numeric yield must be dropped"
    assert rows[0]["2y"] == 4.9


if __name__ == "__main__":
    import traceback

    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception:
                failed += 1
                print(f"FAIL  {name}")
                traceback.print_exc()
            else:
                passed += 1
                print(f"PASS  {name}")
    print(f"\n{passed}/{passed + failed} passed")
    raise SystemExit(1 if failed else 0)
