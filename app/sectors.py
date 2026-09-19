"""US sector classification for industry-relative ranking.

Source of truth is the SEC's own **SIC code** (Standard Industrial
Classification), read from `data.sec.gov/submissions/CIK##########.json`.
That is deliberately chosen over a third-party sector label because:

* it is official and free, with no key and no rate-limit games;
* every SEC registrant carries one, so coverage is total;
* SIC is a *hierarchy*, so the six coarse modules the product asks for can be
  derived from the standard two-digit major groups instead of being guessed.

The trade-off is direction of access: `company_tickers.json` (used for the
ticker universe) has no SIC, so codes must be fetched per CIK. Those lookups are
cached permanently and can be pre-warmed in bulk (see `build_index`), so the
normal path is: fetch the entered ticker's sector, then read peers from cache.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from app import cache
from app.config import settings
from app.http import FetchError, fetch

log = logging.getLogger(__name__)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
NS = "sector"
CACHE_TTL = 0          # SIC codes effectively never change: cache forever

# --------------------------------------------------------------------------- #
# The six modules presented in the UI.
# --------------------------------------------------------------------------- #
SECTORS: list[tuple[str, str, str]] = [
    # (key, 中文, English)
    ("tech", "科技类", "Technology"),
    ("finance", "金融类", "Financials"),
    ("health", "医药食品类", "Healthcare & Food"),
    ("media", "媒体类", "Media & Telecom"),
    ("autoenergy", "汽车能源类", "Auto & Energy"),
    ("industrial", "制造零售类", "Industrial & Retail"),
]
SECTOR_KEYS = [key for key, _, _ in SECTORS]
SECTOR_LABELS = {key: (zh, en) for key, zh, en in SECTORS}


# --------------------------------------------------------------------------- #
# SIC -> module mapping
# --------------------------------------------------------------------------- #
# Refinements examined before the division default, because SIC's own divisions
# are too coarse for the product's categories: motor vehicles (3711) sits in the
# manufacturing division but belongs under 汽车能源类, and pharma (2834) sits in
# the chemicals division but belongs under 医药食品类.
SIC_OVERRIDES: list[tuple[int, int, str]] = [
    # technology & semiconductors
    (3570, 3579, "tech"),   # computer & office equipment
    (3660, 3669, "tech"),   # communications equipment
    (3670, 3679, "tech"),   # electronic components / semiconductors
    (3820, 3829, "tech"),   # measuring & controlling devices
    (3860, 3869, "tech"),   # photographic & optical equipment
    (7370, 7379, "tech"),   # computer programming, data processing, software
    # healthcare & food
    (2830, 2836, "health"),  # drugs / pharmaceuticals
    (3840, 3851, "health"),  # medical instruments & supplies
    (8000, 8099, "health"),  # health services
    (2000, 2199, "health"),  # food & kindred products
    (2080, 2087, "health"),  # beverages
    (5140, 5149, "health"),  # wholesale food & drugs
    (5400, 5499, "health"),  # food stores
    (5810, 5819, "health"),  # eating & drinking places
    # media & telecom
    (2700, 2799, "media"),   # printing & publishing
    (4800, 4899, "media"),   # communications / telecom
    (7810, 7841, "media"),   # motion pictures
    (7900, 7999, "media"),   # amusement & recreation
    # auto & energy
    (1300, 1389, "autoenergy"),  # oil & gas extraction
    (2900, 2999, "autoenergy"),  # petroleum refining
    (4900, 4991, "autoenergy"),  # electric, gas & sanitary services
    (3710, 3716, "autoenergy"),  # motor vehicles & parts
    (3720, 3729, "autoenergy"),  # aerospace (heavy industry, grouped here)
    # industrial & retail
    (5200, 5399, "industrial"),  # building materials, general merchandise, retail
    (5600, 5699, "industrial"),  # apparel & accessory stores
    (5700, 5799, "industrial"),  # home furniture & equipment stores
    (5900, 5999, "industrial"),  # miscellaneous retail
]

# Default by two-digit SIC major group, derived from the standard divisions.
SIC_DIVISIONS: list[tuple[int, int, str]] = [
    (100, 999, "autoenergy"),     # agriculture (grouped with energy/commodities)
    (1000, 1499, "autoenergy"),   # mining & extraction
    (1500, 1799, "industrial"),   # construction
    (2000, 3999, "industrial"),   # manufacturing (refined above where needed)
    (4000, 4499, "autoenergy"),   # transportation
    (4500, 4599, "autoenergy"),   # air transportation
    (4600, 4799, "autoenergy"),   # pipelines / transport services
    (4800, 4899, "media"),        # communications
    (4900, 4999, "autoenergy"),   # electric, gas, sanitary services
    (5000, 5199, "industrial"),   # wholesale trade
    (5200, 5999, "industrial"),   # retail trade
    (6000, 6199, "finance"),      # depository institutions / credit
    (6200, 6299, "finance"),      # security & commodity brokers
    (6300, 6499, "finance"),      # insurance carriers
    (6500, 6599, "finance"),      # real estate
    (6700, 6799, "finance"),      # holding & investment offices
    (7000, 7299, "industrial"),   # hotels / personal services
    (7300, 7369, "industrial"),   # business services
    (7370, 7379, "tech"),         # computer & data processing services
    (7380, 7399, "industrial"),   # other business services
    (7500, 7599, "autoenergy"),   # auto repair & services
    (7600, 7699, "industrial"),   # misc repair services
    (7800, 7999, "media"),        # motion pictures / amusement
    (8000, 8099, "health"),       # health services
    (8100, 8999, "industrial"),   # legal / engineering / management services
    (9000, 9999, "industrial"),   # public administration / non-classifiable
]


def sector_for_sic(sic: int | str | None) -> str | None:
    """Map an SIC code to one of the six modules."""
    if sic is None:
        return None
    try:
        code = int(str(sic).strip())
    except (TypeError, ValueError):
        return None
    if code <= 0:
        return None
    for low, high, key in SIC_OVERRIDES:
        if low <= code <= high:
            return key
    for low, high, key in SIC_DIVISIONS:
        if low <= code <= high:
            return key
    return None


# --------------------------------------------------------------------------- #
# Curated seed
# --------------------------------------------------------------------------- #
# The SEC ticker map has no SIC, so codes must be looked up per CIK and a full
# warm-up is a ~10k-request crawl. This seed makes the feature useful on first
# load and gives each module a sane starting floor; the index grows from there
# (every lookup is remembered, and `build_index` can pre-warm in bulk).
SEED_BY_SECTOR: dict[str, list[str]] = {
    "tech": [
        "MSFT", "AAPL", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "AMD", "QCOM",
        "INTC", "MU", "AMAT", "LRCX", "KLAC", "ADBE", "CRM", "ORCL", "CSCO",
        "IBM", "TXN", "NOW", "INTU", "ADI", "MRVL", "SNPS", "CDNS", "PANW",
        "CRWD", "SNOW", "PLTR", "SHOP", "UBER", "ABNB", "NFLX", "TSM", "ASML",
    ],
    "finance": [
        "JPM", "BAC", "WFC", "C", "GS", "MS", "BLK", "SCHW", "AXP", "SPGI",
        "V", "MA", "PYPL", "BRK-B", "USB", "PNC", "TFC", "COF", "ICE", "CME",
        "AIG", "MET", "PRU", "ALL", "TRV", "PGR", "CB", "MMC", "AON", "FI",
    ],
    "health": [
        "JNJ", "PFE", "MRK", "ABBV", "LLY", "BMY", "AMGN", "GILD", "BIIB",
        "UNH", "CVS", "CI", "HUM", "MDT", "ABT", "SYK", "BSX", "ZBH", "BDX",
        "ISRG", "VRTX", "REGN", "MRNA", "TMO", "DHR", "A", "WAT", "KO", "PEP",
        "PG", "PEP", "MCD", "SBUX", "WMT", "COST", "MDLZ", "CL", "KMB", "GIS",
    ],
    "media": [
        "GOOGL", "META", "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS", "CHTR",
        "WBD", "PARA", "FOXA", "NWSA", "OMC", "IPG", "EA", "TTWO", "RBLX",
        "SPOT", "PINS", "SNAP", "TTD", "MTCH", "IAC", "LYV",
    ],
    "autoenergy": [
        "XOM", "CVX", "COP", "EOG", "SLB", "OXY", "PSX", "VLO", "MPC", "KMI",
        "WMB", "OKE", "HAL", "BKR", "FANG", "DVN", "HES", "MRO", "TSLA", "F",
        "GM", "RIVN", "LCID", "NIO", "TM", "HMC", "STLA", "APTV", "BWA",
        "NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "PEG", "ED", "XEL",
    ],
    "industrial": [
        "CAT", "DE", "HON", "GE", "MMM", "BA", "LMT", "RTX", "NOC", "GD",
        "UNP", "CSX", "NSC", "UPS", "FDX", "HD", "LOW", "TGT", "COST", "TJX",
        "ROST", "DG", "DLTR", "BBY", "NKE", "SBUX", "MCD", "YUM", "CMG",
        "PG", "PEP", "KO", "MMM", "ITW", "EMR", "ETN", "PH", "ROK", "CMI",
    ],
}


def _seed_entries() -> dict[str, dict]:
    """Seed tickers grouped by module, resolved to CIK where the universe has it."""
    from app.universe import load_universe

    by_ticker = {x.ticker: x for x in load_universe()}
    out: dict[str, dict] = {}
    for sector, tickers in SEED_BY_SECTOR.items():
        for symbol in tickers:
            if symbol in out:
                continue
            info = by_ticker.get(symbol)
            if info is None:
                # Some seeds are share classes the SEC map spells differently;
                # keep them anyway so the module is not empty without network.
                out[symbol] = {
                    "ticker": symbol, "cik": None, "sic": None,
                    "sicDescription": "", "sector": sector,
                    "name": "", "seed": True,
                }
                continue
            out[symbol] = {
                "ticker": symbol,
                "cik": info.cik,
                "sic": None,
                "sicDescription": "",
                "sector": sector,
                "name": info.name,
                "seed": True,
            }
    return out


def sector_candidates(sector: str, *, limit: int = 60) -> list[dict]:
    """Peer candidates for a module: resolved index entries first, then the seed.

    Index entries are preferred because they were resolved from a real SIC code;
    the seed is a floor so a cold cache still yields a usable ranking.
    """
    merged: dict[str, dict] = {}
    for entry in _seed_entries().values():
        if entry["sector"] == sector:
            merged[entry["ticker"]] = dict(entry)
    for entry in _index().values():
        if entry.get("sector") == sector:
            merged[entry["ticker"]] = {**merged.get(entry["ticker"], {}), **entry}
    rows = list(merged.values())
    # Prefer entries we can actually resolve to a CIK (they can be ranked).
    rows.sort(key=lambda r: (r.get("cik") is None, r["ticker"]))
    return rows[:limit]


# --------------------------------------------------------------------------- #
# SEC lookups
# --------------------------------------------------------------------------- #
@dataclass
class CompanySector:
    ticker: str
    cik: int
    sic: int | None
    sic_description: str
    sector: str | None
    name: str = ""
    exchange: str = ""


def _primary_exchange(exchanges) -> str | None:
    """SEC lists an exchange per class and often repeats it; take the first."""
    if isinstance(exchanges, str):
        return exchanges or None
    if isinstance(exchanges, (list, tuple)):
        for item in exchanges:
            if item:
                return str(item)
    return None


def _fetch_submission(cik: int) -> dict | None:
    cached = cache.get(NS, f"cik:{cik}", CACHE_TTL)
    if cached is not None:
        return cached
    try:
        data = fetch(
            SUBMISSIONS_URL.format(cik=cik),
            headers={
                "User-Agent": settings.sec_user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
            namespace=None,          # we manage the cache ourselves (TTL = forever)
            expect_json=True,
            timeout=30,
            retries=3,
        )
    except FetchError as exc:
        log.info("SEC submissions lookup failed for CIK %s: %s", cik, exc)
        return None

    slim = {
        "sic": data.get("sic"),
        "sicDescription": data.get("sicDescription"),
        "name": data.get("name"),
        # SEC returns this as a list (sometimes with repeats); keep the raw list
        # so the cache mirrors the source, and pick the primary exchange on read.
        "exchanges": data.get("exchanges") or [],
        "tickers": data.get("tickers") or [],
    }
    cache.put(NS, f"cik:{cik}", slim)
    return slim


def get_company_sector(ticker: str, cik: int | None = None) -> CompanySector | None:
    """Resolve one ticker's SIC code and module."""
    symbol = (ticker or "").strip().upper()
    if not symbol:
        return None

    if cik is None:
        from app.universe import resolve

        info = resolve(symbol)
        if info is None or info.cik is None:
            return None
        cik = info.cik

    data = _fetch_submission(cik)
    if not data:
        return None

    sic_raw = data.get("sic")
    try:
        sic = int(str(sic_raw).strip()) if sic_raw is not None else None
    except (TypeError, ValueError):
        sic = None

    return CompanySector(
        ticker=symbol,
        cik=cik,
        sic=sic,
        sic_description=str(data.get("sicDescription") or ""),
        sector=sector_for_sic(sic),
        name=str(data.get("name") or ""),
        exchange=str(_primary_exchange(data.get("exchanges")) or ""),
    )


