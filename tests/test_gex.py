"""CBOE option chain and dealer gamma exposure.

The properties that matter:

* the OSI symbol parser must survive adjusted contracts, whose root carries digits
  after a split (`NVDA1`), and split-calendar dates — anchoring on a letters-only
  root would drop every adjusted contract silently;
* `iv30` arrives in percent and must be converted once, not twice;
* a reported Greek of 0.0 is not a measurement, so it must not enter a gamma sum;
* the GEX sign convention is an assumption about dealer positioning, and every
  figure carries it — the number is a convention, not an observation.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import gex
from app.models import OptionsContract, OptionsExpiry
from app.providers.cboe_options import parse_osi


def contract(kind: str, strike: float, gamma: float | None, oi: float | None = 100.0,
             days: int = 5) -> OptionsContract:
    return OptionsContract(
        kind=kind, strike=strike, expiration=date.today() + timedelta(days=days),
        gamma=gamma, open_interest=oi, volume=10,
    )


def expiry(*contracts: OptionsContract) -> OptionsExpiry:
    return OptionsExpiry(expiration=date.today() + timedelta(days=5),
                         calls=[c for c in contracts if c.kind == "call"],
                         puts=[c for c in contracts if c.kind == "put"])


# --------------------------------------------------------------------------- #
# OSI parsing
# --------------------------------------------------------------------------- #
def test_osi_parses_a_standard_symbol():
    parsed = parse_osi("AAPL260925C00330000")
    assert parsed == {"expiry": date(2026, 9, 25), "kind": "call", "strike": 330.0}


def test_osi_parses_an_adjusted_root_with_digits():
    """A split produces roots like NVDA1; a letters-only root drops them."""
    parsed = parse_osi("NVDA1260925C00100000")
    assert parsed is not None, "an adjusted contract must still parse"
    assert parsed["kind"] == "call"
    assert parsed["strike"] == 100.0
    assert parsed["expiry"] == date(2026, 9, 25)

    brk = parse_osi("BRKB1260925P00400000")
    assert brk is not None and brk["kind"] == "put" and brk["strike"] == 400.0


def test_osi_parses_fractional_strikes():
    parsed = parse_osi("SPY260918C00762500")
    assert parsed is not None
    assert parsed["strike"] == 762.5


def test_osi_rejects_nonsense():
    for bad in ("", "AAPL", "AAPL260925X00330000", "AAPL260925C00000000",
                "AAPL261325C00330000", None):
        assert parse_osi(bad) is None, bad


# --------------------------------------------------------------------------- #
# Greeks
# --------------------------------------------------------------------------- #
def test_a_reported_zero_greek_is_not_a_measurement():
    """The feed writes 0.0 where the model produced nothing."""
    assert gex.is_skinny(0.0) is True
    assert gex.is_skinny(None) is True
    assert gex.is_skinny(0.0001) is False


def test_zero_gamma_contributes_nothing_to_the_sum():
    expiries = [expiry(contract("call", 100, gamma=0.0),
                       contract("call", 105, gamma=0.02))]
    total = gex.net_gex(expiries, 100.0)
    assert total is not None
    # Only the 105 strike contributes.
    assert abs(total - gex.contract_gex(contract("call", 105, gamma=0.02), 100.0)) < 1e-9


def test_net_gex_is_unmeasurable_without_gamma():
    expiries = [expiry(contract("call", 100, gamma=None))]
    assert gex.net_gex(expiries, 100.0) is None


# --------------------------------------------------------------------------- #
# sign convention
# --------------------------------------------------------------------------- #
def test_calls_add_and_puts_subtract_by_the_stated_convention():
    call = contract("call", 100, gamma=0.02)
    put = contract("put", 100, gamma=0.02)
    call_value = gex.contract_gex(call, 100.0)
    put_value = gex.contract_gex(put, 100.0)
    assert call_value > 0, "a long call position adds gamma"
    assert put_value < 0, "a short put position subtracts under the convention"
    assert abs(call_value + put_value) < 1e-9, "equal gamma must cancel"

    net = gex.net_gex([expiry(call, put)], 100.0)
    assert abs(net) < 1e-9, net


def test_every_figure_carries_the_assumption_it_rests_on():
    assert "assumes" in gex.SIGN_CONVENTION.lower()
    assert "not observable" in gex.SIGN_CONVENTION.lower(), (
        "the convention must say that dealer positioning is not observable"
    )


def test_gex_scales_with_open_interest_and_spot():
    small = gex.contract_gex(contract("call", 100, gamma=0.02, oi=100), 100.0)
    large = gex.contract_gex(contract("call", 100, gamma=0.02, oi=200), 100.0)
    assert abs(large - 2 * small) < 1e-9, "open interest must scale linearly"

    near = gex.contract_gex(contract("call", 100, gamma=0.02), 100.0)
    far = gex.contract_gex(contract("call", 100, gamma=0.02), 200.0)
    assert far > near, "exposure rises with spot squared"


# --------------------------------------------------------------------------- #
# gamma flip
# --------------------------------------------------------------------------- #
def test_gamma_flip_is_interpolated_between_strikes():
    """The crossing between two consecutive strikes is interpolated, not rounded.

    The fixture has exactly one sign change, between 110 and 120, so the expected
    answer is unambiguous. A put below and a call above the body would create a
    second crossing near spot, which is a legitimate chain shape but makes the
    test unable to say which level it is asserting.
    """
    expiries = [expiry(contract("call", 100, gamma=0.02, oi=100),
                       contract("call", 110, gamma=0.02, oi=100),
                       contract("put", 120, gamma=0.02, oi=100))]
    flip = gex.gamma_flip(expiries, 100.0)
    assert flip is not None
    assert 110.0 < flip < 120.0, flip


def test_gamma_flip_takes_the_crossing_nearest_spot():
    """A chain can cross more than once; only the nearest level is actionable.

    Deep puts below and a deep put above create two crossings. The first by strike
    would be reported if the function returned early, which is why it collects
    them all and then chooses.
    """
    expiries = [expiry(contract("call", 100, gamma=0.02, oi=100),
                       contract("call", 110, gamma=0.02, oi=100),
                       contract("call", 120, gamma=0.02, oi=100),
                       contract("put", 130, gamma=0.02, oi=100),
                       contract("put", 90, gamma=0.02, oi=100))]
    flip = gex.gamma_flip(expiries, 125.0)
    assert flip is not None
    # Crossings sit near 95 and near 125; spot is 125, so the near one wins.
    assert abs(flip - 125.0) < 10.0, flip


def test_no_flip_is_reported_when_the_sign_never_changes():
    """Returning the nearest strike would invent a level that does not exist."""
    expiries = [expiry(contract("call", 100, gamma=0.02),
                       contract("call", 110, gamma=0.02),
                       contract("call", 120, gamma=0.02))]
    assert gex.gamma_flip(expiries, 100.0) is None


def test_gamma_flip_needs_enough_strikes():
    expiries = [expiry(contract("call", 100, gamma=0.02))]
    assert gex.gamma_flip(expiries, 100.0) is None


# --------------------------------------------------------------------------- #
# by-strike grouping
# --------------------------------------------------------------------------- #
def test_gex_by_strike_reports_both_sides():
    expiries = [expiry(contract("call", 100, gamma=0.02, oi=100),
                       contract("put", 100, gamma=0.01, oi=100),
                       contract("call", 120, gamma=0.05, oi=100))]
    rows = gex.gex_by_strike(expiries, 100.0)
    assert rows[0]["strike"] == 120.0, "largest absolute exposure first"
    by_strike = {r["strike"]: r for r in rows}
    assert by_strike[100.0]["call_gex"] > 0
    assert by_strike[100.0]["put_gex"] < 0
    for row in rows:
        assert abs(row["net_gex"] - (row["call_gex"] + row["put_gex"])) < 1e-6


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
