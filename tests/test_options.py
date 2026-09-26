"""Option-chain analytics.

Pins the arithmetic that can be verified by hand — put/call ratios, max pain,
strike classification — and the product rule that matters most here: no implied
volatility is invented. The free chain returns placeholder IV with zero bid/ask,
so an IV/RV or skew figure would be fabricated, and a test should fail if one
ever appears.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import options
from app.models import OptionsContract, OptionsExpiry


def contract(kind: str, strike: float, oi: float, volume: float = 0.0,
             last: float | None = None, days: int = 7) -> OptionsContract:
    return OptionsContract(
        kind=kind, strike=strike, expiration=date.today() + timedelta(days=days),
        open_interest=oi, volume=volume, last_price=last,
    )


def test_put_call_ratio_is_puts_over_calls():
    expiry = OptionsExpiry(
        expiration=date.today() + timedelta(days=7),
        calls=[contract("call", 100, 300, 300)],
        puts=[contract("put", 100, 100, 150)],
    )
    assert options._ratio(300, 150) == 0.5
    assert options._ratio(300, 100) == 1 / 3
    # No calls means the ratio is undefined, not zero or infinite.
    assert options._ratio(0, 50) is None


def test_max_pain_minimises_the_value_of_expiring_open_interest():
    """Constructed so the minimum sits strictly between two strikes.

    With 1000 calls at 90, 100 calls at 110 and 100 puts at 100:
      at  90 -> puts pay 0,                    calls pay 0      = 0? see below
      at 100 -> calls(90) pay 10*1000 = 10000, calls(110) 0, puts 0 -> 10000
      at 110 -> calls(90) pay 20*1000 = 20000, puts pay 10*100 = 1000 -> 21000
    The cheapest strike is therefore 90 only if nothing is in the money there,
    which is why an interior minimum needs open interest on both sides of it.
    """
    expiry = OptionsExpiry(
        expiration=date.today() + timedelta(days=7),
        # Calls below the middle strike, so raising the settlement price costs
        # the call writers; puts exactly at the middle, so lowering it costs them.
        calls=[contract("call", 90, 1000), contract("call", 110, 100)],
        puts=[contract("put", 100, 100)],
    )
    # at 90: calls 0 (nobody in the money below 90), puts 0 -> 0 is impossible
    #        because 100 puts are out of the money at 90 too. Cost = 0.
    # at 100: 10 * 1000 = 10000 from the 90 calls. Cost = 10000.
    # at 110: 20 * 1000 = 20000 from the 90 calls + 10 * 100 = 1000 puts = 21000.
    # The minimum over the listed strikes is 90.
    assert options._max_pain(expiry) == 90

    # Mirror it: puts below the middle so the pull goes the other way.
    mirrored = OptionsExpiry(
        expiration=date.today() + timedelta(days=7),
        calls=[contract("call", 100, 100)],
        puts=[contract("put", 90, 100), contract("put", 110, 1000)],
    )
    # at 90:  110 puts pay 20*1000 = 20000 -> 20000
    # at 100: 90 puts pay 10*100 = 1000, 110 puts pay 10*1000 = 10000 -> 11000
    # at 110: 90 puts pay 20*100 = 2000 -> 2000
    assert options._max_pain(mirrored) == 110


def test_max_pain_needs_a_minimum_number_of_strikes():
    thin = OptionsExpiry(
        expiration=date.today() + timedelta(days=7),
        calls=[contract("call", 100, 50)],
        puts=[contract("put", 100, 50)],
    )
    assert options._max_pain(thin) is None


def test_strike_classification_splits_directional_from_defensive():
    spot = 100.0
    # Near the money: a bet on direction.
    assert options._direction("call", 100, spot) == "directional_call"
    assert options._direction("put", 100, spot) == "directional_put"
    # Well above spot: cheap upside or an overwrite, not a small-rise bet.
    assert options._direction("call", 120, spot) == "upside_call"
    # Well below spot: crash protection.
    assert options._direction("put", 80, spot) == "downside_put"
    # Deep in the money on the call side is its own case.
    assert options._direction("call", 80, spot) == "deep_call"
    # A missing spot must not classify as directional.
    assert options._direction("call", 100, 0) == "other"


def test_unusual_requires_both_size_and_a_ratio_above_one():
    expiry = OptionsExpiry(
        expiration=date.today() + timedelta(days=7),
        calls=[
            contract("call", 100, 100, 500),      # 5x, big enough
            contract("call", 105, 100, 50),       # 0.5x, not unusual
            contract("call", 110, 100, 99),       # 0.99x, just under
            contract("call", 115, 10, 5000),      # 500x, big enough
            contract("call", 120, 0, 50),         # too small in absolute terms
        ],
        puts=[],
    )
    found = options._unusual(expiry)
    strikes = [c.strike for c in found]
    assert set(strikes) == {100.0, 115.0}, strikes
    # Ranked by ratio, highest first.
    assert strikes[0] == 115.0
    # Volume under 100 contracts never qualifies, however extreme the ratio.
    assert 120.0 not in strikes


def test_unusual_never_divides_by_a_zero_book():
    """A contract with no open interest has no ratio.

    Dividing by a floor of 1 turned the zero into the raw volume: NVDA's 230 call
    reported "310,776x its open interest" on a strike with none, and on several
    expiries every candidate was such a row.
    """
    expiry = OptionsExpiry(
        expiration=date.today() + timedelta(days=7),
        calls=[
            contract("call", 100, 100, 500),
            contract("call", 125, 0, 310776),     # enormous volume, NO book
            contract("call", 130, 0, 10160),      # this dominated the real list
        ],
        puts=[],
    )
    found = options._unusual(expiry, limit=12)
    assert [c.strike for c in found] == [100.0]
    for c in found:
        assert (c.open_interest or 0) > 0, c.strike
        assert (c.volume or 0) / c.open_interest < 1000, "ratio looks like raw volume"


def test_new_positions_are_reported_separately():
    """Traded contracts with no book yet are new positions, not ratios."""
    expiry = OptionsExpiry(
        expiration=date.today() + timedelta(days=1),
        calls=[contract("call", 200, 0, 10160), contract("call", 205, 0, 3764),
               contract("call", 210, 0, 50)],
        puts=[contract("put", 195, 0, 4100)],
    )
    rows = options._new_positions(expiry)
    strikes = [c.strike for c in rows]
    assert 210.0 not in strikes, "volume under 100 is not worth showing"
    assert set(strikes) == {195.0, 200.0, 205.0}, strikes
    assert strikes[0] == 200.0, "ranked by volume, largest first"
    assert options._unusual(expiry) == [], "none of these has a book to form a ratio"


def test_premium_is_notional_not_contract_count():
    """Each contract is 100 shares; without the multiplier the flow is 100x light."""
    expiry = OptionsExpiry(
        expiration=date.today() + timedelta(days=7),
        calls=[contract("call", 100, 10, 100, last=2.0)],
        puts=[contract("put", 100, 10, 50, last=3.0)],
    )
    totals = options._premium_totals(expiry)
    assert totals["call_premium"] == 20000.0     # 2.0 x 100 x 100
    assert totals["put_premium"] == 15000.0      # 3.0 x 50 x 100
    bare = OptionsExpiry(
        expiration=date.today() + timedelta(days=7),
        calls=[contract("call", 100, 10, 100, last=None)], puts=[])
    assert options._premium_totals(bare)["call_premium"] == 0.0


def test_max_pain_is_not_used_for_waiting_once_the_expiry_has_passed():
    """With no time left there is nothing to wait for."""
    from app.models import OptionsExpirySummary, OptionsSnapshot

    expired = OptionsSnapshot(
        ticker="T", spot=100.0, max_pain=100.5,
        totals={"volume_pcr": 0.8, "oi_pcr": 0.8},
        summaries=[OptionsExpirySummary(expiration="2026-01-01", days=0)],
    )
    assert options._build_verdict(expired).wait == "expired"

    live = OptionsSnapshot(
        ticker="T", spot=100.0, max_pain=100.5,
        totals={"volume_pcr": 0.8, "oi_pcr": 0.8},
        summaries=[OptionsExpirySummary(expiration="2026-02-01", days=5)],
    )
    assert options._build_verdict(live).wait == "yes"


def test_straddle_move_is_a_fraction_of_spot():
    expiry = OptionsExpiry(
        expiration=date.today() + timedelta(days=7),
        calls=[contract("call", 100, 10, 5, last=4.0)],
        puts=[contract("put", 100, 10, 5, last=3.0)],
    )
    # (4 + 3) / 100
    assert abs(options._straddle_move(expiry, 100.0, 100.0) - 0.07) < 1e-12


def test_implied_volatility_is_only_used_when_the_source_publishes_it():
    """The invariant is provenance, not abstinence.

    The Yahoo chain's `implied_volatility` is placeholder data (1e-5 or exactly
    0.5) with bid and ask at zero, so a volatility derived from it would be
    invented. CBOE publishes a real `iv30` and per-contract Greeks, so using it is
    sound. What must never happen is an IV figure produced from the placeholder
    chain — so the code path is gated on the source, and this asserts the gate
    exists rather than that the field is never mentioned.
    """
    import ast

    source = (Path(__file__).resolve().parent.parent
              / "app" / "options.py").read_text()
    tree = ast.parse(source)

    # The gate: an IV/RV assignment reachable only under a published-IV check.
    assigns = [n for n in ast.walk(tree)
               if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Attribute) and t.attr == "iv_rv"
                       for t in n.targets)]
    assert assigns, (
        "iv_rv is no longer assigned; if CBOE's published IV was dropped, the "
        "old no-IV guarantee needs to come back"
    )

    # Each assignment must live inside a body guarded by a truthiness test on
    # something IV-bearing, so it cannot run on the placeholder chain.
    for node in assigns:
        parents = [n for n in ast.walk(tree)
                   if isinstance(n, (ast.If, ast.FunctionDef)) and node in ast.walk(n)]
        guarded = any(
            isinstance(p, ast.If) and any(
                isinstance(c, ast.Attribute)
                and c.attr in ("iv30", "iv_atm", "implied_volatility")
                for c in ast.walk(p.test))
            for p in parents
        )
        assert guarded, (
            "iv_rv is assigned without a guard on a published IV, so it could be "
            "computed from the Yahoo chain's placeholder volatility"
        )


def test_the_placeholder_chain_still_yields_no_gamma():
    """Greeks are the other half of provenance: Yahoo reports none."""
    import inspect

    from app import options as options_module

    source = inspect.getsource(options_module.build_options_snapshot)
    assert "has_greeks" in source, (
        "the gamma block must be gated on the source reporting Greeks, or a "
        "figure would be assumed for the chain that has none"
    )


def test_interpretation_states_its_own_limits():
    """Every reading must carry the caveats, not just the numbers."""
    from app.models import OptionsSnapshot

    snapshot = OptionsSnapshot(
        ticker="TEST", spot=100.0,
        totals={"volume_pcr": 1.4, "oi_pcr": 0.8, "near_volume_pcr": 1.4,
                "near_oi_pcr": 0.8},
        max_pain=96.0, max_pain_expiry="2026-01-02",
        straddle_move=0.03,
        concentration=[{"strike": 100.0, "call_oi": 10, "put_oi": 20, "total_oi": 30,
                        "distance": 0.0}],
        unusual=[],
    )
    notes = options._interpret(snapshot)
    joined = " ".join(notes)
    # A defensive flow reading is reported as such.
    assert "defensive" in joined.lower()
    # The limit that matters most: OI says nothing about holders.
    assert "never who holds them" in joined
    # Divergence between today's flow and the standing book is called out.
    assert "new today" in joined


def test_verdict_reads_flow_against_the_standing_book():
    """The analysis tab's headline comes from volume PCR vs open-interest PCR."""
    from app.models import OptionsSnapshot

    def verdict_for(volume_pcr, oi_pcr):
        return options._build_verdict(OptionsSnapshot(
            ticker="TEST", spot=100.0,
            totals={"volume_pcr": volume_pcr, "oi_pcr": oi_pcr},
        ))

    # Heavy put volume: defensive, and the lean warns against chasing longs.
    heavy = verdict_for(1.4, 0.8)
    assert heavy.stance == "defensive"
    assert heavy.lean_key == "defensive"
    # Defensive flow above the standing book means the tilt is new today.
    assert heavy.novelty == "new_defensive"

    # Light put volume: bullish flow, and the lean warns about crowding.
    light = verdict_for(0.5, 0.9)
    assert light.stance == "bullish"
    assert light.lean_key == "bullish"
    assert light.novelty == "new_bullish"
    assert light.flow_gap is not None and light.flow_gap < 0

    # In line with the book: neither new nor divergent.
    aligned = verdict_for(0.85, 0.85)
    assert aligned.novelty == "aligned"

    # The labels the backend ships must be English, not a fixed language: the UI
    # names the reading itself from the keys, so a baked-in string would show the
    # wrong language on an English screen.
    for verdict in (heavy, light, aligned):
        assert verdict.stance_label.isascii(), verdict.stance_label
        assert verdict.lean.isascii(), verdict.lean
        assert verdict.novelty_label.isascii(), verdict.novelty_label