# --------------------------------------------------------------------------- #
# Sector index: ticker -> sector, cached, optionally pre-warmed in bulk
# --------------------------------------------------------------------------- #
def _index() -> dict[str, dict]:
    return cache.get(NS, "index", CACHE_TTL) or {}


def index_size() -> int:
    return len(_index())


def index_coverage() -> dict[str, int]:
    counts = {key: 0 for key in SECTOR_KEYS}
    for entry in _index().values():
        key = entry.get("sector")
        if key in counts:
            counts[key] += 1
    return counts


def remember(item: CompanySector) -> None:
    """Add one resolved company to the sector index."""
    if not item.sector:
        return
    data = _index()
    data[item.ticker] = {
        "ticker": item.ticker,
        "cik": item.cik,
        "sic": item.sic,
        "sicDescription": item.sic_description,
        "sector": item.sector,
        "name": item.name,
        "updated": date.today().isoformat(),
    }
    cache.put(NS, "index", data)


def sector_peers(sector: str, *, limit: int = 0) -> list[dict]:
    """Companies already indexed under `sector`."""
    rows = [v for v in _index().values() if v.get("sector") == sector]
    rows.sort(key=lambda r: r["ticker"])
    return rows[:limit] if limit else rows


def build_index(
    tickers: list[str] | None = None,
    *,
    limit: int = 0,
    progress=None,
    delay: float = 0.12,
) -> dict:
    """Pre-warm the sector index by walking the ticker universe.

    SEC fair-access guidance is respected via a small delay between requests, so
    warming all ~10k issuers takes a while; `limit` makes it practical to start
    with the largest names.
    """
    import time

    from app.universe import load_universe

    universe = load_universe()
    by_ticker = {x.ticker: x for x in universe}
    targets = [t.upper() for t in tickers] if tickers else [x.ticker for x in universe]
    if limit:
        targets = targets[:limit]

    done = added = failed = 0
    total = len(targets)
    for symbol in targets:
        info = by_ticker.get(symbol)
        if info is None or info.cik is None:
            continue
        item = get_company_sector(symbol, info.cik)
        done += 1
        if item and item.sector:
            remember(item)
            added += 1
        else:
            failed += 1
        if progress and done % 25 == 0:
            progress(done, total, added)
        if delay:
            time.sleep(delay)

    return {"requested": total, "processed": done, "indexed": added, "skipped": failed}


