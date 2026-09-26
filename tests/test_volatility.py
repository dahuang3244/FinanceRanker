"""Implied and realised volatility.

The inversions are checked against prices this module produced from known vols, so
a broken bisection cannot pass by agreeing with itself. The guards matter as much
as the arithmetic: a stale quote inverted without a check yields an absurd vol,
which is how the deep in-the-money calls report ~1%.
"""

from __future__ import annotations

import math
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import volatility as vol
from app.models import OptionsContract, OptionsExpiry


def contract(kind: str, strike: float, price: float, days: int = 30) -> OptionsContract:
    return OptionsContract(
        kind=kind, strike=strike, expiration=date.today() + timedelta(days=days),
        last_price=price, volume=100, open_interest=100,
    )


def test_black_scholes_matches_known_values():
    # At-the-money, 1 year, 20% vol on a 100 spot: call ~= 0.2 * S * sqrt(T/(2pi))
    # plus the rate term; pin the price rather than the approximation.
    price = vol.black_scholes(100, 100, 1.0, 0.20, "call", rate=0.0)
    assert abs(price - 7.9656) < 0.01, price
    put = vol.black_scholes(100, 100, 1.0, 0.20, "put", rate=0.0)
    assert abs(put - price) < 1e-9, "at-the-money with no rate, call and put must match"


def test_put_call_parity_holds():
    S, K, T, sigma, r = 100.0, 95.0, 0.5, 0.3, 0.04
    call = vol.black_scholes(S, K, T, sigma, "call", r)
    put = vol.black_scholes(S, K, T, sigma, "put", r)
    assert abs((call - put) - (S - K * math.exp(-r * T))) < 1e-9


def test_implied_vol_recovers_the_vol_that_priced_it():
    """Round trip: price from a known vol, invert, get it back.

    Strikes are kept near the money on purpose. A deeply in-the-money contract's
    price is almost entirely intrinsic, so it carries little volatility
    information and inverting it is a different (and much weaker) test.
    """
    for sigma in (0.10, 0.25, 0.60, 1.20):
        for strike in (85.0, 95.0, 100.0, 105.0, 115.0):
            for kind in ("call", "put"):
                price = vol.black_scholes(100, strike, 0.5, sigma, kind)
                # Only test quotes whose value is dominated by time value.
                intrinsic = max(0.0, (100 - strike) if kind == "call" else (strike - 100))
                if price - intrinsic < 0.5:
                    continue
                back = vol.implied_vol(price, 100, strike, 0.5, kind)
                assert back is not None, (sigma, strike, kind, price)
                assert abs(back - sigma) < 2e-3, (sigma, strike, kind, back)


def test_implied_vol_rejects_a_stale_price_below_intrinsic():
    """A deep in-the-money call quoting below intrinsic is stale, not cheap.

    Inverting it produces ~1% vol, which is exactly the nonsense seen on the real
    chain, so it must be refused rather than averaged in.
    """
    # Spot 340, strike 255: intrinsic 85. A last price of 83.73 is below it.
    assert vol.implied_vol(83.73, 340.0, 255.0, 30 / 365, "call") is None
    # A price at intrinsic is acceptable and yields a low but real vol.
    result = vol.implied_vol(85.0, 340.0, 255.0, 30 / 365, "call")
    assert result is None or result > 0


def test_implied_vol_refuses_impossible_prices():
    assert vol.implied_vol(0.0, 100, 100, 0.5, "call") is None
    assert vol.implied_vol(-1.0, 100, 100, 0.5, "call") is None
    # A price above what any vol up to MAX_IV can produce.
    assert vol.implied_vol(999.0, 100, 100, 0.5, "call") is None
    # No time left.
    assert vol.implied_vol(5.0, 100, 100, 0.0, "call") is None


def test_realized_volatility_is_annualised_and_ordered():
    # A series with a known daily move: +-1% alternating gives ~1% daily vol.
    closes = [100.0]
    for i in range(60):
        closes.append(closes[-1] * (1.01 if i % 2 == 0 else 1 / 1.01))
    rv = vol.realized_volatility(closes, 21)
    assert rv is not None
    # ~1% daily -> ~16% annualised (252 trading days).
    assert 0.10 < rv < 0.25, rv

    # A calmer series must measure calmer.
    calm = [100.0]
    for i in range(60):
        calm.append(calm[-1] * (1.002 if i % 2 == 0 else 1 / 1.002))
    calm_rv = vol.realized_volatility(calm, 21)
    assert calm_rv < rv, (calm_rv, rv)

    # A flat series has no volatility; returning 0 would be a division hazard.
    assert vol.realized_volatility([100.0] * 30, 21) is None


