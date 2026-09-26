"""Dealer gamma exposure, from reported Greeks.

GEX is the change in dealers' delta-hedge requirement per 1% move in the
underlying. It was previously reported as unmeasurable by this app, on the
grounds that a free chain gives neither Greeks nor dealer positions. That was
half right: the *sign* of dealers' positioning is still not observable, so the
standard convention is applied and labelled rather than hidden —

    dealers are assumed long calls and short puts

which makes call gamma add to exposure and put gamma subtract from it. The
magnitude uses gamma as CBOE reports it, open interest, and the contract
multiplier.

Because the sign is an assumption, every figure carries `basis` saying so, and
the number is described as a *convention* rather than a measurement of what
dealers hold.
"""

from __future__ import annotations

import logging
from datetime import date

from app.models import OptionsContract, OptionsExpiry

log = logging.getLogger(__name__)

CONTRACT_MULTIPLIER = 100
# The sign convention, stated once so the UI and the note cannot disagree.
SIGN_CONVENTION = (
    "Assumes dealers are long calls and short puts, the standard convention; "
    "actual dealer positioning is not observable from public data"
)


def is_skinny(value: float | None) -> bool:
    """A Greek the model did not produce, rather than a measured zero.

    The feed writes 0.0 for deep strikes where the greek is not meaningful.
    Treating that as a measurement would put a row of zeros into the sum and,
    worse, let a strike with no model price look like a real observation.
    """
    return value is None or value == 0


def contract_gex(contract: OptionsContract, spot: float | None) -> float | None:
    """One contract's contribution to dealer gamma, in currency per 1% move.

    gamma is the change in delta per unit move in the underlying. Multiplying by
    open interest, the 100-share multiplier, spot squared and 0.01 gives the
    dollar delta that must be re-hedged for a 1% move.
    """
    if not spot or is_skinny(contract.gamma):
        return None
    if not contract.open_interest:
        return None
    sign = 1.0 if contract.kind == "call" else -1.0
    return (sign * (contract.gamma or 0.0) * (contract.open_interest or 0.0)
            * CONTRACT_MULTIPLIER * spot * spot * 0.01)


def net_gex(expiries: list[OptionsExpiry], spot: float | None) -> float | None:
    """Total dealer gamma across the given expiries, or None if unmeasurable."""
    total = 0.0
    measured = 0
    for expiry in expiries:
        for contract in list(expiry.calls) + list(expiry.puts):
            contribution = contract_gex(contract, spot)
            if contribution is None:
                continue
            total += contribution
            measured += 1
    return total if measured else None


def gex_by_strike(expiries: list[OptionsExpiry], spot: float | None,
                  limit: int = 8) -> list[dict]:
    """Gamma exposure grouped by strike, largest absolute first.

    The strikes where hedging is concentrated are the ones that matter; a strike
    with a tiny net but offsetting call and put gamma is not the same as one with
    none, so both sides are reported.
    """
    if not spot:
        return []
    by_strike: dict[float, dict[str, float]] = {}
    for expiry in expiries:
        for contract in list(expiry.calls) + list(expiry.puts):
            contribution = contract_gex(contract, spot)
            if contribution is None:
                continue
            bucket = by_strike.setdefault(contract.strike, {"call": 0.0, "put": 0.0})
            bucket["call" if contract.kind == "call" else "put"] += contribution
    rows = [
        {
            "strike": strike,
            "call_gex": values["call"],
            "put_gex": values["put"],
            "net_gex": values["call"] + values["put"],
            "distance": (strike / spot - 1.0),
        }
        for strike, values in by_strike.items()
    ]
    rows.sort(key=lambda r: abs(r["net_gex"]), reverse=True)
    return rows[:limit]


def gamma_flip(expiries: list[OptionsExpiry], spot: float | None) -> float | None:
    """The strike where net gamma changes sign, choosing the one nearest spot.

    Above the flip, hedging dampens moves; below it, hedging amplifies them. A
    chain can cross more than once — deep puts are negative, the body positive,
    far calls negative again — so the *first* crossing by strike is not the
    meaningful one. Returning it would have reported a level 38% below spot for a
    chain whose relevant crossing sat just under the price. The nearest crossing
    to spot is the level a reader can act on.

    Only meaningful when the sign actually changes: a chain whose gamma is
    positive everywhere has no flip, and returning the nearest strike anyway would
    invent a level that does not exist.
    """
    if not spot:
        return None
    strikes = sorted({c.strike for e in expiries for c in (e.calls + e.puts)
                      if c.strike and c.gamma})
    if len(strikes) < 3:
        return None

    points: list[tuple[float, float]] = []
    for strike in strikes:
        contribution = 0.0
        for expiry in expiries:
            for contract in list(expiry.calls) + list(expiry.puts):
                if contract.strike != strike:
                    continue
                value = contract_gex(contract, spot)
                if value is not None:
                    contribution += value
        points.append((strike, contribution))
    points.sort()

    crossings: list[float] = []
    for (k0, v0), (k1, v1) in zip(points, points[1:]):
        if v0 == 0:
            continue
        if (v0 < 0) != (v1 < 0):
            span = abs(v0) + abs(v1)
            if span == 0:
                continue
            # Linear interpolation of the crossing, so the level is not rounded to
            # whatever strike happened to be listed.
            crossings.append(k0 + (k1 - k0) * (abs(v0) / span))
    if not crossings:
        return None
    return min(crossings, key=lambda level: abs(level - spot))