# --------------------------------------------------------------------------- #
# Sector-relative ranking
# --------------------------------------------------------------------------- #
def rank_sector(
    sector: str,
    *,
    pool_size: int = 40,
    top: int = 10,
    focus: str | None = None,
    progress=None,
) -> dict:
    """Fetch a module's candidates, score them together, return the top `top`.

    Cross-sectional percentile scoring only means anything *within* one peer
    set, so the candidates are scored in a single `build_rows` pass rather than
    by fetching a score per ticker. `focus` (the ticker the user typed) is always
    included so its rank is meaningful even if it would not otherwise be picked.
    """
    from app import pipeline

    candidates = sector_candidates(sector, limit=pool_size)
    tickers: list[str] = [c["ticker"] for c in candidates]
    if focus:
        symbol = focus.strip().upper()
        if symbol and symbol not in tickers:
            tickers.insert(0, symbol)
    if not tickers:
        return {"sector": sector, "rows": [], "errors": {}, "pool": []}

    rows, errors = pipeline.build_rows(tickers, progress=progress)

    # Present by overall score so the module reads as a ranking; keep unresolved
    # tickers listed but last, since a missing score is not a low score.
    rows.sort(key=lambda r: (r.score_overall is None, -(r.score_overall or 0)))
    ranked = rows[:top] if top else rows

    return {
        "sector": sector,
        "sector_label": SECTOR_LABELS.get(sector, (sector, sector)),
        "pool": tickers,
        "pool_size": len(tickers),
        "rows": [r.model_dump(mode="json") for r in ranked],
        "all_rows": [r.model_dump(mode="json") for r in rows],
        "errors": errors,
        "index_size": index_size(),
        "coverage": index_coverage(),
    }