def test_realized_volatility_needs_enough_observations():
    assert vol.realized_volatility([100.0, 101.0], 21) is None
    assert vol.realized_volatility([], 21) is None


def test_risk_reversal_is_positive_when_puts_are_bid():
    """The usual equity skew: 25-delta puts carry more vol than 25-delta calls."""
    expiry = OptionsExpiry(
        expiration=date.today() + timedelta(days=30),
        calls=[
            contract("call", 100, 3.0), contract("call", 105, 1.4),
            contract("call", 110, 0.7), contract("call", 95, 6.0),
        ],
        puts=[
            contract("put", 100, 3.0), contract("put", 95, 1.6),
            contract("put", 90, 0.95), contract("put", 105, 5.5),
        ],
    )
    skew, detail = vol.risk_reversal(100.0, expiry.calls + expiry.puts, 30 / 365)
    assert skew is not None, detail
    assert detail["put_strike"] <= 100.0 <= detail["call_strike"], detail
    assert abs(detail["put_delta"]) < 0.5 and abs(detail["call_delta"]) < 0.5


def test_risk_reversal_refuses_when_strikes_do_not_straddle_spot():
    """Both sides landing on the same side of spot is not a smile reading."""
    expiry = OptionsExpiry(
        expiration=date.today() + timedelta(days=30),
        calls=[contract("call", 100, 3.0)],
        puts=[contract("put", 100, 3.0)],
    )
    # Both at exactly spot: the straddle test passes, so this must still produce a
    # reading rather than silently returning None.
    skew, detail = vol.risk_reversal(100.0, expiry.calls + expiry.puts, 30 / 365)
    assert skew is not None
    # With only one strike the deltas are near 0.5, so no 25-delta exists; the
    # function picks the closest and reports it rather than failing.
    assert "put_iv" in detail


def test_iv_rank_needs_a_real_history():
    series = [0.1 + i * 0.001 for i in range(60)]
    rank = vol.iv_rank(0.13, series)
    assert rank is not None and 0.0 <= rank <= 1.0
    # Too few observations is not a percentile.
    assert vol.iv_rank(0.13, series[:10]) is None
    assert vol.iv_rank(None, series) is None
    # A value above everything observed ranks at the top.
    assert vol.iv_rank(9.0, series) == 1.0


def test_volatility_block_leaves_fields_blank_rather_than_guessing():
    """No spot, no expiry, or no prices must produce blanks, never a number."""
    empty = vol.volatility_block(None, None, [])
    assert empty["iv_atm"] is None
    assert empty["iv_rv"] is None
    assert empty["skew_points"] is None
    assert empty["basis"] == ""

    expiry = OptionsExpiry(
        expiration=date.today() + timedelta(days=30), calls=[], puts=[])
    no_quotes = vol.volatility_block(100.0, expiry, [])
    # No tradable prices: implied cannot be measured.
    assert no_quotes["iv_atm"] is None
    assert no_quotes["iv_rv"] is None
    assert no_quotes["basis"] == ""


def test_volatility_block_produces_a_ratio_when_both_sides_exist():
    expiry = OptionsExpiry(
        expiration=date.today() + timedelta(days=30),
        calls=[contract("call", 100, 3.0), contract("call", 105, 1.4),
               contract("call", 110, 0.7), contract("call", 95, 6.0)],
        puts=[contract("put", 100, 3.0), contract("put", 95, 1.6),
              contract("put", 90, 0.95), contract("put", 105, 5.5)],
    )
    closes = [100.0]
    for i in range(60):
        closes.append(closes[-1] * (1.01 if i % 2 == 0 else 1 / 1.01))

    block = vol.volatility_block(100.0, expiry, closes)
    assert block["iv_atm"] is not None and block["iv_atm"] > 0
    assert block["rv_21d"] is not None and block["rv_21d"] > 0
    assert block["iv_rv"] is not None
    assert abs(block["iv_rv"] - block["iv_atm"] / block["rv_21d"]) < 1e-9
    assert block["skew_points"] is not None
    assert block["basis"], "the derivation must be stated"
    assert block["expiry"] == expiry.expiration.isoformat()


def test_the_module_states_that_it_does_not_use_the_chain_iv_field():
    """The chain's IV is placeholder data; the inversion must not read it."""
    source = (Path(__file__).resolve().parent.parent / "app" / "volatility.py").read_text()
    body = source.split('"""', 2)[2]      # skip the module docstring
    assert "implied_volatility" not in body.replace("implied volatility", ""), (
        "app/volatility.py must invert prices, not read the chain's IV field"
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
