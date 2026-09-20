"""Public spot FX for explicitly supported depositary receipts.

These rates translate fiscal statements for indicative cross-sectional
valuation. They are current spot rates, not historical accounting FX.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.http import FetchError, fetch


def usd_per_twd(*, allow_yahoo: bool = True) -> tuple[float, str] | None:
    """Fetch a current TWD-to-USD conversion; never silently assume a rate."""
    yahoo_pairs = (("TWDUSD=X", False), ("USDTWD=X", True)) if allow_yahoo else ()
    for symbol, inverse in yahoo_pairs:
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
                price = next(float(v) for v in reversed(closes) if v is not None and v > 0)
                rate = 1 / price if inverse else price
                # Bounds catch an inverted or incorrectly scaled quotation.
                if not 0.01 < rate < 0.10:
                    continue
                stamp = result["timestamp"][-1]
                as_of = datetime.fromtimestamp(stamp, tz=timezone.utc).date().isoformat()
                return rate, f"{url} (close {as_of})"
            except (FetchError, KeyError, IndexError, TypeError, ValueError, StopIteration):
                continue
    # Yahoo may be blocked while the SEC and quote providers still work.
    # This open, keyless reference rate updates daily, so mark its date and
    # retain the existing spot-FX approximation warning on translated filings.
    url = "https://open.er-api.com/v6/latest/USD"
    try:
        data = fetch(url, namespace="fx_reference", ttl=24 * 3600,
                     expect_json=True, retries=1)
        if data.get("result") != "success":
            return None
        twd_per_usd = float(data["rates"]["TWD"])
        rate = 1 / twd_per_usd
        if 0.01 < rate < 0.10:
            return rate, f"{url} (reference update {data.get('time_last_update_utc', 'unknown')})"
    except (FetchError, KeyError, TypeError, ValueError, ZeroDivisionError):
        pass
    return None