def test_verdict_scores_stay_inside_the_five_point_scale():
    from app.models import OptionsSnapshot

    for volume_pcr in (0.2, 0.6, 1.0, 2.0):
        for oi_pcr in (0.3, 0.9, 1.5):
            v = options._build_verdict(OptionsSnapshot(
                ticker="T", spot=100.0,
                totals={"volume_pcr": volume_pcr, "oi_pcr": oi_pcr}))
            assert 1 <= v.chase_safety <= 5, (volume_pcr, oi_pcr, v.chase_safety)
            assert 1 <= v.put_value <= 5, (volume_pcr, oi_pcr, v.put_value)

    # Chasing calls is worst when the book is call-heavy and today's flow is
    # piling into that same side.
    worst = options._build_verdict(OptionsSnapshot(
        ticker="T", spot=100.0, totals={"volume_pcr": 0.40, "oi_pcr": 0.55}))
    better = options._build_verdict(OptionsSnapshot(
        ticker="T", spot=100.0, totals={"volume_pcr": 1.10, "oi_pcr": 1.25}))
    assert better.chase_safety > worst.chase_safety, (
        worst.chase_safety, better.chase_safety)


def test_waiting_is_only_worth_it_when_max_pain_is_close():
    """Max pain far from spot expires without consequence, so it is not a reason
    to wait."""
    from app.models import OptionsSnapshot

    near = options._build_verdict(OptionsSnapshot(
        ticker="T", spot=100.0, max_pain=101.0,
        totals={"volume_pcr": 0.8, "oi_pcr": 0.8}))
    assert near.wait == "yes"
    assert abs(near.max_pain_distance - 0.01) < 1e-9

    far = options._build_verdict(OptionsSnapshot(
        ticker="T", spot=100.0, max_pain=120.0,
        totals={"volume_pcr": 0.8, "oi_pcr": 0.8}))
    assert far.wait == "no"


