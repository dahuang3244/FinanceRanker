"""Normalize known depositary receipts before comparing them with US shares."""

from __future__ import annotations

from app.models import Fundamentals, Quote


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


def normalize_tsm(fund: Fundamentals, quote: Quote, rate: float, fx_source: str) -> Fundamentals:
    """TSM: five ordinary shares per USD ADS; TWD filing translated at spot FX.

    Work on a copy. Original SEC facts stay in TWD, and their provenance is
    retained; only the cross-sectional calculation uses the translated copy.
    """
    if fund.ticker != "TSM" or fund.currency != "TWD" or quote.currency != "USD":
        raise ValueError("TSM normalization requires TWD filing and USD ADS quote")
    if not 0.01 < rate < 0.10:
        raise ValueError("TWD/USD rate outside plausible range")
    result = normalize_tsm_shares(fund, quote)
    for field in _MONETARY_FIELDS:
        series = getattr(result, field)
        if series is not None:
            if series.unit != "TWD":
                raise ValueError(f"{field} unit must be TWD, got {series.unit}")
            series.points = [(d, amount * rate) for d, amount in series.points]
            series.unit = "USD"
    if result.eps_diluted is not None:
        if result.eps_diluted.unit != "TWD/shares":
            raise ValueError("TSM EPS requires TWD per ordinary share")
        result.eps_diluted.points = [
            (d, amount * rate * 5) for d, amount in result.eps_diluted.points
        ]
        result.eps_diluted.unit = "USD/ADS"
    result.currency = "USD"
    result.fx_usd_per_twd = rate
    result.fx_source = fx_source
    result.notes.append(
        f"TSM TWD financials translated to USD at spot {rate:.6f} ({fx_source}); "
        "one ADS = five ordinary shares. USD annual earnings/EV multiples use "
        "current spot FX as an approximation; margins and growth are FX invariant."
    )
    return result
