"""Options-chain analytics: positioning from volume and open interest, plus volatility.

The chain's own `impliedVolatility` field is **not used**. It is largely
placeholder data — clipped to 1e-5 or exactly 0.5 and quantised to sixteenths —
with bid and ask reported as zero. Implied volatility is instead inverted from
each contract's last-traded price in `app.volatility`, which is noisier than a
real feed but is a measurement rather than a placeholder.

Everything else is a positioning observation: put/call ratios on volume and open
interest, max pain, where strikes cluster, and which contracts traded against
their book. Open interest says where contracts exist, never who holds them or
why, and the interpretation says so.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from app.models import (
    OptionsChain,
    OptionsContract,
    OptionsExpiry,
    OptionsExpirySummary,
    OptionsSnapshot,
    OptionsVerdict,
    OptionsVolatility,
)
from app.providers.yahoo_options import (
    MAX_EXPIRIES,
    NEAR_MONTH_DAYS,
)
from app.providers.yahoo_options import fetch_chain as fetch_yahoo_chain
from app.providers.cboe_options import fetch_chain as fetch_cboe_chain

log = logging.getLogger(__name__)

# The distance from spot that separates a directional bet from a defensive one.
# A strike far above spot is not a bet on a small rise; it is either a lottery
# ticket or a covered-call leg, and calling it "bullish" would overstate what the
# contract says.
DIRECTIONAL_BAND = 0.05


def _direction(kind: str, strike: float, spot: float) -> str:
    """Classify a contract by how far its strike sits from spot.

    The four buckets mirror how an options desk reads a chain: a call struck near
    spot is a directional bet on a rise, a call struck well above it is a
    lottery ticket or an overwrite, and the put side splits the same way between
    a near-money hedge and a crash hedge.
    """
    if not spot:
        return "other"
    distance = strike / spot - 1.0
    if kind == "call":
        if distance <= -DIRECTIONAL_BAND:
            return "deep_call"          # already in the money, or a bullish overwrite
        if distance < DIRECTIONAL_BAND:
            return "directional_call"   # near the money: a bet on a rise
        return "upside_call"            # far above spot: cheap upside or a cap
    if distance >= DIRECTIONAL_BAND:
        return "deep_put"
    if distance > -DIRECTIONAL_BAND:
        return "directional_put"        # near the money: a bet on a fall, or a hedge
    return "downside_put"               # far below spot: crash protection


def _ratio(call_value: float, put_value: float) -> float | None:
    if not call_value:
        return None
    return put_value / call_value


def _totals(contracts: list[OptionsContract]) -> tuple[float, float]:
    volume = sum(c.volume or 0 for c in contracts)
    open_interest = sum(c.open_interest or 0 for c in contracts)
    return volume, open_interest


def _max_pain(expiry: OptionsExpiry) -> float | None:
    """The strike at which expiring open interest costs holders the least.

    Computed the standard way: for each candidate strike, sum the intrinsic
    value of every call and put that would expire in the money, weighted by open
    interest, and take the minimum. Only the total per strike is used, so it says
    nothing about who is on either side.
    """
    strikes = sorted({
        c.strike for c in expiry.calls + expiry.puts
        if c.strike and (c.open_interest or 0) > 0
    })
    if len(strikes) < 3:
        return None
    best_strike, best_cost = None, None
    for candidate in strikes:
        cost = 0.0
        for call in expiry.calls:
            if (call.open_interest or 0) > 0 and (call.strike or 0) < candidate:
                cost += (candidate - call.strike) * call.open_interest
        for put in expiry.puts:
            if (put.open_interest or 0) > 0 and (put.strike or 0) > candidate:
                cost += (put.strike - candidate) * put.open_interest
        if best_cost is None or cost < best_cost:
            best_strike, best_cost = candidate, cost
    return best_strike


def _concentration(expiry: OptionsExpiry, spot: float | None, limit: int = 6) -> list[dict]:
    """Strikes carrying the most open interest.

    Read as zones where price may linger: a strike with heavy open interest is
    where a lot of contracts were written, not where the price is going.
    """
    by_strike: dict[float, dict[str, float]] = {}
    for contract in expiry.calls + expiry.puts:
        strike = contract.strike
        if not strike:
            continue
        bucket = by_strike.setdefault(strike, {"call": 0.0, "put": 0.0})
        bucket["call" if contract.kind == "call" else "put"] += contract.open_interest or 0
    rows = [
        {
            "strike": strike,
            "call_oi": int(values["call"]),
            "put_oi": int(values["put"]),
            "total_oi": int(values["call"] + values["put"]),
            "distance": (strike / spot - 1.0) if spot else None,
        }
        for strike, values in by_strike.items()
    ]
    rows.sort(key=lambda r: r["total_oi"], reverse=True)
    return rows[:limit]


def _unusual(expiry: OptionsExpiry, limit: int = 12) -> list[OptionsContract]:
    """Contracts traded heavily against their existing open interest.

    A high volume-to-open-interest ratio means positions are being opened rather
    than closed. Two conditions are required, and the second is not optional:

    * volume of at least 100 contracts, so a handful of trades is not a signal;
    * **open interest above zero**, because that book is the ratio's denominator.
      Dividing by a floor of 1 turned a zero into the raw volume: NVDA's 230 call
      reported "310,776x its open interest" on a strike with no open interest at
      all, and on several expiries every candidate was such a row. A contract with
      no book yet is a new position, worth showing as that rather than as an
      enormous ratio that means nothing.
    """
    scored: list[tuple[float, OptionsContract]] = []
    for contract in expiry.calls + expiry.puts:
        volume = contract.volume or 0
        open_interest = contract.open_interest or 0
        if volume < 100 or open_interest <= 0:
            continue
        ratio = volume / open_interest
        if ratio >= 1.0:
            scored.append((ratio, contract))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [c for _, c in scored[:limit]]


def _new_positions(expiry: OptionsExpiry, limit: int = 6) -> list[OptionsContract]:
    """Traded contracts with no open interest yet, ranked by volume.

    The rows the ratio above deliberately excludes. On a same-day or next-day
    expiry these are most of the chain, so they are reported separately rather
    than either dropped silently or given a meaningless ratio.
    """
    rows = [
        c for c in expiry.calls + expiry.puts
        if (c.volume or 0) >= 100 and (c.open_interest or 0) <= 0
    ]
    rows.sort(key=lambda c: c.volume or 0, reverse=True)
    return rows[:limit]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [c for _, c in scored[:limit]]


def _straddle_move(expiry: OptionsExpiry, spot: float | None, target: float | None) -> float | None:
    """Cost of the at-the-money straddle as a fraction of spot.

    This is the market's own pricing of the move to that expiry, assembled from
    the traded prices of the nearest call and put. It is a *priced* magnitude,
    not an implied volatility: recovering IV from option prices needs a pricing
    model and both bid and ask, and neither is available here.
    """
    if not spot or not target:
        return None
    calls = [c for c in expiry.calls if c.last_price]
    puts = [c for c in expiry.puts if c.last_price]
    if not calls or not puts:
        return None
    call = min(calls, key=lambda c: abs((c.strike or 0) - target))
    put = min(puts, key=lambda c: abs((c.strike or 0) - target))
    if not call.last_price or not put.last_price:
        return None
    return (call.last_price + put.last_price) / spot


def _expiry_summary(expiry: OptionsExpiry, spot: float | None) -> dict:
    call_volume, call_oi = _totals(expiry.calls)
    put_volume, put_oi = _totals(expiry.puts)
    buckets: dict[str, float] = {}
    for contract in expiry.calls + expiry.puts:
        if not contract.strike:
            continue
        key = _direction(contract.kind, contract.strike, spot or 0)
        buckets[key] = buckets.get(key, 0.0) + (contract.open_interest or 0)
    return {
        "expiration": expiry.expiration.isoformat(),
        "days": (expiry.expiration - date.today()).days,
        "call_volume": int(call_volume),
        "put_volume": int(put_volume),
        "call_oi": int(call_oi),
        "put_oi": int(put_oi),
        "volume_pcr": _ratio(call_volume, put_volume),
        "oi_pcr": _ratio(call_oi, put_oi),
        "max_pain": _max_pain(expiry),
        "straddle_move": _straddle_move(expiry, spot, spot),
        "buckets": {k: int(v) for k, v in sorted(buckets.items(), key=lambda kv: -kv[1])},
    }


def _premium_side(contracts: list[OptionsContract]) -> float:
    """Notional premium traded on one side, in currency.

    Each contract represents 100 shares, so the value is
    `last price x volume x 100`. Without the multiplier this was the number of
    contracts, not dollars, and the screen understated the flow roughly 100-fold:
    a $0.31 option over 181,730 contracts is about $5.6M, not $305,758.

    `last` is used rather than a bid/ask mid because the free chain reports zero
    for both, so no better price is available and none is implied.
    """
    return sum((c.last_price or 0) * (c.volume or 0) * 100 for c in contracts)


def _premium_totals(expiry: OptionsExpiry) -> dict[str, float]:
    """Notional premium traded per side, so the flow's size is visible rather than
    only its ratio."""
    return {
        "call_premium": _premium_side(expiry.calls),
        "put_premium": _premium_side(expiry.puts),
    }


def _rescale(value: float | None, low: float, high: float) -> float | None:
    """Map a quantity onto 0..1 against a stated band, clamped."""
    if value is None or high <= low:
        return None
    return max(0.0, min(1.0, (value - low) / (high - low)))


def _to_five(ratio: float) -> int:
    """0..1 to a 1..5 score, rounded."""
    return max(1, min(5, int(round(ratio * 4)) + 1))


def _chase_safety(volume_pcr: float | None, oi_pcr: float | None,
                  iv_rv: float | None = None) -> int | None:
    """How safely a call can be chased, on 1..5.

    Three independent inputs: two about positioning and one about price. Keeping
    the price term is what stops this being a mirror image of `_put_value` — an
    earlier version built both scores from the same two put/call ratios, and they
    moved together as a result.

    * **Crowding** — how call-heavy the book already is. A *low* put/call means
      upside contracts dominate, the trade is popular and a buyer pays up for it,
      so a call-heavy book scores **worse**. The first version of this function
      had that sign inverted.
    * **Flow** — whether today is adding to the crowding. When today's put/call
      sits below the book's, buyers are piling into the crowded side.
    * **Price** — whether the volatility being paid for is rich relative to what
      the stock has actually been realising (`iv30 / rv_21d`). Implied below
      realised means the move is not yet priced; implied far above means the buyer
      pays for it. Omitted when unmeasurable, so the score then rests on
      positioning alone rather than assuming average pricing.
    """
    if volume_pcr is None or oi_pcr is None:
        return None
    # A call-heavy book (low put/call) is crowded, so it scores low.
    crowding = _rescale(oi_pcr, 0.55, 1.30) or 0.0
    # A positive gap means today is more defensive than the book: not piling in.
    gap = volume_pcr - oi_pcr
    flow = _rescale(gap, -0.40, 0.20)
    if flow is None:
        flow = 0.5
    if iv_rv is None or iv_rv <= 0:
        combined = (crowding + flow) / 2.0
    else:
        # Cheaper implied volatility makes chasing more defensible. The band starts
        # below 1.0 because a ratio under 1 (implied below realised) is the case
        # that most favours a buyer, and starting it at the neutral point would
        # clip every such name to the same score.
        price = 1.0 - (_rescale(iv_rv, 0.70, 1.70) or 0.0)
        combined = (crowding + flow + price) / 3.0
    # Squeezed toward the middle, because this is a heuristic: the extremes stay
    # reachable but the scale does not pretend to four-decimal confidence. The
    # band is wide enough that both 1 and 5 occur, or the score would carry less
    # information than the ratios it came from.
    return _to_five(0.04 + 0.92 * combined)


def _put_value(volume_pcr: float | None, oi_pcr: float | None,
               iv_rv: float | None = None) -> int | None:
    """How good value protective puts are, on 1..5.

    A call-heavy book makes protection cheap; a put-heavy one makes it expensive.
    Cost matters too, but it cuts the *opposite* way to chasing: the price of
    protection is the implied volatility itself, so implied well above realised is
    what makes puts expensive, while implied below realised makes them cheap.

    That sign flip is what keeps this score from tracking `_chase_safety` exactly.
    Built from the two put/call ratios alone, as an earlier version was, it
    returned 4 or 5 for every name in the pool — no discrimination at all.
    """
    if volume_pcr is None or oi_pcr is None:
        return None
    crowding = 1.0 - (_rescale(oi_pcr, 0.70, 1.60) or 0.0)
    gap = volume_pcr - oi_pcr
    flow = 1.0 - (_rescale(gap, 0.0, 0.40) or 0.0)
    if iv_rv is None or iv_rv <= 0:
        combined = (crowding + flow) / 2.0
    else:
        # Expensive protection is poor value; the same band as chasing, with the
        # sign reversed, which is what keeps the two scores apart.
        price = _rescale(iv_rv, 0.70, 1.70) or 0.0
        combined = (crowding + flow + price) / 3.0
    return _to_five(0.04 + 0.92 * combined)


def _iv_rv(snapshot: OptionsSnapshot) -> float | None:
    """Implied over realised volatility, read off the snapshot's volatility block.

    Read through a helper rather than directly so the volatility block can be
    absent in tests and on the Yahoo fallback without the verdict raising.
    """
    block = getattr(snapshot, "volatility", None)
    value = getattr(block, "iv_rv", None) if block else None
    return value if value and value > 0 else None


def _catalyst_note(verdict: OptionsVerdict, snapshot: OptionsSnapshot) -> None:
    """Name the next dated event, and warn when it falls inside the expiry.

    An earnings date inside the option's life is the most important thing about a
    short-dated position, and it is not visible from volume and open interest at
    all — so it is stated rather than left for the reader to remember.
    """
    next_earnings = getattr(snapshot, "next_earnings", None)
    if not next_earnings:
        return
    verdict.next_earnings = next_earnings
    try:
        when = date.fromisoformat(str(next_earnings)[:10])
    except (ValueError, TypeError):
        return
    verdict.days_to_earnings = (when - date.today()).days
    expiry_days = (getattr(snapshot.volatility, "expiry_days", None)
                   if snapshot.volatility else None)
    if expiry_days is not None and 0 <= verdict.days_to_earnings <= expiry_days:
        verdict.earnings_in_window = True


def _build_verdict(snapshot: OptionsSnapshot) -> OptionsVerdict:
    """The plain-language judgement the analysis tab leads with.

    Every field is derived from volume, open interest and the reported Greeks. A
    volatility reading is included only when the source publishes one: the CBOE
    chain reports `iv30` and per-contract Greeks, so those are used, while the
    Yahoo fallback reports placeholder IV and no Greeks, and on that source the
    volatility and gamma figures are omitted rather than estimated.
    """
    totals = snapshot.totals
    volume_pcr = totals.get("volume_pcr")
    oi_pcr = totals.get("oi_pcr")
    verdict = OptionsVerdict()

    # Where today's flow sits. The label fields are English fallbacks for direct
    # API consumers; the UI names the reading from `stance`/`lean_key` so it
    # follows the selected language.
    if volume_pcr is not None:
        if volume_pcr >= 1.0:
            verdict.stance = "defensive"
            verdict.stance_label = "Defensive demand is rising"
            verdict.lean_key = "defensive"
            verdict.lean = "careful about chasing longs"
        elif volume_pcr <= 0.7:
            verdict.stance = "bullish"
            verdict.stance_label = "Upside chasing is crowded"
            verdict.lean_key = "bullish"
            verdict.lean = "careful about paying up for protection"
        else:
            verdict.stance = "balanced"
            verdict.stance_label = "Flow is roughly balanced"
            verdict.lean_key = "balanced"
            verdict.lean = "no clear direction; go by the book"

    # Is today's tilt new, or already reflected in the book?
    if volume_pcr is not None and oi_pcr is not None:
        gap = volume_pcr - oi_pcr
        if gap > 0.15:
            verdict.novelty = "new_defensive"
            verdict.novelty_label = "The defensive tilt is new today"
        elif gap < -0.15:
            verdict.novelty = "new_bullish"
            verdict.novelty_label = "The bullish tilt is new today"
        else:
            verdict.novelty = "aligned"
            verdict.novelty_label = "Flow and book agree"
        verdict.flow_gap = gap

    # The two question-boxes. They take different signs on the price of
    # volatility, so they do not simply mirror each other: cheap implied vol
    # favours chasing calls, expensive implied vol is what makes puts poor value.
    verdict.chase_safety = _chase_safety(volume_pcr, oi_pcr, _iv_rv(snapshot))
    verdict.put_value = _put_value(volume_pcr, oi_pcr, _iv_rv(snapshot))

    if snapshot.max_pain and snapshot.spot:
        distance = abs(snapshot.max_pain / snapshot.spot - 1.0)
        front_days = None
        if snapshot.summaries:
            front_days = snapshot.summaries[0].days
        # Max pain only has time to act before expiry. On an expiring session there
        # is no time left, so "worth waiting" is not a meaningful answer.
        if front_days is not None and front_days <= 0:
            verdict.wait = "expired"
        else:
            verdict.wait = "yes" if distance <= 0.03 else "no"
        verdict.max_pain_distance = snapshot.max_pain / snapshot.spot - 1.0

    _catalyst_note(verdict, snapshot)
    return verdict


def _reference_expiry(expiries: list[OptionsExpiry], target_days: int = 35) -> OptionsExpiry | None:
    """The expiry closest to `target_days` out, used for every volatility figure.

    Two reasons for a month rather than the front week:

    * a one-day straddle carries almost no information, and inverting it is
      dominated by rounding in the last-traded price;
    * the skew in particular needs depth. Measured across expiries, a one-day
      reading flips sign between refresh — on AAPL the 6-day skew read -0.32 and
      the 17-day +1.23 on the same chain. A monthly expiry is where the smile is
      stable enough to be worth reporting, and it is the conventional horizon for
      both IV/RV and a 25-delta risk reversal.

    The target is in calendar days, so the nearest listed monthly expiry is used.
    """
    if not expiries:
        return None
    return min(expiries, key=lambda e: abs((e.expiration - date.today()).days - target_days))


def build_options_snapshot(
    ticker: str,
    *,
    spot: float | None = None,
    expiries: int = MAX_EXPIRIES,
    allow_yahoo: bool = True,
    history=None,
    next_earnings: str | None = None,
) -> OptionsSnapshot | None:
    """Assemble the options view for one ticker, or None when it has no market.

    CBOE is tried first because it publishes Greeks and a real IV, which is what
    makes a dealer-gamma figure measurable and removes the need to invert a
    volatility from last-traded prices. Yahoo remains the fallback: it has no
    Greeks, so on that source the gamma block is omitted rather than estimated.
    """
    ticker = ticker.upper().strip()
    chain: OptionsChain | None = fetch_cboe_chain(ticker)
    source_kind = "cboe"
    if chain is None or not chain.expiries:
        chain = fetch_yahoo_chain(ticker, expiries=expiries, allow_yahoo=allow_yahoo)
        source_kind = "yahoo"
    if chain is None or not chain.expiries:
        return None

    note: list[str] = []
    if source_kind == "yahoo":
        note.append(
            "CBOE's chain was unavailable, so this reading uses the Yahoo chain, which "
            "reports no Greeks and only placeholder implied volatility — gamma exposure "
            "is omitted and IV is inverted from last-traded prices."
        )
    if not spot:
        spot = chain.spot
        if spot:
            note.append("spot taken from the option chain rather than the quote")

    summaries = [_expiry_summary(e, spot) for e in chain.expiries]
    nearest = chain.expiries[0]
    # "Near month" is the front expiry and any dated within a week of it, capped to
    # the front couple of expiries. Without the cap the window covered every fetched
    # expiry, so the near-month PCR equalled the whole-chain PCR to the last digit
    # and told the reader nothing.
    front_days = (nearest.expiration - date.today()).days
    near_cutoff = front_days + NEAR_MONTH_DAYS
    near_volume_calls = near_volume_puts = 0.0
    near_oi_calls = near_oi_puts = 0.0
    near_expiries = 0
    for expiry in chain.expiries:
        days = (expiry.expiration - date.today()).days
        if days > near_cutoff or near_expiries >= 2:
            continue
        near_expiries += 1
        call_volume, call_oi = _totals(expiry.calls)
        put_volume, put_oi = _totals(expiry.puts)
        near_volume_calls += call_volume
        near_volume_puts += put_volume
        near_oi_calls += call_oi
        near_oi_puts += put_oi

    all_calls = [c for e in chain.expiries for c in e.calls]
    all_puts = [c for e in chain.expiries for c in e.puts]
    total_volume_calls, total_oi_calls = _totals(all_calls)
    total_volume_puts, total_oi_puts = _totals(all_puts)

    snapshot = OptionsSnapshot(
        ticker=ticker,
        spot=spot,
        currency=chain.currency,
        as_of=chain.as_of,
        source=chain.source,
        expiries_available=chain.expiries_available,
        expiries_used=len(chain.expiries),
        summaries=[OptionsExpirySummary(**s) for s in summaries],
        totals={
            "volume_calls": int(total_volume_calls),
            "volume_puts": int(total_volume_puts),
            "oi_calls": int(total_oi_calls),
            "oi_puts": int(total_oi_puts),
            "volume_pcr": _ratio(total_volume_calls, total_volume_puts),
            "oi_pcr": _ratio(total_oi_calls, total_oi_puts),
            "near_volume_pcr": _ratio(near_volume_calls, near_volume_puts),
            "near_oi_pcr": _ratio(near_oi_calls, near_oi_puts),
        },
        max_pain=_max_pain(nearest),
        max_pain_expiry=nearest.expiration.isoformat(),
        straddle_move=_straddle_move(nearest, spot, spot),
        concentration=_concentration(nearest, spot),
        unusual=[
            {
                "kind": c.kind, "strike": c.strike,
                "expiration": c.expiration.isoformat(),
                "days": (c.expiration - date.today()).days,
                "volume": int(c.volume or 0),
                "open_interest": int(c.open_interest or 0),
                # Only reached for contracts with a real book, so this cannot
                # divide by zero.
                "ratio": round((c.volume or 0) / c.open_interest, 1),
                "last_price": c.last_price,
                "direction": _direction(c.kind, c.strike or 0, spot or 0),
            }
            for c in _unusual(nearest)
        ],
        # Traded contracts with no open interest yet: new positions, reported as
        # such rather than given a ratio against a zero book.
        new_positions=[
            {
                "kind": c.kind, "strike": c.strike,
                "expiration": c.expiration.isoformat(),
                "days": (c.expiration - date.today()).days,
                "volume": int(c.volume or 0),
                "last_price": c.last_price,
                "direction": _direction(c.kind, c.strike or 0, spot or 0),
            }
            for c in _new_positions(nearest)
        ],
    )
    # Front-month premium flow, and the judgement the analysis tab leads with.
    snapshot.totals.update(_premium_totals(nearest))

    # Volatility figures come off a ~30-day expiry rather than the front week:
    # a one-day straddle carries almost no information and its inversion is
    # dominated by rounding in the last-traded price.
    vol_expiry = _reference_expiry(chain.expiries, target_days=30)
    # `PriceHistory` exposes closes as a convenience; the raw points are the
    # fallback. The accessor is a method on some model versions and a property on
    # others, so both are accepted rather than assuming one.
    closes: list[float] = []
    accessor = getattr(history, "closes", None)
    if callable(accessor):
        try:
            closes = list(accessor() or [])
        except Exception:  # noqa: BLE001 - fall through to the raw points
            closes = []
    elif accessor:
        closes = list(accessor)
    if not closes and history is not None:
        closes = [p.close for p in getattr(history, "points", []) if getattr(p, "close", None)]
    from app.volatility import volatility_block

    block = volatility_block(spot, vol_expiry, closes) if vol_expiry else {}
    if block:
        snapshot.volatility = OptionsVolatility(**block)
    # A published 30-day IV beats an inverted one, so CBOE's `iv30` is preferred
    # for the ratio when it is present.
    if chain.iv30 and snapshot.volatility.rv_21d:
        snapshot.volatility.iv_atm = chain.iv30
        snapshot.volatility.iv_source = "published iv30"
        snapshot.volatility.iv_rv = chain.iv30 / snapshot.volatility.rv_21d
    # Dealer gamma, only where the chain reports gamma. On the Yahoo fallback a
    # figure would have to be assumed, so none is produced.
    if chain.has_greeks:
        from app.gex import SIGN_CONVENTION, gamma_flip, gex_by_strike, net_gex

        snapshot.gex = net_gex(chain.expiries, spot)
        snapshot.gamma_flip = gamma_flip(chain.expiries, spot)
        snapshot.gex_strikes = gex_by_strike(chain.expiries, spot)
        snapshot.gex_basis = SIGN_CONVENTION
    snapshot.compliance = chain.compliance
    # The dated release, so the verdict can warn when it falls inside the expiry.
    snapshot.next_earnings = next_earnings
    snapshot.verdict = _build_verdict(snapshot)
    snapshot.notes = _interpret(snapshot) + note
    return snapshot


def _interpret(snapshot: OptionsSnapshot) -> list[str]:
    """Plain-language readings, each tied to the figure that produced it.

    Written as observations about positioning with their limits attached, because
    a chain cannot distinguish a hedge from a bet and a screen that implied
    otherwise would be inventing a story.
    """
    notes: list[str] = []
    totals = snapshot.totals
    volume_pcr = totals.get("volume_pcr")
    oi_pcr = totals.get("oi_pcr")

    if volume_pcr is not None:
        if volume_pcr >= 1.0:
            notes.append(
                f"Put volume exceeds call volume ({volume_pcr:.3f}): today's flow leans defensive."
            )
        elif volume_pcr <= 0.7:
            notes.append(
                f"Call volume leads puts ({volume_pcr:.3f}): today's flow leans bullish."
            )
        else:
            notes.append(f"Volume put/call {volume_pcr:.3f}: today's flow is balanced.")

    if volume_pcr is not None and oi_pcr is not None:
        if volume_pcr > oi_pcr and volume_pcr >= 0.9:
            notes.append(
                "Put/call is higher on volume than on open interest, so the defensive tilt "
                "is new today rather than a standing position."
            )
        elif volume_pcr < oi_pcr and volume_pcr <= 0.8:
            notes.append(
                "Put/call is lower on volume than on open interest: bullish flow against a "
                "defensively positioned book."
            )

    if snapshot.max_pain and snapshot.spot:
        gap = snapshot.max_pain / snapshot.spot - 1.0
        notes.append(
            f"Max pain sits at {snapshot.max_pain:g} ({gap:+.1%} from spot) for the "
            f"{snapshot.max_pain_expiry} expiry — a level where expiring contracts cost "
            f"holders least, not a price target."
        )
        if abs(gap) > 0.05:
            notes.append(
                "That is more than 5% from spot, so its pull is weak and largely expires "
                "with the contracts."
            )

    if snapshot.straddle_move:
        notes.append(
            f"The at-the-money straddle costs {snapshot.straddle_move:.1%} of spot, which is "
            f"the move the market has priced to that expiry."
        )

    top = snapshot.concentration[:1]
    if top and top[0]["total_oi"]:
        row = top[0]
        notes.append(
            f"Heaviest open interest sits at {row['strike']:g} "
            f"({row['total_oi']:,} contracts, {row['call_oi']:,} calls / {row['put_oi']:,} puts)."
        )

    if snapshot.unusual:
        biggest = snapshot.unusual[0]
        notes.append(
            f"Most active relative to its book: {biggest['strike']:g} {biggest['kind']} "
            f"expiring {biggest['expiration']} at {biggest['ratio']:.1f}x its open interest."
        )

    notes.append(
        "Open interest shows where contracts exist, never who holds them: an out-of-the-money "
        "put can be a crash hedge or a bet on a fall, and this data cannot tell them apart."
    )
    notes.append(
        "No implied volatility is reported. The free chain returns placeholder IV values with "
        "zero bid/ask, so an IV/RV or skew figure would be fabricated."
    )
    return notes