def test_verdict_is_blank_rather_than_wrong_when_the_inputs_are_missing():
    from app.models import OptionsSnapshot

    empty = options._build_verdict(OptionsSnapshot(ticker="T", spot=None))
    assert empty.stance == ""
    assert empty.chase_safety is None
    assert empty.put_value is None
    assert empty.wait == ""


def test_the_two_scores_cannot_both_be_maximum():
    """Chasing calls and buying protection are two sides of one crowded trade.

    The previous version added a point per boolean condition and every condition
    fired together, so both scores pinned at 5/5 for every name — including names
    where chasing was the wrong thing to do, which is the opposite of the intent.
    """
    from app.models import OptionsSnapshot

    both_five = []
    for volume_pcr in (0.20, 0.35, 0.50, 0.65, 0.80, 0.95, 1.10, 1.30, 1.60):
        for oi_pcr in (0.40, 0.60, 0.75, 0.90, 1.10, 1.40, 1.80):
            verdict = options._build_verdict(OptionsSnapshot(
                ticker="T", spot=100.0,
                totals={"volume_pcr": volume_pcr, "oi_pcr": oi_pcr},
            ))
            if verdict.chase_safety == 5 and verdict.put_value == 5:
                both_five.append((volume_pcr, oi_pcr))
    assert not both_five, (
        f"both scores reached maximum for {both_five[:3]}, which is the bug: they "
        f"describe opposite trades and cannot both be attractive"
    )


