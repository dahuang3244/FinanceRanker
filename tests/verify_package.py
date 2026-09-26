"""Verify the shareable zip the way a recipient experiences it.

Checks structure, required files, that the packaged exe matches the build we
just made, and then actually extracts and runs it with a clean data directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROOT = Path(
    r"C:\Users\Lenovo\OneDrive - Nanyang Technological University\Desktop"
    r"\FinanceRanker-v0.1.1-Windows-"
)
ZIP = ROOT / "FinanceRanker-0.1.1-portable.zip"
BUILT_EXE = REPO / "dist" / "FinanceRanker" / "FinanceRanker.exe"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:16]


print(f"zip: {ZIP.name}  ({ZIP.stat().st_size / 1e6:.0f} MB)")
with zipfile.ZipFile(ZIP) as z:
    names = [n.replace("\\", "/") for n in z.namelist()]

    tops = sorted({n.split("/")[0] for n in names})
    print(f"top level: {tops}")
    assert tops == ["FinanceRanker"], f"unexpected top level: {tops}"
    assert not [n for n in names if n.startswith("FinanceRanker/FinanceRanker/")], "nested folder"

    for want in [
        "FinanceRanker/FinanceRanker.exe",
        "FinanceRanker/READ-ME-FIRST.txt",
        "FinanceRanker/_internal/chromium/chrome.exe",
    ]:
        assert want in names, f"missing {want}"
    print("required files: OK")

    # The exe inside the zip must be the build we just made, not a stale one.
    if BUILT_EXE.exists():
        with tempfile.TemporaryDirectory() as tmp:
            z.extract("FinanceRanker/FinanceRanker.exe", tmp)
            zip_exe = Path(tmp) / "FinanceRanker" / "FinanceRanker.exe"
            assert sha(zip_exe) == sha(BUILT_EXE), (
                f"zip carries a STALE exe: {sha(zip_exe)} vs build {sha(BUILT_EXE)}"
            )
        print(f"packaged exe matches the current build ({sha(BUILT_EXE)}): OK")
    else:
        print("!! build exe not found for comparison")

    text = z.read("FinanceRanker/READ-ME-FIRST.txt").decode("utf-8")
    for needle, why in [
        ("抓取与排名", "tells them how to refresh"),
        ("里面已经带好一份数据", "explains that data ships with the copy"),
        ("需要联网", "states the network requirement"),
        ("_internal", "warns not to delete _internal"),
        ("data 文件夹", "says where data lives and that it travels with the folder"),
    ]:
        assert needle in text, f"readme missing: {why}"
    print("readme covers the first-run pitfalls: OK")

with tempfile.TemporaryDirectory(prefix="fr-recipient-") as tmp:
    with zipfile.ZipFile(ZIP) as z:
        z.extractall(tmp)
    exe = Path(tmp) / "FinanceRanker" / "FinanceRanker.exe"
    data_dir = Path(tmp) / "FinanceRanker" / "data"
    # Do NOT set FR_DATA_DIR here: the portable directory beside the exe is what
    # a recipient gets, and an override would mask whether the snapshot shipped.
    env = {k: v for k, v in os.environ.items() if k != "FR_DATA_DIR"}
    print("\nlaunching the extracted copy exactly as a recipient would...")
    proc = subprocess.Popen([str(exe), "--no-window"], env=env)
    log = data_dir / "launcher.log"
    port = None
    deadline = time.time() + 150
    while time.time() < deadline:
        time.sleep(2)
        if log.exists():
            for line in log.read_text(errors="replace").splitlines():
                marker = "serving at http://127.0.0.1:"
                if marker in line:
                    port = line.split(marker, 1)[1].split("/", 1)[0].strip()
                    break
        if port or proc.poll() is not None:
            break
    try:
        assert port, "the extracted app never started serving"
        print(f"  serving on port {port}")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=60) as r:
            health = json.load(r)
        print(f"  health: {health['status']}")
        print(f"  providers reachable: {', '.join(health['probe']['reachable'])}")
        assert health["probe"]["reachable"], "no data provider reachable"
        print(f"  store: runs={health['store']['runs']} snapshots={health['store']['snapshots']}")

        # A packaged copy ships a pre-refreshed snapshot beside the exe so the
        # recipient sees a populated app. That is the point of the portable data
        # directory, so assert it rather than tolerating an empty screen.
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ranking", timeout=90) as r:
            ranking = json.load(r)
        rows = ranking.get("rows", [])
        assert rows, "packaged copy has no snapshot — the recipient would see an empty app"
        print(f"  bundled snapshot: {len(rows)} rows, metrics_version={ranking.get('metrics_version')}")
        assert ranking.get("staleness", {}).get("stale") is False, (
            f"the bundled snapshot is stale: {ranking.get('staleness')}"
        )
        first = rows[0]
        # The annual bridge is deliberately withheld from the ranking payload now;
        # the company page derives its reconciliation per quarter from /api/eps, so
        # the check has moved to that endpoint below and to the columns that remain.
        assert first.get("non_gaap_eps") is not None, "non-GAAP EPS is not populated"
        assert "non_gaap" not in first, (
            "the annual bridge is still being shipped; a page running older "
            "JavaScript would render it unlabelled as a quarterly reconciliation"
        )
        print(f"  non-GAAP EPS bundled: {first['ticker']} {first['non_gaap_eps']:.2f} "
              f"(annual bridge withheld as intended)")

        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/eps/{first['ticker']}", timeout=180) as r:
            eps = json.load(r)
        assert eps.get("available"), f"quarterly EPS unavailable for {first['ticker']}"
        quarters = eps.get("quarters") or []
        assert quarters, "the quarterly endpoint returned nothing"
        newest = quarters[0]
        assert newest.get("label"), "a quarter has no period label"
        assert newest.get("gaap_eps") is not None, "a quarter has no GAAP EPS"
        assert newest.get("adjusted_net_income") is not None, (
            "a quarter has no derivation chain — adjusted net income is missing"
        )
        print(f"  quarterly EPS bundled: {len(quarters)} quarters, newest {newest['label']} "
              f"GAAP {newest['gaap_eps']}")

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/analyst/TSM", timeout=90) as r:
            analyst = json.load(r)
        assert analyst.get("available"), "analyst endpoint not working in this build"
        print(f"  analyst endpoint works (target {analyst.get('target_mean')})")

        # The options workspace, so check its assets and endpoint — the easiest
        # thing to forget to ship.
        for asset in ("options.html", "js/options.js", "css/options.css"):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/{asset}", timeout=30) as r:
                served = r.read()
            assert len(served) > 500, f"{asset} is empty"
        print("  options page assets served")

        # Stale scripts are a real failure mode: the app upgrades in place on a
        # stable port, so an unversioned page can keep calling the previous
        # build's API. HTML must be uncached and its assets versioned.
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/options.html", timeout=30) as r:
            page = r.read().decode("utf-8")
            cache_control = r.headers.get("cache-control", "")
        assert "no-store" in cache_control, f"HTML is cacheable ({cache_control!r})"
        stamps = set(re.findall(r"\?v=([0-9a-f]{6,})", page))
        assert stamps, "no versioned asset references on the page"
        print(f"  assets versioned (stamp {sorted(stamps)[0]}) and HTML uncached")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/options/AAPL", timeout=180) as r:
            options_payload = json.load(r)
        if options_payload.get("available"):
            assert options_payload.get("summaries"), "option chain returned no expiries"
            assert options_payload.get("totals", {}).get("volume_pcr") is not None
            print(f"  options endpoint works (AAPL spot {options_payload.get('spot')}, "
                  f"max pain {options_payload.get('max_pain')})")
        else:
            # A provider outage is acceptable; silence is not.
            assert options_payload.get("reason"), "unavailable must explain itself"
            print(f"  options endpoint reported: {options_payload['reason']}")

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/strategies", timeout=30) as r:
            strategies = json.load(r)
        print(f"  weight presets: {len(strategies['strategies'])}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()

# The portable database must ship inside the zip, or none of the above matters.
with zipfile.ZipFile(ZIP) as z:
    names = [n.replace("\\", "/") for n in z.namelist()]
    bundled_db = [n for n in names if n.startswith("FinanceRanker/data/") and n.endswith(".db")]
    print(f"\nbundled database: {bundled_db or 'NONE'}")
    assert bundled_db, "no database inside the zip — the recipient opens an empty app"

print("\nPACKAGE VERIFIED — unzip, open the folder, double-click FinanceRanker.exe; data is already there")
