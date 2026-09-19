"""Tiny on-disk TTL cache.

Free public endpoints are rate-limited and occasionally flaky, so every raw
response is memoised. This keeps refreshes fast and dramatically reduces the
chance of being throttled.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from app.config import CACHE_DIR


def _key(namespace: str, key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)[:60]
    folder = CACHE_DIR / namespace
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{safe}.{digest}.json"


def get(namespace: str, key: str, ttl: int) -> Any | None:
    path = _key(namespace, key)
    if not path.exists():
        return None
    try:
        if ttl >= 0 and (time.time() - path.stat().st_mtime) > ttl:
            return None
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def put(namespace: str, key: str, value: Any) -> None:
    path = _key(namespace, key)
    tmp = path.with_suffix(".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False)
        tmp.replace(path)
    except (OSError, TypeError, ValueError):
        # Cache is an optimisation; never fail a request because of it.
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def clear(namespace: str | None = None) -> int:
    target = CACHE_DIR / namespace if namespace else CACHE_DIR
    removed = 0
    if not target.exists():
        return 0
    for path in target.rglob("*.json"):
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def stats() -> dict[str, int]:
    out: dict[str, int] = {}
    for folder in CACHE_DIR.iterdir() if CACHE_DIR.exists() else []:
        if folder.is_dir():
            out[folder.name] = sum(1 for _ in folder.glob("*.json"))
    return out
