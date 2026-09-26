"""Implied and realised volatility, for the options threshold tab.

Two figures the free chain does *not* hand over, recovered from data it does:

* **Implied volatility** is inverted from traded option prices with a
  Black-Scholes bisection, not read from the chain's `impliedVolatility` field.
  That field is placeholder data — most values are clipped to 1e-5 or exactly
  0.5 and quantised to sixteenths — so it cannot carry a skew. An option's last
  price can, albeit noisily.
* **Realised volatility** is computed from the price history, which is reliable.

Everything here is explicit about its assumptions. The inversions use
last-traded prices, so a stale quote produces a stale vol, and every result
carries the guard that rejected nonsense rather than silently averaging it in.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timezone

log = logging.getLogger(__name__)

RISK_FREE = 0.04          # a flat rate: the comparison is between two vols, not a price
# A traded vol below this is not a cheap option, it is a price that has gone
# stale relative to spot: the deep in-the-money calls on a real chain invert to
# ~1%, which is not a volatility anyone is quoting. The floor doubles as the
# lower bound of the bisection, so such quotes are refused rather than averaged
# into an at-the-money reading.
MIN_IV, MAX_IV = 0.05, 5.0
TRADING_DAYS = 252
# How far the two at-the-money legs may disagree before the mean is refused.
# Both invert the same point on the smile, so a gap this wide means one of the two
# last-traded prices is stale rather than that the market is skewed.
MAX_LEG_GAP = 0.03


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes(spot: float, strike: float, years: float, vol: float,
                  kind: str, rate: float = RISK_FREE) -> float:
    """European option value. Only used to invert a price back to a vol."""
    if years <= 0 or vol <= 0:
        intrinsic = (spot - strike) if kind == "call" else (strike - spot)
        return max(0.0, intrinsic)
    sqrt_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + vol * vol / 2) * years) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    if kind == "call":
        return spot * _norm_cdf(d1) - strike * math.exp(-rate * years) * _norm_cdf(d2)
    return strike * math.exp(-rate * years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def delta(spot: float, strike: float, years: float, vol: float,
          kind: str, rate: float = RISK_FREE) -> float:
    if years <= 0 or vol <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + (rate + vol * vol / 2) * years) / (vol * math.sqrt(years))
    return _norm_cdf(d1) if kind == "call" else _norm_cdf(d1) - 1.0


def implied_vol(price: float, spot: float, strike: float, years: float,
                kind: str, rate: float = RISK_FREE,
                tol: float = 1e-8) -> float | None:
    """Invert a traded price to a volatility, or None if it cannot be trusted.

    Rejects a price below intrinsic value: that is a stale quote, not a cheap
    option, and inverting it produces an absurd vol. The floor of the search at
    `MIN_IV` catches the rest — a quote stale enough to imply a 1% vol is not a
    measurement, so it is refused rather than returned.
    """
    if price <= 0 or years <= 0 or spot <= 0 or strike <= 0:
        return None
    intrinsic = max(0.0, (spot - strike) if kind == "call" else (strike - spot))
    # A price below intrinsic is stale data, not a cheap option.
    if price < intrinsic - max(0.02, spot * 0.005):
        return None
    if black_scholes(spot, strike, years, MAX_IV, kind, rate) < price:
        return None
    # A quote whose only explanation is a vol at or below the floor carries no
    # information — it is either stale or priced at pure intrinsic — so it is
    # refused rather than returned as a "very low vol" reading. This is what keeps
    # the deep in-the-money wings, which invert to ~1% on a real chain, out of the
    # smile and out of the at-the-money average.
    if black_scholes(spot, strike, years, MIN_IV, kind, rate) >= price:
        return None

    lo, hi = MIN_IV, MAX_IV
    for _ in range(100):
        mid = (lo + hi) / 2
        if black_scholes(spot, strike, years, mid, kind, rate) < price:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2


def realized_volatility(closes: list[float], window: int = 21) -> float | None:
    """Annualised standard deviation of daily log returns over `window` days.

    Uses the most recent `window` returns. Needs at least 5 observations, below
    which the estimate is noise rather than a measurement.
    """
    series = [c for c in closes if c and c > 0]
    if len(series) < 6:
        return None
    returns = [
        math.log(series[i] / series[i - 1])
        for i in range(1, len(series))
        if series[i - 1] > 0 and series[i] > 0
    ]
    if len(returns) < 5:
        return None
    sample = returns[-window:] if len(returns) > window else returns
    mean = sum(sample) / len(sample)
    variance = sum((r - mean) ** 2 for r in sample) / (len(sample) - 1)
    if variance <= 0:
        return None
    return math.sqrt(variance) * math.sqrt(TRADING_DAYS)


def realized_vol_series(closes: list[float], window: int = 21) -> list[float]:
    """Rolling realised vol, so a current reading can be placed in its own range."""
    series = [c for c in closes if c and c > 0]
    out: list[float] = []
    for end in range(window + 1, len(series) + 1):
        window_closes = series[max(0, end - window - 1):end]
        value = realized_volatility(window_closes, window)
        if value is not None:
            out.append(value)
    return out


def atm_implied_vol(spot: float, contracts, years: float) -> tuple[float | None, float | None]:
    """Implied vol of the at-the-money straddle's legs.

    Returns `(call_iv, put_iv)` for the strikes nearest spot. Averaging the two
    sides is deliberate at the caller: they are two quotes for the same point on
    the smile, so together they are more robust than either alone.
    """
    if not spot or years <= 0:
        return None, None

    def nearest(kind: str):
        rows = [c for c in contracts if c.kind == kind and c.strike and c.last_price]
        if not rows:
            return None
        return min(rows, key=lambda c: abs(c.strike - spot))

    call, put = nearest("call"), nearest("put")
    call_iv = implied_vol(call.last_price, spot, call.strike, years, "call") if call else None
    put_iv = implied_vol(put.last_price, spot, put.strike, years, "put") if put else None
    return call_iv, put_iv


def _skew_confidence(detail: dict, used: int, priced: int, days: float) -> dict:
    """How much weight the skew reading deserves.

    On a chain where most contracts have not traded, an inverted vol comes from a
    stale last price, and skew measured that way can flip sign between refreshes.
    This compares the liquidity of the strikes actually used against the chain as
    a whole and the contract's time to expiry, and reports a low-confidence
    reading rather than presenting it as a measurement.
    """
    if not detail:
        return {"level": "none", "reason": "no usable contracts"}
    # Trades on the two strikes the reading rests on. A risk reversal built on two
    # untraded contracts is a coincidence, not a smile.
    put_volume = detail.get("put_volume") or 0
    call_volume = detail.get("call_volume") or 0
    if days < 5:
        return {"level": "low",
                "reason": f"only {days:.0f} days to expiry, where the smile is unstable"}
    if put_volume == 0 or call_volume == 0:
        return {"level": "low",
                "reason": "one leg of the risk reversal has not traded today"}
    if used < 20 or (priced and used / priced < 0.4):
        return {"level": "low",
                "reason": f"only {used} of {priced} contracts could be priced"}
    return {"level": "ok", "reason": ""}


def risk_reversal(spot: float, contracts, years: float,
                  target_delta: float = 0.25) -> tuple[float | None, dict]:
    """25-delta put IV minus 25-delta call IV, in vol points.

    The standard risk reversal: positive means puts are bid over calls, which is
    the usual equity skew. Contract IVs are inverted from last prices and the
    strike closest to the target delta on each side is used.

    Returns `(skew_in_vol_points, detail)`; the detail is kept so a reader can see
    which strikes produced the number — and how liquid they are — rather than
    being asked to trust it.
    """
    if not spot or years <= 0:
        return None, {}
    candidates = []
    for contract in contracts:
        if not contract.strike or not contract.last_price:
            continue
        iv = implied_vol(contract.last_price, spot, contract.strike, years, contract.kind)
        if iv is None:
            continue
        d = delta(spot, contract.strike, years, iv, contract.kind)
        candidates.append({
            "kind": contract.kind, "strike": contract.strike, "iv": iv,
            "delta": d, "error": abs(abs(d) - target_delta),
            "volume": contract.volume or 0,
        })
    if not candidates:
        return None, {}

    chosen = {}
    for kind in ("call", "put"):
        side = sorted([c for c in candidates if c["kind"] == kind],
                      key=lambda c: c["error"])
        if not side:
            return None, {}
        chosen[kind] = side[0]

    # The two strikes must straddle spot, or the spread is not a smile reading.
    if not (chosen["put"]["strike"] <= spot <= chosen["call"]["strike"]):
        return None, {"rejected": "the 25-delta strikes do not straddle spot"}

    priced = sum(1 for c in contracts if c.last_price)
    skew_points = (chosen["put"]["iv"] - chosen["call"]["iv"]) * 100.0
    detail = {
        "put_strike": chosen["put"]["strike"],
        "put_iv": chosen["put"]["iv"],
        "put_delta": chosen["put"]["delta"],
        "put_volume": chosen["put"]["volume"],
        "call_strike": chosen["call"]["strike"],
        "call_iv": chosen["call"]["iv"],
        "call_delta": chosen["call"]["delta"],
        "call_volume": chosen["call"]["volume"],
        "contracts_used": len(candidates),
        "contracts_priced": priced,
        "skew_points": skew_points,
    }
    detail["confidence"] = _skew_confidence(
        detail, len(candidates), priced, years * 365)
    return skew_points, detail


def iv_rank(current: float | None, series: list[float]) -> float | None:
    """Where today's reading sits in its own past, as a 0..1 percentile.

    Answers "is this high for this name", which a bare ratio cannot. Needs a
    reasonable history; below 30 observations the percentile is not meaningful.
    """
    if current is None:
        return None
    values = [v for v in series if v is not None]
    if len(values) < 30:
        return None
    below = sum(1 for v in values if v <= current)
    return below / len(values)


def volatility_block(spot: float | None, expiry, closes: list[float]) -> dict:
    """Assemble the volatility figures for one snapshot.

    `expiry` is an `OptionsExpiry`. The two sides are deliberately different in
    kind and the result says so: implied vol is forward-looking and inverted from
    prices, realised vol is backward-looking and measured from returns. Their
    ratio is the standard "is premium rich or cheap" comparison.
    """
    out: dict = {
        "iv_atm": None, "iv_call": None, "iv_put": None,
        "rv_21d": None, "iv_rv": None, "iv_rank": None,
        "skew_points": None, "skew": {}, "expiry": None,
        "basis": "", "leg_gap": None, "iv_note": "",
        "expiry_days": None,
    }
    if not spot or expiry is None:
        return out

    rv = realized_volatility(closes, 21)
    out["rv_21d"] = rv

    expiry_date = expiry.expiration
    now = datetime.now(timezone.utc)
    expiry_dt = datetime.combine(expiry_date, datetime.min.time(), tzinfo=timezone.utc)
    raw_days = (expiry_dt - now).total_seconds() / 86400
    out["expiry_days"] = round(raw_days, 1)
    # A floor of one day keeps a same-day expiry from dividing by zero.
    years = max(raw_days / 365, 1 / 365)
    contracts = expiry.calls + expiry.puts

    call_iv, put_iv = atm_implied_vol(spot, contracts, years)
    out["iv_call"], out["iv_put"] = call_iv, put_iv
    # The two legs quote the same point on the smile, so they should agree. When
    # they do not, one of the two last-traded prices is stale, and averaging blends
    # a good quote with a bad one: LLY's legs disagreed by 11 vol points (0.307
    # against 0.416) and their mean drove IV/RV to 1.87, past the "premium is rich"
    # trigger. A disagreement that wide is reported rather than averaged.
    leg_gap = None
    if call_iv is not None and put_iv is not None:
        leg_gap = abs(call_iv - put_iv)
        out["leg_gap"] = leg_gap

    legs = [v for v in (call_iv, put_iv) if v is not None]
    legs_disagree = leg_gap is not None and leg_gap > MAX_LEG_GAP
    if legs:
        if legs_disagree:
            out["iv_atm"] = min(call_iv, put_iv) if None not in (call_iv, put_iv) else legs[0]
            out["iv_note"] = (
                f"the at-the-money legs disagree by {leg_gap * 100:.1f} vol points, so one "
                f"quote is stale; the lower leg is shown and the ratio is withheld"
            )
        else:
            out["iv_atm"] = sum(legs) / len(legs)
        out["expiry"] = expiry_date.isoformat()
        out["basis"] = (
            f"ATM implied from the {expiry_date.isoformat()} straddle legs, inverted "
            f"from last-traded prices; realised from 21 daily log returns"
        )

    skew_points, skew_detail = risk_reversal(spot, contracts, years)
    if skew_points is not None:
        out["skew_points"] = skew_points
        out["skew"] = skew_detail

    # The ratio multiplies the implied leg by nothing, so a stale leg propagates
    # straight into IV/RV and the threshold reading built on it. When the legs
    # cannot agree there is no trustworthy implied figure, and a withheld ratio is
    # more useful than one assembled from a quote known to be wrong.
    if out["iv_atm"] is not None and rv and not legs_disagree:
        out["iv_rv"] = out["iv_atm"] / rv

    # IV rank: today's ratio placed against what it would have been at each past
    # realised-vol reading, holding today's implied fixed. The implied side has no
    # stored history in a free source, so this is the honest version of the
    # measure rather than a claim about implied vol's own range.
    series = realized_vol_series(closes, 21)
    implied = out["iv_atm"]
    if out["iv_rv"] is not None and implied and series:
        ratios = [implied / v for v in series if v]
        out["iv_rank"] = iv_rank(out["iv_rv"], ratios)
    return out
