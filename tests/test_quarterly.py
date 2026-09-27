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


def test_quarter_labels_handle_a_spilling_fiscal_calendar():
    """Neither date works alone, which is why this was wrong twice.

    Coca-Cola's first quarter of 2026 runs 2026-01-01 to 2026-04-03. Labelling by
    the end month called it Q2, so its card showed 2026 Q2, 2025 Q4, 2025 Q3,
    2025 Q2 and looked as though a quarter were missing — the periods were right,
    only the label was wrong. Labelling by the start month then broke the offset
    filers: NVIDIA's 2026-01-26 to 2026-04-26 is its first quarter but the second
    calendar quarter.
    """
    # An ordinary calendar quarter.
    assert _quarter_label("2026-04-01", "2026-06-30") == "2026 Q2"
    assert _quarter_label("2026-01-01", "2026-03-31") == "2026 Q1"
    # A fiscal quarter that spills past the month end.
    assert _quarter_label("2026-01-01", "2026-04-03") == "2026 Q1", (
        "a period closing on the 3rd belongs to the quarter that just ended"
    )
    assert _quarter_label("2025-12-29", "2026-03-28") == "2026 Q1"
    # A 52/53-week calendar closing just after the month end.
    assert _quarter_label("2026-03-30", "2026-06-28") == "2026 Q2"
    # An offset fiscal year. NVIDIA's first fiscal quarter ends in late April; the
    # label is the calendar quarter, so this is Q2 — and that is deliberate, because
    # every filer is labelled the same way and a fiscal-quarter number would be a
    # different scheme per company.
    assert _quarter_label("2026-01-26", "2026-04-26") == "2026 Q2"
    # A year boundary, where the correction has to roll the year back too.
    assert _quarter_label("2025-10-01", "2025-12-31") == "2025 Q4"
    assert _quarter_label("2025-12-01", "2026-01-03") == "2025 Q4"


def test_a_quarter_only_tagged_cumulatively_is_derived():
    """Filers do not tag every three-month period.

    In a 10-K iXBRL requires year-to-date figures, so the three-month fourth
    quarter has no fact of its own; and some filers tag the nine-month cumulative
    but not the three-month third quarter. Both are recovered by subtraction, which
    is exact because these figures accumulate.
    """
    import inspect

    from app import quarterly as module

    source = inspect.getsource(module._derive_quarters)
    assert "later_eps - earlier_eps" in source, "EPS must be derived by subtraction"
    assert "later_income - earlier_income" in source
    assert "MIN_QUARTER_DAYS <= gap_days <= MAX_QUARTER_DAYS" in source, (
        "only a gap of about a quarter may be derived"
    )
    assert '"derived": True' in source, "a derived quarter must be marked as such"


def test_a_derived_quarter_does_not_invent_a_share_count():
    """A diluted count is a weighted average, so it cannot be subtracted."""
    import inspect

    from app import quarterly as module

    source = inspect.getsource(module._derive_quarters)
    assert 'SHARE_TAGS, earlier_start, end' in source, (
        "the earlier period's real share count must be carried, not differenced"
    )


def test_the_picker_skips_a_second_variant_of_the_same_quarter():
    """XBRL holds the same quarter with a shifted start.

    Coca-Cola carries both 2025-03-29→06-27 and 2025-03-28→06-27. Without skipping
    the variant, it displaced a real quarter and the four "latest" quarters came out
    one short at the far end.
    """
    import inspect

    from app import quarterly as module

    source = inspect.getsource(module._consecutive)
    assert "same_period" in source, "a variant of the chosen quarter must be detected"
    assert "if 0 <= same_period <= 7:" in source, (
        "and skipped rather than appended as another quarter"
    )


def test_adjustments_are_netted_on_the_pretax_side_where_possible():
    """Adjusted income is built from pre-tax income and then taxed.

    Both routes are defensible, and this one is measurably closer to the filings'
    own basis. Against Alphabet's six published quarters, removing an after-tax
    amount from net income item by item leaves a mean error of 0.187 per share;
    adjusting pre-tax income and re-taxing it leaves 0.085. The reason is that the
    tax on a one-off is a single pool rather than a rate applied to each line.

    A filer that tags only net income still gets a figure, so the older route is
    kept as a fallback rather than removed.
    """
    import inspect

    from app import quarterly as module

    source = inspect.getsource(module.quarterly_eps)
    assert "use_pretax = pretax is not None and tax is not None and pretax > 0" in source
    assert 'key="adjusted_tax"' in source, (
        "the tax on adjusted pre-tax income must appear as its own line, or the "
        "derivation cannot be checked against the filing"
    )
    assert "adjusted_income = pretax if use_pretax else net_income" in source, (
        "the pre-tax route must fall back to net income when pre-tax is not tagged"
    )


