"""Public spot FX for explicitly supported depositary receipts.

These rates translate fiscal statements for indicative cross-sectional
valuation. They are current spot rates, not historical accounting FX.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.http import FetchError, fetch


def usd_per_twd(*, allow_yahoo: bool = True) -> tuple[float, str] | None:
    """Fetch a current TWD-to-USD conversion; never silently assume a rate."""
    return usd_per(("TWD",), allow_yahoo=allow_yahoo)


# Plausible bounds are not asserted per currency: a rate is checked only against
# being positive and finite, because a wrong-but-plausible band is worse than none.
# The currencies this is used for are listed so an unsupported one returns nothing
# rather than a rate keyed to a symbol that happens not to exist.
def usd_per(currencies: tuple[str, ...], *, allow_yahoo: bool = True) -> tuple[float, str] | None:
    """A current rate for one currency against USD, or None.

    Generalised from the TWD-only version because the same gap affects every
    depositary receipt: NVO files in DKK and trades in USD, so the cross-currency
    guard dropped its price multiples and left them blank while Yahoo publishes them.

    The reference feed returns a whole table of rates in one request, so covering
    another currency costs no extra call — only the bounds check is per currency, since
    a plausible range for the krone is not a plausible range for the won.
    """
    for currency in currencies:
        if currency == "USD":
            return 1.0, "identity (filings already in USD)"
    yahoo_pairs: list[tuple[str, bool]] = []
    if allow_yahoo:
        for currency in currencies:
            yahoo_pairs.append((f"{currency}USD=X", False))
            yahoo_pairs.append((f"USD{currency}=X", True))
    bounds = {
        "TWD": (0.01, 0.10),
        "DKK": (0.05, 0.50),
        "EUR": (0.5, 2.0),
        "GBP": (0.5, 2.5),
        "JPY": (0.002, 0.05),
        "KRW": (0.0002, 0.005),
        "CHF": (0.5, 2.0),
        "SEK": (0.03, 0.40),
        "NOK": (0.03, 0.40),
        "CAD": (0.5, 1.5),
        "ILS": (0.1, 1.0),
        "INR": (0.005, 0.05),
        "CNY": (0.05, 0.50),
        "HKD": (0.05, 0.30),
        "SGD": (0.4, 1.5),
        "AUD": (0.4, 1.5),
    }
    for currency in currencies:
        low, high = bounds.get(currency, (1e-6, 1e6))
        for symbol, inverse in [pair for pair in yahoo_pairs
                                if currency in pair[0]]:
            for host in ("query1", "query2"):
                url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}"
                try:
                    data = fetch(
                        url,
                        params={"range": "5d", "interval": "1d"},
                        namespace="fx_yahoo",
                        ttl=3600,
                        expect_json=True,
                        impersonate=True,
                        retries=1,
                    )
                    result = data["chart"]["result"][0]
                    closes = result["indicators"]["quote"][0]["close"]
                    price = next(float(v) for v in reversed(closes)
                                 if v is not None and v > 0)
                    rate = 1 / price if inverse else price
                    # Bounds catch an inverted or incorrectly scaled quotation.
                    if not low < rate < high:
                        continue
                    stamp = result["timestamp"][-1]
                    as_of = datetime.fromtimestamp(
                        stamp, tz=timezone.utc).date().isoformat()
                    return rate, f"{url} (close {as_of})"
                except (FetchError, KeyError, IndexError, TypeError,
                        ValueError, StopIteration):
                    continue
    # Yahoo may be blocked while the SEC and quote providers still work. This open,
    # keyless reference rate updates daily and carries the whole table at once, so
    # every supported currency is reachable from one request. Its date is marked and
    # the spot-FX approximation warning on translated filings still applies.
    url = "https://open.er-api.com/v6/latest/USD"
    try:
        data = fetch(url, namespace="fx_reference", ttl=24 * 3600,
                     expect_json=True, retries=1)
        if data.get("result") != "success":
            return None
        rates = data.get("rates") or {}
        for currency in currencies:
            per_usd = rates.get(currency)
            if per_usd is None:
                continue
            try:
                rate = 1 / float(per_usd)
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            low, high = bounds.get(currency, (1e-6, 1e6))
            if low < rate < high:
                return rate, (f"{url} (reference update "
                              f"{data.get('time_last_update_utc', 'unknown')})")
    except (FetchError, KeyError, TypeError, ValueError, ZeroDivisionError):
        pass
    return None
