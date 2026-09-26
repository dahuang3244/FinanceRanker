"""The beat/miss must compare two figures on one basis.

This has been wrong twice, in two different ways, so it is pinned here.

Attempt 1 compared a GAAP actual against an adjusted consensus. Those are
different measures, so the percentage measured the gap between two definitions
rather than the gap between a result and a forecast. It produced "Beat +3.3%" for
Qualcomm on a quarter whose GAAP EPS (1.87) sat *below* the estimate (2.22).

Attempt 2 used the analyst feed's actual and estimate as a pair, which is
internally consistent, but still displayed them beside this site's GAAP figure
without saying which two were compared — inviting the reader to make the invalid
comparison themselves.

The rule: the surprise is computed from one source's own actual/estimate pair, and
the payload records the actual it used, so the UI can state it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent


def test_the_surprise_is_recorded_with_the_figure_it_used():
    """The actual must be recorded, or the UI cannot say what was compared."""
    import inspect

    from app import main

    source = inspect.getsource(main.eps_quarters)
    assert 'estimate = getattr(match, "eps_estimate", None)' in source
    assert "row.consensus_eps = estimate" in source
    assert "row.surprise_actual = actual" in source
    assert "actual - estimate) / abs(estimate)" in source, (
        "the surprise must be the difference of those two figures"
    )
    assert "row.surprise_basis = basis" in source, (
        "the basis must be recorded so the note can name it"
    )


def test_the_consensus_is_compared_with_the_adjusted_figure():
    """The consensus is a non-GAAP forecast, so it must meet a non-GAAP actual.

    Measured across the pool, the estimate's level tracks the adjusted figure for
    seven of nine filers whose two bases differ. Alphabet is the clear case: its
    forward estimate sits near an adjusted 2.37, not the GAAP 4.85. Comparing the
    consensus with GAAP therefore measures the gap between two definitions — which
    is what produced a "+214% beat" for a quarter whose adjusted EPS (2.48) was
    *below* the 2.90 estimate.
    """
    import inspect

    from app import main

    source = inspect.getsource(main.eps_quarters)
    assert "bases_differ" in source, "the two bases must be compared"
    assert 'actual = adjusted if bases_differ else gaap' in source, (
        "the consensus must be met with the adjusted figure when the bases differ"
    )
    assert 'basis = "adjusted" if bases_differ else "gaap"' in source, (
        "the basis used must be recorded so the UI can state it"
    )


def test_the_basis_choice_is_checkable_not_arbitrary():
    """The switch happens only when the two bases differ by a material margin."""
    import inspect

    from app import main

    source = inspect.getsource(main.eps_quarters)
    assert "abs(gaap - adjusted) > abs(estimate) * 0.10" in source, (
        "an immaterial difference must not switch the basis"
    )


def test_a_remaining_mismatch_is_still_flagged():
    """Even on the right basis the figure can sit far from the consensus.

    That points at a definition this site does not capture, so it is flagged
    rather than presented as a clean surprise.
    """
    import inspect

    from app import main

    source = inspect.getsource(main.eps_quarters)
    assert "surprise_mixed_basis = True" in source
    assert "abs(estimate) * 0.35" in source, (
        "the flag must rest on a stated threshold"
    )


def test_the_basis_note_names_the_basis_it_used():
    """The card must say which of its two figures the consensus was compared with."""
    view = (ROOT / "app" / "web" / "static" / "js" / "company-view.js").read_text(encoding="utf-8")
    assert "function basisNote" in view, "there must be a basis note"
    assert 'q.surprise_basis === "adjusted"' in view, (
        "the note must branch on the basis actually used"
    )
    assert "co.eps.basisAdjusted" in view and "co.eps.basisGaap" in view

    i18n = (ROOT / "app" / "web" / "static" / "js" / "i18n.js").read_text(encoding="utf-8")
    for key in ("co.eps.basisAdjusted", "co.eps.basisGaap"):
        assert key in i18n, f"{key} missing"
    for placeholder in ("{actual}", "{estimate}"):
        assert placeholder in i18n, f"{placeholder} missing from the basis note"


def test_the_consensus_column_is_labelled_non_gaap():
    """A consensus labelled plainly 'consensus' invites comparing it with GAAP."""
    i18n = (ROOT / "app" / "web" / "static" / "js" / "i18n.js").read_text(encoding="utf-8")
    assert "co.eps.nonGaap" in i18n
    view = (ROOT / "app" / "web" / "static" / "js" / "company-view.js").read_text(encoding="utf-8")
    assert 't("co.eps.nonGaap")' in view, "the adjusted column must be labelled non-GAAP"


def test_the_analyst_fallback_still_records_its_basis():
    """A filer with no XBRL quarters still gets a labelled beat/miss."""
    import inspect

    from app import quarterly

    source = inspect.getsource(quarterly._from_analyst)
    assert 'source="analyst"' in source
    assert "consensus_eps=" in source and "surprise_pct=" in source


def test_a_default_refresh_preserves_the_last_universe():
    """A refresh with no explicit list must not shrink a hand-picked pool.

    This happened twice: a 15-name pool silently became the 13-name configured
    default, and two companies dropped out of the ranking with nothing saying so.
    """
    import inspect

    from app import main

    source = inspect.getsource(main.refresh)
    assert "store.list_runs" in source, (
        "the refresh must consult the previous run's universe"
    )
    assert "previous if len(previous) > 1 else settings.ticker_list" in source, (
        "the previous universe must win when usable, with the configured default "
        "only as a fallback"
    )
    assert 'first.get("tickers") if isinstance(first, dict)' in source, (
        "list_runs may return a dict or a model, so both shapes must be read"
    )


def test_the_analyst_join_attaches_only_when_the_sources_agree():
    """Enriching one source's quarter with another's figure must be gated.

    A calendar-month match alone still mis-joined Qualcomm: its March 2026 filings
    give a GAAP EPS of 6.88 while the feed reports an actual of 2.65 for the same
    month, so those are not the same measurement. Attaching the 6.88 turned a 3.3%
    beat into 168%. The attach is therefore conditional on the feed's own actual
    agreeing with this site's GAAP figure.
    """
    import inspect

    from app import main

    source = inspect.getsource(main.analyst_detail)
    assert "tolerance = max(" in source, "the join needs a tolerance"
    assert "if abs(feed_actual - gaap) > tolerance:" in source, (
        "a quarter whose two sources disagree must not be enriched"
    )
    assert "continue" in source


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
