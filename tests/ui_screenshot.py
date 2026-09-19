"""Headless screenshot + console-error check for the FinanceRanker UI.

Dev-only helper (not part of the app): loads each page from a running local
server, writes full-page screenshots and prints any console/page errors.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8848"
OUT = Path("/tmp/frshots")
PAGES = [
    "index.html", "refresh.html", "ranking.html", "history.html", "sources.html",
    "company.html?ticker=NVDA",
]

# Prefer the local Chrome-for-Testing build; Playwright's own pinned revision may
# not be downloaded on this machine.
CANDIDATES = [
    Path.home() / "Library/Caches/ms-playwright/chromium-1217/chrome-mac-arm64/"
    "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
]


def chrome_path() -> str | None:
    for path in CANDIDATES:
        if path.exists():
            return str(path)
    return None


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    failures = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chrome_path())
        for size, suffix in (((1440, 1000), ""), ((420, 900), "-mobile")):
            context = browser.new_context(viewport={"width": size[0], "height": size[1]},
                                          device_scale_factor=2)
            for page_name in PAGES:
                page = context.new_page()
                errors: list[str] = []
                page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                        if m.type in ("error", "warning") else None)
                page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
                page.goto(f"{BASE}/{page_name}", wait_until="networkidle")
                page.wait_for_timeout(900)
                stem = page_name.split("?")[0].replace(".html", "")
                if "?" in page_name:
                    stem += "-" + page_name.split("ticker=")[-1].lower()
                target = OUT / f"{stem}{suffix}.png"
                page.screenshot(path=str(target), full_page=True)
                flag = "FAIL" if errors else "ok  "
                print(f"[{flag}] {page_name}{suffix} -> {target}")
                for e in errors:
                    failures += 1
                    print(f"        {e}")
                page.close()
            context.close()
        browser.close()
    print(f"\nscreenshots in {OUT}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
