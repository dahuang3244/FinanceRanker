"""Provider reachability probe.

"No data source works everywhere" is a fact of this problem domain: Yahoo
geo-blocks whole regions, Eastmoney's push2 load-balancers are unreachable from
some networks, the SEC demands a descriptive User-Agent, and a stale system
proxy can break every request at once. What *can* be guaranteed is that the app
detects which sources are reachable **in the environment it is actually running
in** and reorders its failover chain accordingly, instead of discovering the
same outage on every ticker.

Results are cached so a launch costs a handful of small requests, and the probe
can be re-run on demand (`POST /api/providers/probe`).
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from app import cache
from app.config import settings
from app.http import FetchError, fetch

log = logging.getLogger(__name__)

NS = "provider_health"
TTL = 3600          # re-probe hourly at most
PROBE_TICKER = "MSFT"


def _probe_sec() -> tuple[bool, str]:
    """SEC needs the declared User-Agent, so this also validates that config."""
    try:
        data = fetch(
            "https://data.sec.gov/api/xbrl/companyconcept/CIK0000789019/us-gaap/Revenues.json",
            headers={"User-Agent": settings.sec_user_agent},
            expect_json=True,
            timeout=15,
            retries=1,
        )
    except FetchError as exc:
        return False, str(exc)[:120]
    ok = isinstance(data, dict) and "units" in data
    return ok, "ok" if ok else "unexpected payload"


def _probe_sina() -> tuple[bool, str]:
    from app.providers.prices import SINA_US_DAILY

    try:
        text = fetch(
            SINA_US_DAILY.format(symbol=PROBE_TICKER),
            headers={"Referer": "https://finance.sina.com.cn/"},
            expect_json=False,
            timeout=15,
            retries=1,
        )
    except FetchError as exc:
        return False, str(exc)[:120]
    # The response is a JSONP array of daily bars. Sina does not echo the method
    # name back, so assert on the payload shape, not on the request string.
    ok = isinstance(text, str) and "x([" in text and '"d":"' in text and len(text) > 500
    return ok, "ok" if ok else "unexpected payload"


def _probe_tencent() -> tuple[bool, str]:
    from app.providers.quotes import TENCENT_QUOTE

    try:
        text = fetch(
            TENCENT_QUOTE.format(ticker=PROBE_TICKER),
            headers={"Referer": "https://gu.qq.com/"},
            expect_json=False,
            timeout=15,
            retries=1,
        )
    except FetchError as exc:
        return False, str(exc)[:120]
    ok = isinstance(text, str) and text.count("~") > 30
    return ok, "ok" if ok else "unexpected payload"


def _probe_akshare() -> tuple[bool, str]:
    """The akshare facade is probed through its Eastmoney financial endpoint."""
    from app.providers.akshare_us import AkshareUnavailable, get_analysis

    try:
        frame = get_analysis(PROBE_TICKER)
    except AkshareUnavailable as exc:
        return False, str(exc)[:120]
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:120]
    ok = frame is not None and not frame.empty
    return ok, "ok" if ok else "empty frame"


def _probe_eastmoney() -> tuple[bool, str]:
    try:
        data = fetch(
            "https://push2.eastmoney.com/api/qt/stock/get",
            params={"secid": f"105.{PROBE_TICKER}", "fields": "f43,f58"},
            headers={"Referer": "https://quote.eastmoney.com/"},
            expect_json=True,
            impersonate=True,
            timeout=15,
            retries=1,
        )
    except FetchError as exc:
        return False, str(exc)[:120]
    ok = bool((data or {}).get("data"))
    return ok, "ok" if ok else "no data"


def _probe_yahoo() -> tuple[bool, str]:
    try:
        data = fetch(
            "https://query1.finance.yahoo.com/v8/finance/chart/AAPL",
            params={"range": "5d", "interval": "1d"},
            expect_json=True,
            impersonate=True,
            timeout=15,
            retries=1,
        )
    except FetchError as exc:
        return False, str(exc)[:120]
    try:
        ok = bool(data["chart"]["result"])
    except (KeyError, IndexError, TypeError):
        ok = False
    return ok, "ok" if ok else "unexpected payload"


PROBES: dict[str, tuple[str, callable]] = {
    "sec": ("年度财报（SEC XBRL）", _probe_sec),
    "sina": ("日线行情（新浪财经）", _probe_sina),
    "tencent": ("实时报价（腾讯）", _probe_tencent),
    "akshare": ("财经数据聚合（akshare/东方财富）", _probe_akshare),
    "eastmoney": ("行情备用（东方财富 push2）", _probe_eastmoney),
    "yahoo": ("雅虎财经（可选）", _probe_yahoo),
}


def _cached() -> dict | None:
    return cache.get(NS, "probe", TTL)


def results(*, force: bool = False) -> dict:
    """Probe every provider (or return the cached result)."""
    if not force:
        hit = _cached()
        if hit:
            return hit

    started = time.perf_counter()
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=len(PROBES)) as pool:
        futures = {pool.submit(fn): key for key, (_, fn) in PROBES.items()}
        for future in futures:
            key = futures[future]
            label = PROBES[key][0]
            try:
                ok, detail = future.result()
            except Exception as exc:  # pragma: no cover - defensive
                ok, detail = False, f"{type(exc).__name__}: {exc}"[:120]
            out[key] = {"key": key, "label": label, "ok": ok, "detail": detail}
            if key == "yahoo" and not settings.enable_yahoo:
                # Probed for information only; it is not part of the chain.
                out[key]["disabled"] = True

    payload = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "elapsed_s": round(time.perf_counter() - started, 2),
        "providers": out,
        "reachable": [k for k, v in out.items() if v["ok"]],
    }
    cache.put(NS, "probe", payload)
    log.info(
        "provider probe: %s",
        ", ".join(f"{k}={'ok' if v['ok'] else 'down'}" for k, v in out.items()),
    )
    return payload


def reachable(key: str, *, force: bool = False) -> bool:
    """Whether a provider answered the last probe (optimistic before one runs)."""
    try:
        return bool(results(force=force)["providers"].get(key, {}).get("ok"))
    except Exception:
        return True


def yahoo_usable() -> bool:
    """Resolve the tri-state Yahoo policy against the last probe.

    `enable_yahoo` (legacy bool) wins when set, then `yahoo_mode`.
    """
    from app.config import settings as cfg

    if cfg.enable_yahoo:
        return True
    mode = (cfg.yahoo_mode or "auto").strip().lower()
    if mode in ("off", "false", "no", "0"):
        return False
    if mode in ("on", "true", "yes", "1"):
        return True
    return reachable("yahoo")           # "auto"