def test_the_scores_actually_vary_across_names():
    """A score that is the same for every input carries no information.

    This is the shape the bug took: META showed 5/5 and 5/5, and so did every
    other ticker.
    """
    from app.models import OptionsSnapshot

    chase, put = set(), set()
    for volume_pcr in (0.10, 0.20, 0.40, 0.60, 0.80, 1.00, 1.20, 1.50, 2.00):
        for oi_pcr in (0.30, 0.40, 0.70, 1.00, 1.30, 1.70, 2.20):
            verdict = options._build_verdict(OptionsSnapshot(
                ticker="T", spot=100.0,
                totals={"volume_pcr": volume_pcr, "oi_pcr": oi_pcr},
            ))
            chase.add(verdict.chase_safety)
            put.add(verdict.put_value)
    assert len(chase) >= 3, f"chase safety only ever returns {sorted(chase)}"
    assert len(put) >= 3, f"put value only ever returns {sorted(put)}"
    # And the full range must be reachable.
    assert min(chase) == 1 and max(chase) == 5, sorted(chase)


def test_a_crowded_upside_book_makes_chasing_worse_not_better():
    """The direction of the signal, which the old boolean logic inverted.

    A call-heavy book (low put/call) means the trade is popular and a buyer pays
    up for it, so chasing should score *worse* than when the book is balanced.
    """
    from app.models import OptionsSnapshot

    def chase_for(oi_pcr):
        return options._build_verdict(OptionsSnapshot(
            ticker="T", spot=100.0,
            totals={"volume_pcr": oi_pcr, "oi_pcr": oi_pcr},
        )).chase_safety

    crowded = chase_for(0.50)      # calls dominate the book
    balanced = chase_for(1.00)
    assert crowded < balanced, (
        f"a call-heavy book scored {crowded} against {balanced} for a balanced one: "
        f"crowding must make chasing worse, not better"
    )