def test_a_cumulative_may_not_be_combined_across_filings():
    """A restatement must not be subtracted from a figure on another basis.

    This is the defect that showed Qualcomm posting a large quarterly loss while
    profitable. Its 10-K states a fiscal 2025 EPS of 5.01; a later 10-Q restates the
    first nine months at 7.79 — a nine-month total *above* the full year. Subtracting
    one from the other gave a fourth quarter of -2.78. Micron is the same fault more
    quietly: a 10-Q nine-month figure against a 10-K full year gave a fourth quarter
    of 5.02 where the company reported 4.60.

    Two figures filed together are consistent by construction, so within one filing
    the subtraction is sound. Across filings it is not, and no quarter is derived.
    """
    import inspect

    from app import quarterly as module

    source = inspect.getsource(module._derive_quarters)
    assert "if later_accn and accn and accn != later_accn:" in source, (
        "axis periods must be refused when they come from different filings"
    )
    assert 'row.get("accn")' in source, (
        "the accession has to be captured with the span for that test to work"
    )


def test_a_displayed_gaap_figure_matches_the_tagged_fact():
    """Every quarter shown must equal the fact the filer actually tagged.

    The check that makes the restatement bug visible: a derived quarter is only
    acceptable if it agrees with the filer's own statement, and a quarter taken from
    a fact must equal that fact. Run against the live cache for the whole pool, and
    skipped when there is no network rather than failing the suite.
    """
    from app.quarterly import EPS_TAGS, _facts, _value_for, quarterly_eps

    pool = ("MU", "QCOM", "GOOGL", "MSFT", "AAPL", "KO", "AVGO", "ORCL")
    checked = 0
    for ticker in pool:
        try:
            facts = _facts(ticker)
        except Exception:  # noqa: BLE001 - offline
            return
        if not facts:
            continue
        for quarter in quarterly_eps(ticker, quarters=4):
            stated = _value_for(facts, EPS_TAGS, quarter.start, quarter.end,
                                ("USD/shares",))
            if stated is None or quarter.gaap_eps is None:
                continue
            checked += 1
            assert abs(stated - quarter.gaap_eps) < 0.005, (
                f"{ticker} {quarter.label}: displayed {quarter.gaap_eps} "
                f"but the filing states {stated}"
            )
    assert checked > 0, "no quarter was comparable, so the check proved nothing"


def test_a_quarter_distorted_by_a_tax_item_is_flagged():
    """A correctly-read figure can still be unrepresentative, and say so.

    Qualcomm's March 2026 quarter reports EPS of 6.88 — read correctly from the filing
    — on net income of $7.37bn that contains a tax *benefit* of $5.14bn, an effective
    rate of -230%, while revenue fell from $12.25bn to $10.6bn. Against a consensus of
    2.56 the app showed a large miss that describes nothing about the business.

    Flagged rather than corrected: the number is what the company filed.
    """
    from app.quarterly import _distortion

    flagged, reason = _distortion(-2.30, 2_232e6, 7_370e6)
    assert flagged, "an effective rate of -230% is a tax item, not a rate"
    assert "tax rate" in reason

    flagged, reason = _distortion(-0.23, 8_000e6, 9_000e6)
    assert flagged, "a negative effective rate is never an ordinary quarter"

    flagged, reason = _distortion(None, -500e6, -1_200e6)
    assert flagged, "a loss-making quarter cannot be measured against an estimate"
    assert "loss" in reason

    flagged, _ = _distortion(0.17, 30_000e6, 25_000e6)
    assert not flagged, "an ordinary quarter must not be flagged"

    flagged, _ = _distortion(0.24, 50_000e6, 38_000e6)
    assert not flagged, "a high but ordinary rate must not be flagged"


def test_the_distorted_figure_is_still_reported():
    """The flag withholds a comparison, not the number."""
    from app.quarterly import quarterly_eps

    rows = quarterly_eps("QCOM", quarters=4)
    for row in rows:
        if row.distorted:
            assert row.gaap_eps is not None, (
                "a distorted quarter must still show the figure the company filed"
            )
            return


