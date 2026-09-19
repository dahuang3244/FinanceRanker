"""Central configuration.

Everything is environment-overridable so the same code runs locally, behind a
proxy, or on an overseas host without edits.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# In a PyInstaller bundle the source tree is unpacked to a temporary directory
# that is deleted on exit, so neither the bundled assets nor the user's data may
# live next to the code. `_MEIPASS` holds the read-only bundle; writable state
# goes to the per-user application directory for the platform.
FROZEN = getattr(sys, "frozen", False)
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
BASE_DIR = BUNDLE_DIR


def _user_data_dir() -> Path:
    """Per-user writable directory, following each platform's convention."""
    override = os.environ.get("FR_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "FinanceRanker"
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return Path(base or Path.home()) / "FinanceRanker"
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / "FinanceRanker"


DATA_DIR = _user_data_dir() if FROZEN else (BUNDLE_DIR / "data")
CACHE_DIR = DATA_DIR / "cache"
EXPORT_DIR = DATA_DIR / "exports"

# Creating these can fail on a read-only volume; the app must still start and
# report the problem rather than crash on import.
for _d in (DATA_DIR, CACHE_DIR, EXPORT_DIR):
    try:
        _d.mkdir(parents=True, exist_ok=True)
    except OSError:  # pragma: no cover - depends on the host filesystem
        pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FR_",
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------- server ----------
    host: str = "127.0.0.1"
    port: int = 8848

    # ---------- networking ----------
    # Empty string => let requests use the OS/env proxy (macOS system proxy included).
    # Set to e.g. "http://127.0.0.1:7897" to force a proxy, or to "direct" to bypass all proxies.
    proxy: str = ""
    http_timeout: int = 25
    max_retries: int = 4
    retry_base_delay: float = 1.0

    # Be polite to free public endpoints.
    request_delay: float = 0.15
    max_concurrency: int = 6

    # SEC requires a descriptive User-Agent with contact info.
    sec_user_agent: str = "FinanceRanker/1.0 (research; contact@example.com)"

    # ---------- provider toggles ----------
    # Yahoo geo-blocks whole regions (and datacenter egress), so the honest
    # default is adaptive rather than either always-on or always-off:
    #   yahoo_mode="auto" -> include Yahoo only when the startup probe reaches it
    #   yahoo_mode="on"   -> always include it
    #   yahoo_mode="off"  -> never touch it
    # `enable_yahoo` is the legacy override and still wins when set.
    enable_yahoo: bool = False
    yahoo_mode: str = "auto"
    enable_akshare: bool = True

    # ---------- cache ----------
    cache_ttl_price: int = 6 * 3600        # daily bars change slowly
    cache_ttl_quote: int = 5 * 60          # intraday quote
    cache_ttl_fundamentals: int = 12 * 3600
    cache_ttl_secmap: int = 24 * 3600

    # ---------- scheduling ----------
    # Set exactly one. `schedule_hours > 0` refreshes on an interval;
    # `schedule_cron` takes a standard 5-field crontab (UTC), e.g. "0 22 * * 1-5".
    schedule_hours: float = 0
    schedule_cron: str = ""

    # ---------- storage ----------
    db_path: Path = DATA_DIR / "finance_ranker.db"
    export_dir: Path = EXPORT_DIR

    # ---------- default universe (mirrors the workbook's Peer Data row 5) ----------
    default_tickers: str = "MSFT,AAPL,GOOGL,AMZN,META,NVDA,TSM,AMD,QCOM,AVGO,AMAT,MU,ORCL"

    # ---------- scoring weights (mirrors Scoring!AR39:AV39) ----------
    w_growth: float = 0.25
    w_profitability: float = 0.25
    w_cash: float = 0.20
    w_valuation: float = 0.20
    w_market: float = 0.10

    @property
    def ticker_list(self) -> list[str]:
        return [t.strip().upper() for t in self.default_tickers.split(",") if t.strip()]

    def proxies(self) -> dict[str, str] | None:
        """Resolve the proxy mapping for `requests`."""
        if self.proxy == "direct":
            return {}
        if self.proxy:
            return {"http": self.proxy, "https": self.proxy}
        return None  # defer to environment / OS settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

# akshare (and some other libs) build their own `requests.Session`, which honours the
# macOS *system* proxy. When a proxy is misconfigured or absent that surfaces as a
# confusing `ProxyError`, so pin the policy once, process-wide, before akshare loads.
if settings.proxy == "direct":
    os.environ.setdefault("NO_PROXY", "*")
    os.environ.setdefault("no_proxy", "*")
