"""Optional dated Yahoo calendar event; third-party estimate, not issuer guidance."""

from __future__ import annotations

from datetime import datetime, timezone

from app.http import fetch


def get_earnings_event(ticker: str) -> dict | None:
    data = fetch(
        f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}",
        params={"modules": "calendarEvents"}, namespace="earnings_yahoo", ttl=12 * 3600,
        expect_json=True, impersonate=True, timeout=5, retries=1,
    )
    try:
        event = data["quoteSummary"]["result"][0]["calendarEvents"]["earnings"]
        stamps = [d.get("raw") for d in (event.get("earningsDate") or [])]
        dates = [datetime.fromtimestamp(int(v), timezone.utc) for v in stamps if v]
    except (KeyError, IndexError, TypeError, ValueError, OverflowError):
        return None
    now = datetime.now(timezone.utc)
    upcoming = sorted(d for d in dates if 0 <= (d - now).days <= 120)
    if not upcoming:
        return None
    return {"date": upcoming[0].date().isoformat(),
            "source": "Yahoo Finance calendar (estimated; verify with issuer)"}
