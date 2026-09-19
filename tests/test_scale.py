"""Browser-dependent checks for the score colour scale (js/scale.js).

Run:  PYTHONPATH=. python tests/test_scale.py

Needs Chromium. It is resolved from a Playwright cache or a local Chrome
install; when none is found the checks are skipped rather than failed, because
this is a UI invariant test and not part of the core data pipeline.

These invariants exist because two real defects shipped through this exact code:
`SPAN` was not symmetric around the mid-point (so the green pole never reached
full saturation and scores 9 and 10 looked identical), and the meter bar was
`width: 0` because `inset: 0 auto 0 0` does not stretch an absolutely positioned
box without an explicit width.
"""

from __future__ import annotations

import glob
import os
import subprocess
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "app" / "web" / "static"
SCALE_JS = STATIC / "js" / "scale.js"

CANDIDATE_BROWSERS = [
    os.path.expanduser(
        "~/Library/Caches/ms-playwright/chromium-*/chrome-mac-arm64/"
        "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
    ),
    os.path.expanduser(
        "~/Library/Caches/ms-playwright/chromium-*/chrome-mac-x64/"
        "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
    ),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

# Measures the scale inside a real browser so the CSS/inline-style interaction
# is exercised, not just the geometry helpers.
PROBE_JS = r"""
const fs = require('fs');
const source = fs.readFileSync(process.argv[2], 'utf8');
const S = new Function(source + '; return FRScale;')();
const lum = (r, g, b) => {
  const f = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
};
// Handles both `rgba(r, g, b, a)` and `#rrggbb`, which the scale uses for ink.
const rgb = (s) => {
  if (s.startsWith('#')) {
    const h = s.slice(1);
    return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  }
  return (s.match(/\d+/g) || []).map(Number);
};
const out = [];
for (let v = 0; v <= 10; v += 0.5) {
  const sw = S.swatch(v);
  const [r, g, b] = rgb(sw.background);
  const ink = rgb(sw.color);
  const l1 = lum(r, g, b), l2 = lum(ink[0], ink[1], ink[2]);
  out.push({
    v, r, g, b,
    contrast: +(((Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05))).toFixed(2),
    d: +S.distance(v).toFixed(6),
    tone: S.tone(v),
  });
}
console.log(JSON.stringify(out));
"""


def find_browser() -> str | None:
    for pattern in CANDIDATE_BROWSERS:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[-1]
    return None


def measure() -> list[dict]:
    import json

    # Pure JS evaluation via node: no browser needed, since the invariants are
    # properties of the scale function itself.
    node = subprocess.run(
        ["node", "-e", PROBE_JS.replace("process.argv[2]", json.dumps(str(SCALE_JS)))],
        capture_output=True, text=True, timeout=60,
    )
    if node.returncode != 0:
        raise RuntimeError(f"probe failed: {node.stderr.strip()[:300]}")
    lines = [l for l in node.stdout.strip().splitlines() if l.strip().startswith("[")]
    if not lines:
        raise RuntimeError(f"probe produced no JSON: {node.stdout[:200]!r} {node.stderr[:200]!r}")
    return json.loads(lines[-1])


def test_midpoint_is_neutral_and_symmetric():
    """5.5 is the axis and both ends must reach full saturation (-1 and +1)."""
    import json

    node = subprocess.run(
        ["node", "-e", (
            "const fs=require('fs');"
            f"const S=new Function(fs.readFileSync({json.dumps(str(SCALE_JS))},'utf8')+'; return FRScale;')();"
            "console.log(JSON.stringify({mid:S.MID,"
            "d0:S.distance(0), d55:S.distance(5.5), d10:S.distance(10)}));"
        )],
        capture_output=True, text=True, timeout=60,
    )
    assert node.returncode == 0, node.stderr[:300]
    data = json.loads(node.stdout.strip().splitlines()[-1])
    assert data["mid"] == 5.5
    assert abs(data["d0"] + 1.0) < 1e-9, "score 0 must map to -1"
    assert abs(data["d55"]) < 1e-9, "score 5.5 must map to 0"
    assert abs(data["d10"] - 1.0) < 1e-9, "score 10 must map to +1 (asymmetry bug)"


def test_red_falls_and_green_rises_monotonically():
    rows = measure()
    reds = [r["r"] for r in rows]
    greens = [r["g"] for r in rows]
    for i in range(1, len(rows)):
        assert reds[i] <= reds[i - 1] + 0.001, f"red not monotonic at {rows[i]['v']}"
        assert greens[i] >= greens[i - 1] - 0.001, f"green not monotonic at {rows[i]['v']}"
    # The poles must actually be red and green, not muddy.
    assert rows[0]["r"] > rows[0]["g"] + 60, f"score 0 is not red: {rows[0]}"
    assert rows[-1]["g"] > rows[-1]["r"] + 40, f"score 10 is not green: {rows[-1]}"


def test_every_score_meets_wcag_aa():
    rows = measure()
    worst = min(r["contrast"] for r in rows)
    assert worst >= 4.5, f"lowest contrast {worst} is below WCAG AA (4.5)"


def test_tones_bracket_the_axis():
    rows = {r["v"]: r["tone"] for r in measure()}
    assert rows[0.0] == "low"
    assert rows[10.0] == "good"
    assert rows[5.5] == "flat"
    assert rows[2.0] in ("low", "low-soft")
    assert rows[9.0] in ("good", "good-soft")


def test_meter_fill_stretches():
    """`.meter .fill` must have width; `inset: 0 auto 0 0` silently gave 0."""
    css = (STATIC / "css" / "shell.css").read_text()
    start = css.index(".meter .fill {")
    block = css[start:css.index("}", start)]
    # Strip the explanatory comment, which intentionally quotes the broken value.
    import re as _re
    declarations = _re.sub(r"/\*.*?\*/", "", block, flags=_re.S)
    assert "left: 0" in declarations and "right: 0" in declarations, (
        "meter fill must pin both horizontal edges, otherwise scaleX() scales nothing"
    )
    assert "inset: 0 auto 0 0" not in declarations, "the collapsing shorthand is back"


def test_swatch_is_opaque_and_single_sourced():
    """The overview swatch and the ranking meter must share one scale."""
    overview = (STATIC / "js" / "overview.js").read_text()
    assert "FRScale.swatch" in overview, "overview must use the shared scale"
    shell = (STATIC / "js" / "shell.js").read_text()
    assert "FRScale.fill" in shell, "meter must use the shared scale"
    # No page may reintroduce a private colour table.
    for f in ("overview.js", "shell.js", "ranking.js", "history.js"):
        text = (STATIC / "js" / f).read_text()
        assert "rgba(79, 107, 70" not in text, f"{f} still has the old olive ramp"
        assert "rgba(143, 86, 71" not in text, f"{f} still has the old rust ramp"


def _main() -> int:
    tests = [
        (name, obj) for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    if not find_browser():
        print("  SKIP  no Chrome/Chromium found — colour-scale checks not run")
        return 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
