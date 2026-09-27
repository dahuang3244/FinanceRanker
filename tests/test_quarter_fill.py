"""Filling a quarter XBRL did not tag, from the analyst feed.

The merge is where a silent bug would live: two sources keyed differently, joined on
a date, with a cap applied afterwards. This pins the invariants that make it safe.

Run: python tests/test_quarter_fill.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import QuarterlyEps  # noqa: E402
from app.quarterly import _fill_from_analyst, _months_touched  # noqa: E402


class Entry:
    """One row of an analyst earnings history."""

    def __init__(self, end: str, actual: float, estimate: float,
                 surprise: float = 0.0) -> None:
        self.quarter_end = date.fromisoformat(end)
        self.eps_actual = actual
        self.eps_estimate = estimate
        self.surprise_pct = surprise
        self.currency = "USD"


class Feed:
    def __init__(self, entries) -> None:
        self.earnings_history = list(entries)


def sec_row(end: str, gaap: float, adjusted: float | None = None) -> QuarterlyEps:
    return QuarterlyEps(label=end, start="", end=end, gaap_eps=gaap,
                        adjusted_eps=adjusted, source="sec",
                        has_adjustments=adjusted is not None)


def test_a_gap_is_filled_from_the_feed():
    """The case that prompted this: a quarter XBRL omits entirely."""
    rows = [sec_row("2026-06-28", 1.87), sec_row("2026-03-29", 6.88),
            sec_row("2025-12-28", 2.78)]
    feed = Feed([Entry("2026-06-30", 2.21, 2.22), Entry("2026-03-31", 2.65, 2.56),
                 Entry("2025-12-31", 3.50, 3.40), Entry("2025-09-30", 3.00, 2.88)])
    merged = _fill_from_analyst(rows, feed, quarters=4,
                                all_periods=[("2025-09-29", "2025-12-28")])
    assert len(merged) == 4, "the gap should be filled to reach four quarters"
    ends = [row.end for row in merged]
    assert "2025-09-30" in ends, "the missing quarter is the one to add"
    assert ends == sorted(ends, reverse=True), "newest first"


def test_a_filled_row_carries_no_invented_bridge():
    """It must not present a mixed-basis comparison as a derivation."""
    rows = [sec_row("2026-06-28", 1.87, 2.30)]
    feed = Feed([Entry("2025-09-30", 3.00, 2.88, 0.04)])
    merged = _fill_from_analyst(rows, feed, quarters=4, all_periods=[])
    added = [row for row in merged if row.source == "analyst"]
    assert added, "expected one filled row"
    row = added[0]
    assert row.adjusted_eps is None, "no adjusted figure may be invented"
    assert row.has_adjustments is False
    assert not row.lines, "a filled row is not a reconciliation"
    assert row.adjusted_net_income is None
    assert row.consensus_eps == 2.88, "the feed's own pair is carried together"
    assert row.surprise_basis == "analyst-reported", (
        "the basis must be stated so a reader cannot mistake it for the bridge"
    )


def test_a_quarter_already_held_is_never_added_twice():
    """Days apart is the same quarter, whatever the two sources call it."""
    rows = [sec_row("2026-06-28", 1.87), sec_row("2026-03-29", 6.88)]
    # The feed names the same periods a few days later.
    feed = Feed([Entry("2026-06-30", 2.21, 2.22), Entry("2026-03-31", 2.65, 2.56)])
    merged = _fill_from_analyst(rows, feed, quarters=4, all_periods=[])
    assert len(merged) == 2, "no duplicate quarter may be added"
    assert all(row.source == "sec" for row in merged), (
        "a stated quarter must never be replaced by the feed's view of it"
    )


def test_a_spilling_fiscal_quarter_is_not_duplicated():
    """The bug this caught: a quarter ending 2026-04-03 is labelled March.

    Coca-Cola's first quarter of 2026 runs to 2026-04-03, so the app keys it to April
    while the feed labels the same quarter March. A single-month test added it twice —
    once from each source, under two different end dates, which reads as two quarters.
    """
    rows = [sec_row("2026-04-03", 0.91, 0.85), sec_row("2025-12-31", 0.58)]
    feed = Feed([Entry("2026-03-31", 0.86, 0.81), Entry("2025-12-31", 0.58, 0.56)])
    merged = _fill_from_analyst(rows, feed, quarters=4,
                                all_periods=[("2026-01-01", "2026-04-03")])
    labels = [row.label for row in merged]
    assert len(merged) == len(set(row.end for row in merged)), "duplicate end date"
    assert not any(row.end == "2026-03-31" for row in merged), (
        "the March label is the same quarter as the 2026-04-03 period"
    )
    assert labels, "non-empty"


def test_quarters_are_capped_and_ordered():
    rows = [sec_row("2026-06-28", 1.87)]
    feed = Feed([Entry("2025-09-30", 3.00, 2.88), Entry("2025-06-30", 2.50, 2.40),
                 Entry("2025-03-31", 2.00, 1.90)])
    merged = _fill_from_analyst(rows, feed, quarters=3, all_periods=[])
    assert len(merged) == 3, "the cap is the number of quarters asked for"
    ends = [row.end for row in merged]
    assert ends == sorted(ends, reverse=True)
    assert ends[0] == "2026-06-28", "the newest quarter is the stated one"


def test_consecutive_quarters_are_never_near_duplicates():
    """The invariant behind the whole merge: adjacent rows are a quarter apart."""
    rows = [sec_row("2026-06-28", 1.87), sec_row("2026-03-29", 6.88)]
    feed = Feed([Entry("2025-12-31", 2.78, 2.70), Entry("2025-09-30", 3.00, 2.88),
                 Entry("2025-06-30", 2.43, 2.34)])
    merged = _fill_from_analyst(rows, feed, quarters=5, all_periods=[])
    for newer, older in zip(merged, merged[1:]):
        gap = (date.fromisoformat(newer.end) - date.fromisoformat(older.end)).days
        assert 60 <= gap <= 130, (
            f"{newer.end} and {older.end} are {gap} days apart, which is not a quarter"
        )


def test_an_absent_feed_changes_nothing():
    rows = [sec_row("2026-06-28", 1.87)]
    assert _fill_from_analyst(rows, None, quarters=4, all_periods=[]) == rows
    assert _fill_from_analyst(rows, Feed([]), quarters=4, all_periods=[]) == rows


def test_an_entry_without_an_actual_is_skipped():
    """An upcoming quarter reports earnings as 0, which is not a result."""
    rows = [sec_row("2026-06-28", 1.87)]
    feed = Feed([Entry("2026-09-30", 0.0, 1.47)])
    merged = _fill_from_analyst(rows, feed, quarters=4, all_periods=[])
    assert len(merged) == 1, "a future quarter must not be listed as reported"


def test_months_touched_bounds_the_period():
    assert _months_touched("2026-04-01", "2026-04-03") == {"2026-04"}
    assert _months_touched("2025-10-01", "2025-12-31") == {
        "2025-10", "2025-11", "2025-12"}
    assert _months_touched("", "2026-06-30") == {"2026-06"}


if __name__ == "__main__":
    import traceback

    passed = 0
    failed = 0
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not callable(function):
            continue
        try:
            function()
        except Exception:  # noqa: BLE001 - report and continue
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
        else:
            passed += 1
            print(f"PASS  {name}")
    print()
    print(f"{passed}/{passed + failed} passed")
    sys.exit(1 if failed else 0)