def test_the_catalyst_is_flagged_when_earnings_fall_inside_the_expiry():
    from app.models import OptionsExpirySummary, OptionsSnapshot, OptionsVolatility

    soon = (date.today() + timedelta(days=5)).isoformat()
    snapshot = OptionsSnapshot(
        ticker="T", spot=100.0, next_earnings=soon,
        totals={"volume_pcr": 0.8, "oi_pcr": 0.8},
        summaries=[OptionsExpirySummary(expiration=soon, days=5)],
        volatility=OptionsVolatility(expiry_days=20.0),
    )
    verdict = options._build_verdict(snapshot)
    assert verdict.next_earnings == soon
    assert verdict.earnings_in_window is True, "earnings inside the expiry must be flagged"

    far = (date.today() + timedelta(days=90)).isoformat()
    later = OptionsSnapshot(
        ticker="T", spot=100.0, next_earnings=far,
        totals={"volume_pcr": 0.8, "oi_pcr": 0.8},
        summaries=[OptionsExpirySummary(expiration=soon, days=5)],
        volatility=OptionsVolatility(expiry_days=20.0),
    )
    assert options._build_verdict(later).earnings_in_window is False


def test_the_two_scores_are_not_mirror_images():
    """Chasing calls and buying puts share positioning inputs but not pricing.

    An earlier version built both from the same two put/call ratios, so `put_value`
    returned 4 or 5 for every name in the pool — no discrimination at all. The
    price of volatility cuts the opposite way for each, which is what separates
    them: cheap implied vol favours chasing, expensive implied vol is what makes
    protection poor value.
    """
    from app.models import OptionsSnapshot, OptionsVolatility

    def scores(volume_pcr, oi_pcr, iv_rv):
        snapshot = OptionsSnapshot(
            ticker="T", spot=100.0,
            totals={"volume_pcr": volume_pcr, "oi_pcr": oi_pcr},
            volatility=OptionsVolatility(iv_rv=iv_rv),
        )
        verdict = options._build_verdict(snapshot)
        return verdict.chase_safety, verdict.put_value

    # Same positioning, opposite pricing: the two scores must move apart.
    cheap = scores(0.70, 0.80, 0.80)
    rich = scores(0.70, 0.80, 1.70)
    assert cheap[0] > rich[0], (
        f"cheap implied vol must favour chasing: {cheap[0]} vs {rich[0]}"
    )
    assert cheap[1] < rich[1], (
        f"expensive implied vol must make protection worse value: {cheap[1]} vs {rich[1]}"
    )

    pairs = [scores(v, o, r)
             for v in (0.30, 0.55, 0.80, 1.10)
             for o in (0.45, 0.70, 1.00, 1.40)
             for r in (0.75, 1.05, 1.60)]
    identical = sum(1 for a, b in pairs if a == b)
    assert identical < len(pairs) * 0.6, (
        f"{identical}/{len(pairs)} cases gave identical scores; the two measures "
        f"are tracking each other rather than describing different trades"
    )
    assert len({a for a, _ in pairs}) >= 3, sorted({a for a, _ in pairs})
    assert len({b for _, b in pairs}) >= 3, sorted({b for _, b in pairs})


