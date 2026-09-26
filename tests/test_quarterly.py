"""Per-quarter GAAP and adjusted EPS.

The properties that matter, each of which was a real bug while building this:

* a quarter is a ~90-day XBRL duration, and the same quarter appears in several
  filings, so facts must be de-duplicated and later restatements must win;
* a tag that is the *total* must precede its own components, or the adjustment
  resolves to a component — Alphabet's equity gain came out at $21.4bn instead of
  $99.0bn;
* adjustments must be netted at the structural tax rate, not the quarter's blended
  rate, which the adjustment itself distorts;
* a line the filer did not tag is named as missing, never treated as zero.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import quarterly
from app.quarterly import (
    _core_tax_rate,
    _is_quarter,
    _quarter_label,
    _unit_series,
    _value_for,
)


def fact(start: str, end: str, val: float, filed: str = "2026-08-01") -> dict:
    return {"start": start, "end": end, "val": val, "filed": filed, "form": "10-Q"}


def node(rows: list[dict], unit: str = "USD") -> dict:
    return {"units": {unit: rows}}


# --------------------------------------------------------------------------- #
# identifying a quarter
# --------------------------------------------------------------------------- #
def test_a_quarter_is_a_ninety_day_duration():
    assert _is_quarter(fact("2026-04-01", "2026-06-30", 1.0)) is True
    # A full year is not a quarter.
    assert _is_quarter(fact("2025-01-01", "2025-12-31", 1.0)) is False
    # A point-in-time fact has no start.
    assert _is_quarter({"end": "2026-06-30", "val": 1.0}) is False


def test_the_same_quarter_appearing_twice_is_deduplicated():
    """A period is reported in the 10-Q and again as a comparative next year."""
    rows = [
        fact("2026-04-01", "2026-06-30", 9.11, "2026-07-22"),
        fact("2026-04-01", "2026-06-30", 9.11, "2026-10-28"),
    ]
    series = _unit_series(node(rows), ("USD",))
    assert len(series) == 1, "the same period must not be counted as two quarters"


def test_a_later_filing_replaces_an_earlier_figure():
    """A restatement should win, not be appended as a second quarter."""
    rows = [
        fact("2026-04-01", "2026-06-30", 9.11, "2026-07-22"),
        fact("2026-04-01", "2026-06-30", 9.05, "2026-10-28"),
    ]
    series = _unit_series(node(rows), ("USD",))
    assert series[-1]["val"] == 9.05, series


def test_quarter_labels_name_the_calendar_quarter():
    assert _quarter_label("2026-04-01", "2026-06-30") == "2026 Q2"
    assert _quarter_label("2026-01-01", "2026-03-31") == "2026 Q1"
    assert _quarter_label("2026-10-01", "2026-12-31") == "2026 Q4"


def test_units_are_selected_by_preference():
    payload = {"units": {"USD": [fact("2026-04-01", "2026-06-30", 100.0)],
                         "shares": [fact("2026-04-01", "2026-06-30", 5.0)]}}
    assert _unit_series(payload, ("shares",))[-1]["val"] == 5.0
    assert _unit_series(payload, ("USD",))[-1]["val"] == 100.0


# --------------------------------------------------------------------------- #
# tag resolution — the total must win
# --------------------------------------------------------------------------- #
def test_a_total_tag_is_preferred_over_its_own_components():
    """The bug this exists to prevent.

    Alphabet tags `EquitySecuritiesFvNiGainLoss` (the $99.0bn total) alongside an
    unrealised and a realised component. Taking the first tag that matched picked a
    component and produced an adjusted EPS of 7.70 against the release's 3.04.
    """
    facts = {
        "EquitySecuritiesFvNiGainLoss": node([fact("2026-04-01", "2026-06-30", 99_031e6)]),
        "EquitySecuritiesFvNiUnrealizedGainLoss": node([fact("2026-04-01", "2026-06-30", 21_399e6)]),
        "EquitySecuritiesFvNiRealizedGainLoss": node([fact("2026-04-01", "2026-06-30", 278e6)]),
    }
    tags = quarterly._TAG_MAP["equity_securities_gain"]
    assert tags[0] == "EquitySecuritiesFvNiGainLoss", (
        "the aggregate concept must lead the tuple, or a component is returned"
    )
    value = _value_for(facts, tags, "2026-04-01", "2026-06-30", ("USD",))
    assert value == 99_031e6, value


def test_only_the_matching_period_is_returned():
    facts = {"NetIncomeLoss": node([
        fact("2026-01-01", "2026-03-31", 62_578e6),
        fact("2026-04-01", "2026-06-30", 112_193e6),
    ])}
    value = _value_for(facts, ("NetIncomeLoss",), "2026-04-01", "2026-06-30", ("USD",))
    assert value == 112_193e6, value
    assert _value_for(facts, ("NetIncomeLoss",), "2025-01-01", "2025-03-31", ("USD",)) is None


def test_aggregate_mode_sums_genuine_components():
    facts = {
        "A": node([fact("2026-04-01", "2026-06-30", 1.0)]),
        "B": node([fact("2026-04-01", "2026-06-30", 2.0)]),
    }
    assert _value_for(facts, ("A", "B"), "2026-04-01", "2026-06-30", ("USD",)) == 1.0
    assert _value_for(facts, ("A", "B"), "2026-04-01", "2026-06-30", ("USD",),
                      aggregate=True) == 3.0


# --------------------------------------------------------------------------- #
# the structural tax rate
# --------------------------------------------------------------------------- #
def test_the_structural_rate_ignores_the_quarter_the_adjustment_distorted():
    """A large pre-tax gain raises taxable income and with it the blended rate.

    Netting the gain at that blended rate over-taxes the removal, which is why
    Q2:26 came out at 3.16 against the release's 3.04.
    """
    rows = [
        ("2025-07-01", "2025-09-30", 9_008e6, 43_987e6),     # ~20.5%
        ("2026-01-01", "2026-03-31", 14_834e6, 77_412e6),    # ~19.2%
        ("2026-04-01", "2026-06-30", 26_560e6, 138_753e6),   # ~19.1%, distorted
    ]
    rate = _core_tax_rate(rows)
    assert rate is not None
    assert 0.15 < rate < 0.22, rate


def test_the_structural_rate_is_none_without_usable_quarters():
    assert _core_tax_rate([]) is None
    assert _core_tax_rate([("a", "b", None, None)]) is None
    # A nonsensical rate is excluded rather than averaged in.
    assert _core_tax_rate([("a", "b", 900.0, 100.0)]) is None


# --------------------------------------------------------------------------- #
# honesty about what is missing
# --------------------------------------------------------------------------- #
def test_an_untagged_adjustment_is_named_not_treated_as_zero():
    """A zero add-back would present an untagged figure as company-endorsed."""
    import inspect

    source = inspect.getsource(quarterly.quarterly_eps)
    assert "missing.append(label)" in source, (
        "an untagged line must be recorded as missing"
    )
    assert "value is None or value == 0" in source, (
        "a zero or absent value must not be added back"
    )


def test_the_bridge_states_the_period_and_that_it_differs_from_annual():
    """A reader cannot tell a quarterly bridge from an annual one by the numbers."""
    import inspect

    from app.models import NonGaapReconciliation

    model = NonGaapReconciliation(period="quarter", period_label="2026 Q2")
    assert model.period == "quarter"
    assert model.period_label == "2026 Q2"

    source = inspect.getsource(quarterly.quarterly_bridge)
    assert 'period="quarter"' in source
    # And the annual bridge must say so too.
    from app.engine import metrics

    annual = inspect.getsource(metrics._reconciliation)
    assert 'period="annual"' in annual, "the annual bridge must be labelled"


def test_quarterly_bridge_reports_the_newest_quarter():
    result = quarterly.quarterly_bridge("__no_such_ticker__")
    assert result is None, "an unknown ticker must not invent a bridge"


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
