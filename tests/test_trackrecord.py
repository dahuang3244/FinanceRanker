"""The dated company record and the catalyst table.

The properties that matter:

* routine quarterly prints are excluded from the record — four earnings a year
  would bury the decisions that shaped the company;
* a foreign private issuer files 6-K, not 8-K, so ignoring that form left every
  such filer with an empty record despite it filing regularly;
* every row is a dated filing that can be opened, and whether an event *worked* is
  never asserted, because a filing index cannot say that.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import trackrecord
from app.models import CompanyEvent


def event(days_ago: int, kind: str, items: list[str], labels: list[str],
          routine: bool = False, form: str = "8-K") -> CompanyEvent:
    return CompanyEvent(
        date=date.today() - timedelta(days=days_ago), form=form, items=items,
        labels=labels, kind=kind, is_routine=routine,
        url="https://www.sec.gov/x",
    )


def analyst(next_earnings: str | None = None):
    return type("A", (), {"next_earnings_date": next_earnings})()


# --------------------------------------------------------------------------- #
# the record
# --------------------------------------------------------------------------- #
def test_record_excludes_routine_quarterly_prints():
    events = [
        event(10, "results", ["2.02", "9.01"], ["Reported results", "Exhibits"], routine=True),
        event(20, "management", ["5.02"], ["Management change"]),
        event(30, "acquisition", ["2.01"], ["Completed an acquisition"]),
    ]
    record = trackrecord.build_record(events)
    titles = [m.title for m in record]
    assert "Management change" in titles
    assert "Acquisition completed" in titles
    assert "Results of operations" not in titles, "a routine print is not a record entry"


def test_record_is_newest_first_and_bounded():
    events = [event(d, "strategic", ["8.01"], ["Other material event"])
              for d in range(10, 600, 30)]
    record = trackrecord.build_record(events, limit=6)
    assert len(record) == 6
    dates = [m.date for m in record]
    assert dates == sorted(dates, reverse=True)


def test_record_drops_events_older_than_the_window():
    events = [event(365 * 9, "acquisition", ["2.01"], ["Completed an acquisition"])]
    assert trackrecord.build_record(events, years=6) == []


def test_a_material_agreement_is_classified_as_an_agreement():
    events = [event(5, "strategic", ["1.01"], ["Entered a material agreement"])]
    record = trackrecord.build_record(events)
    assert record[0].kind == "agreement", record[0].kind
    assert record[0].title == "Material agreement entered"


def test_foreign_issuer_disclosures_are_kept():
    """A 6-K carries no item codes, so it cannot be judged routine.

    TSMC files 6-K rather than 8-K. Treating only 8-K as an event left every
    foreign private issuer with an empty record while it filed regularly.
    """
    events = [
        event(5, "disclosure", [], ["Foreign issuer disclosure (Form 6-K)"], form="6-K"),
        event(40, "disclosure", [], ["Foreign issuer disclosure (Form 6-K)"], form="6-K"),
    ]
    record = trackrecord.build_record(events)
    assert len(record) == 2, "6-K filings must appear in the record"
    assert record[0].title == "Foreign issuer disclosure"
    assert record[0].kind == "disclosure"


# --------------------------------------------------------------------------- #
# the catalyst table
# --------------------------------------------------------------------------- #
def test_the_scheduled_release_comes_first():
    events = [event(5, "management", ["5.02"], ["Management change"])]
    when = (date.today() + timedelta(days=30)).isoformat()
    rows = trackrecord.build_catalysts(events, analyst(when))
    assert rows, "the scheduled release must produce a row"
    assert rows[0].scheduled is True, "a future date sorts first"
    assert rows[0].status == "pending"
    assert rows[0].title == "Next earnings release"
    # And the filed event is still present.
    assert any(not r.scheduled for r in rows)


def test_catalysts_carry_a_watch_question_per_kind():
    events = [
        event(5, "acquisition", ["2.01"], ["Completed an acquisition or disposal"]),
        event(15, "management", ["5.02"], ["Management change"]),
        event(25, "strategic", ["1.01"], ["Entered a material agreement"]),
    ]
    rows = trackrecord.build_catalysts(events)
    by_kind = {r.kind: r for r in rows}
    assert "integration" in by_kind["acquisition"].watch.lower()
    assert "strategy change" in by_kind["management"].watch.lower()
    assert "contract" in by_kind["strategic"].watch.lower()
    for row in rows:
        assert row.watch, f"{row.title} has no watch text"
        assert row.key_figures, f"{row.title} has no key figures"


def test_routine_prints_are_not_catalysts():
    events = [event(5, "results", ["2.02"], ["Reported results"], routine=True)]
    rows = trackrecord.build_catalysts(events, analyst(None))
    assert rows == [], "a routine print is not a catalyst"


def test_nothing_is_claimed_about_whether_an_event_worked():
    """A filing index cannot say an acquisition succeeded, so it must not imply it."""
    events = [event(5, "acquisition", ["2.01"], ["Completed an acquisition"])]
    rows = trackrecord.build_catalysts(events)
    row = rows[0]
    assert row.status == "filed", row.status
    # The status vocabulary must not contain a success claim.
    for word in ("succeeded", "worked", "delivered", "beat", "missed"):
        assert word not in row.status.lower(), word
        assert word not in row.title.lower(), word


def test_a_6k_catalyst_says_to_open_the_filing():
    """The form states nothing about its own subject, so it must not pretend to."""
    events = [event(5, "disclosure", [], ["Foreign issuer disclosure"], form="6-K")]
    rows = trackrecord.build_catalysts(events)
    assert rows
    assert "open it" in rows[0].watch.lower(), rows[0].watch


def test_a_filer_with_no_events_and_no_release_yields_empty_lists():
    assert trackrecord.build_record([]) == []
    assert trackrecord.build_catalysts([], analyst(None)) == []


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
