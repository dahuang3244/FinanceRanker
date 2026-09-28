"""Normalize known depositary receipts before comparing them with US shares."""

from __future__ import annotations

from app.models import Fundamentals, Quote


# Depositary receipts whose ADS ratio is known. One ADS equals this many ordinary
# shares; a ratio of 1.0 is an ADS that represents one share.
#
# The ratio cannot be inferred and must not be guessed: it scales both the share count
# and the per-share figures, so getting it wrong misstates every multiple while
# looking entirely plausible. Each entry is here because it is a stated, stable ratio,
# and a receipt whose ratio is unknown is left untranslated rather than approximated —
# blank figures are recoverable, confidently wrong ones are not.
_ADS_RATIO = {
    "TSM": 5.0,     # one ADS = five ordinary shares
    "NVO": 1.0,     # one ADS = one B share
    "ASML": 1.0,
    "SAP": 1.0,
    "SHEL": 2.0,
    "BP": 1.0,
    "AZN": 0.5,     # one ADS = two ordinary shares
    "UL": 1.0,
    "DEO": 1.0,
    "RIO": 1.0,
    "HSBC": 1.0,
}


_MONETARY_FIELDS = (
    "revenue", "gross_profit", "cost_of_revenue", "operating_income",
    "net_income", "equity", "assets", "cash", "operating_cash_flow",
    "capex", "sbc", "restructuring", "amortization",
    "depreciation_amortization", "tax_provision", "pretax_income",
    "debt_current", "debt_long",
)


def normalize_tsm_shares(fund: Fundamentals, quote: Quote) -> Fundamentals:
    """Put ordinary share counts on the ADS basis without altering TWD facts."""
    if fund.ticker != "TSM" or fund.currency != "TWD" or quote.currency != "USD":
        raise ValueError("TSM share normalization requires TWD filing and USD ADS quote")
    result = fund.model_copy(deep=True)
    result.reporting_currency = "TWD"
    if result.shares_diluted is not None:
        if result.shares_diluted.unit not in ("shares", "ordinary shares"):
            raise ValueError("TSM share series must contain ordinary shares")
        result.shares_diluted.points = [(d, amount / 5) for d, amount in result.shares_diluted.points]
        result.shares_diluted.unit = "ADS"
    if result.shares_outstanding is not None:
        result.shares_outstanding /= 5
    result.shares_basis = "TSM: ordinary shares / 5 ADS ratio"
    return result


def normalize_depositary(fund: Fundamentals, quote: Quote, rate: float,
                         fx_source: str) -> Fundamentals | None:
    """Translate any supported depositary receipt's filing currency into USD.

    Generalised from the TSM-only version, because the gap is not TSM's: NVO files in
    DKK and trades in USD, so the cross-currency guard withheld its price multiples and
    left them blank — while Yahoo publishes them, having done exactly this conversion.

    Returns None when the receipt is not one whose ratio is known, and the caller then
    leaves the figures untouched. That is deliberate: an unknown ratio cannot be
    inferred from the data, and guessing it misstates every multiple by the ratio while
    looking entirely plausible.
    """
    ticker = (fund.ticker or "").upper()
    ratio = _ADS_RATIO.get(ticker)
    if ratio is None:
        return None
    filing = (fund.currency or "").upper()
    if not filing or filing == "USD" or (quote.currency or "USD").upper() != "USD":
        return None
    if not rate or rate <= 0:
        return None

    result = fund.model_copy(deep=True)
    result.reporting_currency = filing

    # Share counts move to the ADS basis. Ordinary shares per ADS divide the count.
    if result.shares_diluted is not None:
        if result.shares_diluted.unit not in ("shares", "ordinary shares"):
            raise ValueError(f"{ticker} share series must contain ordinary shares")
        result.shares_diluted.points = [
            (day, amount / ratio) for day, amount in result.shares_diluted.points
        ]
        result.shares_diluted.unit = "ADS"
    if result.shares_outstanding is not None:
        result.shares_outstanding /= ratio

    for field in _MONETARY_FIELDS:
        series = getattr(result, field)
        if series is None:
            continue
        if (series.unit or "").upper() != filing:
            raise ValueError(f"{field} unit must be {filing}, got {series.unit}")
        series.points = [(day, amount * rate) for day, amount in series.points]
        series.unit = "USD"

    # A per-share figure needs the rate *and* the ratio: the ADS represents `ratio`
    # ordinary shares, so its earnings are that many times one share's.
    if result.eps_diluted is not None:
        expected = f"{filing}/shares"
        if (result.eps_diluted.unit or "") != expected:
            raise ValueError(
                f"{ticker} EPS requires {expected}, got {result.eps_diluted.unit}")
        result.eps_diluted.points = [
            (day, amount * rate * ratio)
            for day, amount in result.eps_diluted.points
        ]
        result.eps_diluted.unit = "USD/ADS"

    result.currency = "USD"
    if filing == "TWD":
        result.fx_usd_per_twd = rate
    result.fx_source = fx_source
    result.shares_basis = (
        f"{ticker}: ordinary shares / {ratio:g} ADS ratio"
        if ratio != 1 else f"{ticker}: one ADS per ordinary share"
    )
    result.notes.append(
        f"{ticker} {filing} financials translated to USD at spot {rate:.6f} "
        f"({fx_source}); one ADS = {ratio:g} ordinary share(s). USD annual "
        "earnings/EV multiples use current spot FX as an approximation; margins and "
        "growth are FX invariant."
    )
    return result


def normalize_tsm(fund: Fundamentals, quote: Quote, rate: float, fx_source: str) -> Fundamentals:
    """TSM: five ordinary shares per USD ADS; TWD filing translated at spot FX.

    Work on a copy. Original SEC facts stay in TWD, and their provenance is
    retained; only the cross-sectional calculation uses the translated copy.
    """
    if fund.ticker != "TSM" or fund.currency != "TWD" or quote.currency != "USD":
        raise ValueError("TSM normalization requires TWD filing and USD ADS quote")
    if not 0.01 < rate < 0.10:
        raise ValueError("TWD/USD rate outside plausible range")
    result = normalize_depositary(fund, quote, rate, fx_source)
    if result is None:  # pragma: no cover - TSM is in the ratio table
        raise ValueError("TSM normalization requires a known ADS ratio")
    return result
