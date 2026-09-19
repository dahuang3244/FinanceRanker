"""US ticker universe for the preview picker (US equities only).

Source priority, based on what actually responds from this network:

  1. SEC `company_tickers.json` — 10k+ issuers, authoritative, fast, carries the
     CIK that makes the SEC fundamentals lookup a direct hit.
  2. akshare `stock_us_spot_em` — richer (price/market cap) but its Eastmoney
     push2 load-balancers are frequently unreachable, so it is only a bonus.
  3. A small curated seed list so the picker is usable on a cold start.

Everything is cached to disk; the picker therefore stays responsive offline.
"""

from __future__ import annotations

import logging

from app import cache
from app.config import settings
from app.http import FetchError, fetch
from app.preview_models import TickerInfo

log = logging.getLogger(__name__)

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
NS = "universe"
FULL_TTL = 24 * 3600

# Starters that mirror the workbook's own peer list, so the picker is useful
# before any network call succeeds.
SEED: list[tuple[str, str]] = [
    ("MSFT", "Microsoft Corporation"),
    ("AAPL", "Apple Inc."),
    ("GOOGL", "Alphabet Inc."),
    ("AMZN", "Amazon.com, Inc."),
    ("META", "Meta Platforms, Inc."),
    ("NVDA", "NVIDIA Corporation"),
    ("TSM", "Taiwan Semiconductor Manufacturing"),
    ("AMD", "Advanced Micro Devices, Inc."),
    ("QCOM", "QUALCOMM Incorporated"),
    ("AVGO", "Broadcom Inc."),
    ("AMAT", "Applied Materials, Inc."),
    ("MU", "Micron Technology, Inc."),
    ("ORCL", "Oracle Corporation"),
    ("INTC", "Intel Corporation"),
    ("TSLA", "Tesla, Inc."),
    ("NFLX", "Netflix, Inc."),
    ("ADBE", "Adobe Inc."),
    ("CRM", "Salesforce, Inc."),
    ("CSCO", "Cisco Systems, Inc."),
    ("IBM", "International Business Machines"),
    ("TXN", "Texas Instruments Incorporated"),
    ("LRCX", "Lam Research Corporation"),
    ("KLAC", "KLA Corporation"),
    ("SNOW", "Snowflake Inc."),
    ("PLTR", "Palantir Technologies Inc."),
    ("UBER", "Uber Technologies, Inc."),
    ("ABNB", "Airbnb, Inc."),
    ("SHOP", "Shopify Inc."),
    ("SQ", "Block, Inc."),
    ("PYPL", "PayPal Holdings, Inc."),
]

# Brand / product names that the SEC registrant list does not spell out, mapped
# to the listed symbol so a search for the product still finds the company.
ALIASES: dict[str, list[str]] = {
    "GOOGLE": ["GOOGL", "GOOG"],
    "ALPHABET": ["GOOGL", "GOOG"],
    "FACEBOOK": ["META"],
    "INSTAGRAM": ["META"],
    "WHATSAPP": ["META"],
    "IPHONE": ["AAPL"],
    "MACBOOK": ["AAPL"],
    "WINDOWS": ["MSFT"],
    "AZURE": ["MSFT"],
    "TESLA": ["TSLA"],
    "NETFLIX": ["NFLX"],
    "AMAZON": ["AMZN"],
    "AWS": ["AMZN"],
    "NVIDIA": ["NVDA"],
    "TAIWAN SEMICONDUCTOR": ["TSM"],
    "TSMC": ["TSM"],
    "INTEL": ["INTC"],
    "MICRON": ["MU"],
    "BROADCOM": ["AVGO"],
    "QUALCOMM": ["QCOM"],
    "ORACLE": ["ORCL"],
    "SALESFORCE": ["CRM"],
    "ADOBE": ["ADBE"],
    "PALANTIR": ["PLTR"],
    "SNOWFLAKE": ["SNOW"],
    "PAYPAL": ["PYPL"],
}

# Exchanges implied by SEC's ticker suffix conventions.
_EXCHANGE_HINTS = (("", "US"))


def _from_sec() -> list[TickerInfo]:
    raw = fetch(
        SEC_TICKERS_URL,
        headers={
            "User-Agent": settings.sec_user_agent,
            "Accept-Encoding": "gzip, deflate",
        },
        namespace="sec_map",
        ttl=FULL_TTL,
        expect_json=True,
        retries=3,
    )
    out: list[TickerInfo] = []
    for entry in (raw or {}).values():
        try:
            ticker = str(entry["ticker"]).upper().strip()
            cik = int(entry["cik_str"])
        except (KeyError, TypeError, ValueError):
            continue
        if not ticker:
            continue
        out.append(
            TickerInfo(
                ticker=ticker,
                name=str(entry.get("title", "")).strip(),
                exchange="US",
                cik=cik,
                source="SEC",
            )
        )
    return out