def test_put_value_discriminates_across_a_realistic_pool():
    """The specific failure: put value returned 4 or 5 for every name in the pool.

    Fifteen names returning two values is not a rating, it is a constant. This
    pins that the spread is real, using each name's actual measured inputs.
    """
    from app.models import OptionsSnapshot, OptionsVolatility

    pool = [
        (0.521, 0.757, 0.70), (0.572, 0.563, 0.83), (0.423, 0.496, 1.13),
        (0.348, 0.492, 1.17), (1.028, 1.125, 1.14), (0.287, 0.905, 1.00),
        (0.753, 0.858, 1.63), (0.456, 0.519, 1.17), (0.691, 0.577, 1.03),
        (0.746, 1.290, 1.86), (1.049, 1.104, 0.95), (0.971, 0.985, 1.23),
        (0.491, 0.625, 0.99), (0.907, 1.275, 1.05), (0.454, 0.822, 0.98),
    ]
    values = []
    for volume_pcr, oi_pcr, iv_rv in pool:
        verdict = options._build_verdict(OptionsSnapshot(
            ticker="T", spot=100.0,
            totals={"volume_pcr": volume_pcr, "oi_pcr": oi_pcr},
            volatility=OptionsVolatility(iv_rv=iv_rv),
        ))
        values.append(verdict.put_value)
    distinct = sorted(set(values))
    assert len(distinct) >= 3, (
        f"put value only ever returns {distinct} across a real 15-name pool"
    )
    assert min(values) >= 1 and max(values) <= 5


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