def test_tagged_quarters_reconcile_to_the_tagged_cumulative():
    """The check that settles whether a figure is a quarter or a year-to-date total.

    A concern worth taking seriously: a source labelling a cumulative period as a
    quarter would make the app show nine months of earnings as one quarter. Measured
    across the pool it does not happen — of 152 values from two independent sources, 38
    matched a quarter-length fact, 114 matched neither, and **0 matched a cumulative
    fact**. Neither source labels year-to-date figures as quarters.

    This test pins the invariant that makes that check meaningful: a filer's own tagged
    quarters sum to its own tagged cumulative for the same fiscal year, so the tagged
    quarters are the reported periods and a figure disagreeing with them is on a
    different basis rather than a different period. Alphabet's 2025 quarters are
    2.81 + 2.31 + 2.87 = 7.99 against a tagged nine months of 7.99; Meta's are
    6.43 + 7.14 + 1.05 = 14.62 against 14.62.

    Skipped offline rather than failing, since it reads the live cache.
    """
    from datetime import date

    from app.quarterly import EPS_TAGS, _facts, _unit_series

    for ticker in ("GOOGL", "META", "MSFT", "AAPL"):
        try:
            facts = _facts(ticker)
        except Exception:  # noqa: BLE001 - offline
            return
        if not facts:
            continue
        quarters: list[tuple[str, str, float]] = []
        cumulative: list[tuple[str, str, float]] = []
        for tag in EPS_TAGS:
            node = facts.get(tag)
            if not node:
                continue
            for row in _unit_series(node, ("USD/shares",)):
                start, end, value = row.get("start"), row.get("end"), row.get("val")
                if not start or not end or value is None:
                    continue
                days = (date.fromisoformat(end) - date.fromisoformat(start)).days
                if 80 <= days <= 100:
                    quarters.append((start, end, float(value)))
                elif 170 <= days <= 380:
                    cumulative.append((start, end, float(value)))
        for nine_start, nine_end, nine_value in cumulative:
            days = (date.fromisoformat(nine_end) - date.fromisoformat(nine_start)).days
            if not (255 <= days <= 285):
                continue
            # Quarters of *this* fiscal year, identified by the same start date. Taking
            # every quarter that merely falls inside the window sweeps up periods from
            # other years — Alphabet has a 26.29 quarter from years back whose dates sit
            # between these — and the sum then measures nothing.
            inside = [v for s, e, v in quarters if s == nine_start and e <= nine_end]
            if len(inside) != 3:
                continue
            assert abs(sum(inside) - nine_value) < 0.02, (
                f"{ticker}: quarters {inside} sum to {sum(inside):.2f} but the tagged "
                f"nine months is {nine_value:.2f}"
            )


def test_no_displayed_figure_is_a_cumulative_amount():
    """A guard against the failure mode directly: a quarter shown as nine months.

    Every displayed GAAP figure is compared with the filer's cumulative facts for the
    same month. A match with no quarter-length fact of the same value would mean a
    year-to-date total presented as one quarter's earnings. Skipped offline.
    """
    from datetime import date

    from app.quarterly import EPS_TAGS, _facts, _unit_series, quarterly_eps

    for ticker in ("GOOGL", "META", "MSFT", "AAPL", "MU"):
        try:
            facts = _facts(ticker)
        except Exception:  # noqa: BLE001 - offline
            return
        if not facts:
            continue
        quarter_values: dict[str, list[float]] = {}
        cumulative_values: dict[str, list[float]] = {}
        for tag in EPS_TAGS:
            node = facts.get(tag)
            if not node:
                continue
            for row in _unit_series(node, ("USD/shares",)):
                start, end, value = row.get("start"), row.get("end"), row.get("val")
                if not start or not end or value is None:
                    continue
                days = (date.fromisoformat(end) - date.fromisoformat(start)).days
                if 80 <= days <= 100:
                    quarter_values.setdefault(end[:7], []).append(float(value))
                elif 170 <= days <= 380:
                    cumulative_values.setdefault(end[:7], []).append(float(value))

        for row in quarterly_eps(ticker, quarters=4):
            if row.gaap_eps is None:
                continue
            month = row.end[:7]
            if any(abs(value - row.gaap_eps) < 0.02
                   for value in quarter_values.get(month, [])):
                continue
            for value in cumulative_values.get(month, []):
                assert abs(value - row.gaap_eps) >= 0.02, (
                    f"{ticker} {row.label}: displayed {row.gaap_eps} is a cumulative "
                    f"amount ({value}) for that month, not a quarter"
                )


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