def _from_akshare() -> list[TickerInfo]:
    """Bonus source: adds Chinese short names. Safe to fail."""
    try:
        import akshare as ak

        frame = ak.stock_us_spot_em()
    except Exception as exc:
        log.info("akshare US spot unavailable (%s); using SEC-only universe", exc)
        return []
    if frame is None or frame.empty:
        return []
    out: list[TickerInfo] = []
    code_col = "代码" if "代码" in frame.columns else None
    name_col = "名称" if "名称" in frame.columns else None
    if not code_col:
        return []
    for _, row in frame.iterrows():
        raw = str(row.get(code_col, ""))
        # akshare encodes the exchange as a numeric prefix: 105/106/107.TICKER
        ticker = raw.split(".")[-1].upper().strip()
        if not ticker:
            continue
        out.append(
            TickerInfo(
                ticker=ticker,
                name=str(row.get(name_col, "")).strip() if name_col else "",
                exchange=raw.split(".")[0] if "." in raw else "",
                source="akshare",
            )
        )
    return out


def seed_universe() -> list[TickerInfo]:
    return [TickerInfo(ticker=t, name=n, exchange="US", source="seed") for t, n in SEED]


def load_universe(*, force: bool = False) -> list[TickerInfo]:
    """Full US ticker list, cached; never raises."""
    if not force:
        cached = cache.get(NS, "all", FULL_TTL)
        if cached:
            items = [TickerInfo.model_validate(x) for x in cached]
            if len(items) >= len(SEED):
                return items

    items: list[TickerInfo] = []
    by_ticker: dict[str, TickerInfo] = {}

    try:
        for info in _from_sec():
            by_ticker[info.ticker] = info
    except FetchError as exc:
        log.warning("SEC ticker list unavailable: %s", exc)

    # Enrich with akshare names when reachable; do not let it block the result.
    try:
        for info in _from_akshare():
            existing = by_ticker.get(info.ticker)
            if existing is None:
                by_ticker[info.ticker] = info
            elif not existing.name and info.name:
                existing.name = info.name
                existing.source = f"{existing.source}+akshare"
    except Exception as exc:  # pragma: no cover - defensive
        log.info("akshare universe enrichment skipped: %s", exc)

    for ticker, name in SEED:
        if ticker not in by_ticker:
            by_ticker[ticker] = TickerInfo(
                ticker=ticker, name=name, exchange="US", source="seed"
            )

    items = sorted(by_ticker.values(), key=lambda x: x.ticker)
    if items:
        cache.put(NS, "all", [x.model_dump() for x in items])
    return items


def search(query: str, limit: int = 40, pool: list[TickerInfo] | None = None) -> list[TickerInfo]:
    """Rank matches: exact, then prefix, then ticker-substring, then name.

    The whole universe is scanned every time (10k dict lookups is well under a
    millisecond). An earlier version bailed out after collecting a few matches,
    which — on an alphabetically sorted universe — meant a query like "NV" never
    reached NVDA. Ticker matches always outrank name matches so that searching a
    product name cannot bury the obvious symbol.
    """
    universe = load_universe() if pool is None else pool
    text = (query or "").strip().upper()
    if not text:
        seeds = {t for t, _ in SEED}
        head = [x for x in universe if x.ticker in seeds]
        rest = [x for x in universe if x.ticker not in seeds]
        return (head + rest)[:limit]

    universe_by_ticker = {x.ticker: x for x in universe}
    buckets: list[list[TickerInfo]] = [[], [], [], []]  # exact, prefix, contains, name
    for info in universe:
        ticker = info.ticker
        if ticker == text:
            buckets[0].append(info)
        elif ticker.startswith(text):
            buckets[1].append(info)
        elif text in ticker:
            buckets[2].append(info)
        elif text in (info.name or "").upper():
            buckets[3].append(info)

    # Within a bucket, prefer the shortest symbol: for "NV" the Nvidia-style
    # 4-letter prefixes read as more relevant than 3-letter ones like NVA/NVG.
    # Then prefer an earlier position and a shorter name.
    curated = {t for t, _ in SEED}

    def key(info: TickerInfo) -> tuple[int, int, int, int]:
        # Well-known tickers first, then the shortest symbol ("NV" -> NVDA rather
        # than NVR), then earliest match position, then shortest name.
        pos = info.ticker.find(text)
        return (
            0 if info.ticker in curated else 1,
            len(info.ticker),
            pos if pos >= 0 else 999,
            len(info.name or ""),
        )

    # Product/brand names that the SEC issuer list does not contain (it lists the
    # registrant, e.g. "Alphabet Inc." rather than "Google").
    aliased = [universe_by_ticker[t] for t in ALIASES.get(text, []) if t in universe_by_ticker]

    out: list[TickerInfo] = list(aliased)
    for bucket in buckets:
        out.extend(sorted(bucket, key=key))
        if len(out) >= limit:
            break
    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique: list[TickerInfo] = []
    for info in out:
        if info.ticker not in seen:
            seen.add(info.ticker)
            unique.append(info)
    return unique[:limit]


def resolve(ticker: str) -> TickerInfo | None:
    """Look one ticker up in the universe (for the CIK and the display name)."""
    text = (ticker or "").strip().upper()
    if not text:
        return None
    for info in load_universe():
        if info.ticker == text:
            return info
    return None
