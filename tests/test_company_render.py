"""Render checks for the company detail page (js/company-view.js).

Run:  PYTHONPATH=. python tests/test_company_render.py

Loads the page's real scripts in Node with a minimal DOM stub and renders one
synthetic row, so the check exercises the shipped markup rather than a copy of
it. Node is required; without it the checks are skipped rather than failed,
because this is a UI invariant test and not part of the data pipeline.

Why this exists: every row in the five dimension tables rendered its 指标 / metric
cell from `metric.label` and `metric.en`, which `DIMENSIONS` never defines -- so
the metric column on the single-ticker page was blank for *every* stock, while
the values, peer medians and scores beside it were all fine. The names live in
the shared i18n dictionary (`m.<attr>`), reached through `labelFor`/`subLabelFor`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "app" / "web" / "static"

# A full row so no metric is skipped, with values in the shapes the page expects
# (ratios as fractions, multiples as x, money in units).
ROW = {
    "ticker": "MSFT", "company": "Microsoft Corporation", "currency": "USD",
    "filing_currency": "USD",
    "status": "Refreshed", "notes": "SEC XBRL (US-GAAP) annual facts.",
    "price": 493.78, "market_cap": 3.6e12, "revenue_fy0": 331_839_000_000,
    "revenue_growth_yoy": 0.18, "revenue_cagr_5y": 0.14, "eps_growth_fy1": 0.12,
    "eps_cagr_5y": 0.15, "gross_margin": 0.68, "operating_margin": 0.47,
    "net_margin": 0.40, "roe": 0.30, "roic": 0.25, "fcf_margin": 0.25,
    "fcf_yield": 0.025, "ocf_to_net_income": 1.37, "forward_pe": 27.5,
    "price_to_sales": 11.0, "ev_to_ebitda": 18.0, "price_to_book": 8.0,
    "price_to_fcf": 40.0, "return_1y": 0.2, "debt_to_assets": 0.45,
    "drawdown_52w": -0.1, "beta": 0.9, "gaap_eps": 17.95,
    "score_overall": 7.5, "score_growth": 7.0, "score_profitability": 8.0,
    "score_cash": 7.0, "score_valuation": 6.0, "score_market": 7.0,
    "data_coverage": 21, "rank": 1, "rank_eligible": True, "profile": "Strong",
    "fiscal_end": "2026-06-30", "fetched_at": "2026-09-01T12:00:00",
    "revenue_fy_minus_1": 281_724_000_000, "diluted_shares_fy0": 7_453_000_000,
    "share_count": 7_425_545_491, "effective_tax_rate": 0.19,
}

# Metric names that must appear as the row label of their dimension table. The
# second element is the other language's secondary line, which `subLabelFor`
# supplies; both are checked so a half-fix cannot pass.
EXPECTED_ZH = {
    "revenue_growth_yoy": ("营收同比", "Revenue growth YoY"),
    "revenue_cagr_5y": ("营收 5Y CAGR", "Revenue CAGR 5Y"),
    "eps_growth_fy1": ("FY1 EPS 增速", "FY1 EPS growth"),
    "eps_cagr_5y": ("GAAP EPS 5Y CAGR", "GAAP EPS CAGR 5Y"),
    "gross_margin": ("毛利率", "Gross margin"),
    "operating_margin": ("营业利润率", "Operating margin"),
    "net_margin": ("净利率", "Net margin"),
    "roe": ("ROE", "ROE"),
    "roic": ("ROIC", "ROIC"),
    "fcf_margin": ("FCF 利润率", "FCF margin"),
    "fcf_yield": ("FCF 收益率", "FCF yield"),
    "ocf_to_net_income": ("现金转化", "Cash conversion"),
    "forward_pe": ("Forward 12m P/E", "Forward 12m P/E"),
    "price_to_sales": ("P/S", "Price / sales"),
    "ev_to_ebitda": ("EV/EBITDA", "EV / EBITDA"),
    "price_to_book": ("P/B", "Price / book"),
    "price_to_fcf": ("P/FCF", "Price / FCF"),
    "return_1y": ("1 年回报", "1Y return"),
    "debt_to_assets": ("资产负债率", "Debt / assets"),
    "drawdown_52w": ("52 周回撤", "52W drawdown"),
    "beta": ("Beta", "Beta"),
}

PROBE_JS = r"""
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const dir = __STATIC__;

// Minimal DOM: enough for the three scripts to load, and `readyState` is left
// as "loading" so shell.js registers its boot handler instead of running it.
const ctx = {
  console,
  setTimeout: () => 0,
  setInterval: () => 0,
  clearInterval: () => {},
  navigator: { language: 'zh-CN', languages: ['zh-CN'] },
  localStorage: {
    store: {},
    getItem(k) { return Object.prototype.hasOwnProperty.call(this.store, k) ? this.store[k] : null; },
    setItem(k, v) { this.store[k] = String(v); },
    removeItem(k) { delete this.store[k]; },
  },
  location: { search: '', href: 'http://127.0.0.1/company.html', pathname: '/company.html' },
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  addEventListener() {},
  removeEventListener() {},
  document: {
    readyState: 'loading',
    documentElement: { lang: 'zh-CN', dataset: {}, style: {}, setAttribute() {} },
    addEventListener() {},
    removeEventListener() {},
    querySelector: () => null,
    querySelectorAll: () => [],
    getElementById: () => null,
    createElement: () => ({
      style: {}, dataset: {}, classList: { add() {}, remove() {} },
      setAttribute() {}, appendChild() {}, remove() {}, addEventListener() {},
    }),
    body: { dataset: { page: 'co' }, appendChild() {}, insertBefore() {}, firstChild: null },
  },
};
ctx.window = ctx;

// A vm context has the language built-ins but none of the host's web globals,
// so the handful the page scripts touch are borrowed from Node itself.
for (const name of ['URLSearchParams', 'URL', 'TextEncoder', 'TextDecoder',
                    'AbortController', 'structuredClone', 'Intl']) {
  if (globalThis[name] !== undefined) ctx[name] = globalThis[name];
}
ctx.fetch = () => Promise.reject(new Error('network is not available in this test'));
ctx.requestAnimationFrame = () => 0;
ctx.getComputedStyle = () => ({ getPropertyValue: () => '' });
ctx.alert = () => {};
for (const name of ['IntersectionObserver', 'MutationObserver', 'ResizeObserver']) {
  ctx[name] = class { observe() {} unobserve() {} disconnect() {} };
}

vm.createContext(ctx);

for (const file of ['js/i18n.js', 'js/shell.js', 'js/company-view.js']) {
  const source = fs.readFileSync(path.join(dir, file), 'utf8');
  const name = { 'js/i18n.js': 'FRI18n', 'js/shell.js': 'FR', 'js/company-view.js': 'FRCompany' }[file];
  vm.runInContext(source + '\n;globalThis.__' + name + ' = ' + name + ';', ctx, { filename: file });
}

const row = __ROW__;
// Wrapped in an IIFE: top-level `const` in a vm script lands in the context's
// lexical scope and would collide on the second render.
const script = `
  (() => {
    const html = __NAME__.render(row, { rankedNeighbours: [row] });
    return JSON.stringify({
      html,
      dims: __NAME__.DIMENSIONS.flatMap((d) => d.metrics.map((m) => m.attr)),
    });
  })()
`;
const render = (lang) => {
  ctx.row = row;
  ctx.__NAME__ = ctx.__FRCompany;
  ctx.__FRI18n.set(lang === 'zh' ? 'zh' : 'en');
  return JSON.parse(vm.runInContext(script, ctx));
};

console.log(JSON.stringify({ zh: render('zh'), en: render('en') }));
"""


def node_available() -> bool:
    return shutil.which("node") is not None


def probe() -> dict:
    js = (
        PROBE_JS.replace("__STATIC__", json.dumps(str(STATIC)))
        .replace("__ROW__", json.dumps(ROW))
        .replace("__NAME__", "__FRCompany")
    )
    result = subprocess.run(
        ["node", "-e", js],
        capture_output=True,
        text=True,
        # The rendered markup is Chinese, and the default locale codec on a
        # zh-CN Windows host is GBK, which would fail to decode it.
        encoding="utf-8",
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"probe failed: {result.stderr.strip()[:600]}")
    lines = [l for l in result.stdout.strip().splitlines() if l.strip().startswith("{")]
    if not lines:
        raise RuntimeError(f"probe produced no JSON: {result.stdout[:300]!r}")
    return json.loads(lines[-1])


def test_every_dimension_metric_has_a_name_in_both_languages():
    """The 指标 column was blank for every row; every metric must be labelled."""
    data = probe()
    assert data["zh"]["dims"], "no metrics were discovered"

    for lang, expected in (("zh", EXPECTED_ZH), ("en", None)):
        html = data[lang]["html"]
        for attr in data[lang]["dims"]:
            assert f'data-attr="{attr}"' in html, f"{attr} row missing from the {lang} render"
        for attr, names in EXPECTED_ZH.items():
            zh, en = names
            wanted = (zh, en) if lang == "zh" else (en, zh)
            for name in wanted:
                assert name in html, f"{lang}: {attr} is missing its label {name!r}"
            # The label must be the cell's own content, not a stray occurrence.
            assert f"<b>{wanted[0]}</b>" in html, (
                f"{lang}: {attr} label {wanted[0]!r} is not inside the metric cell"
            )


def test_no_metric_cell_renders_empty_or_undefined():
    html = probe()["zh"]["html"]
    assert "<b></b>" not in html, "a metric name cell is empty"
    assert "undefined" not in html, "an undefined value leaked into the markup"


def test_missing_values_still_show_a_dash():
    """Labelling must not hide genuinely missing figures."""
    html = _render_zh({**ROW, "roe": None})
    assert 'data-attr="roe"' in html
    assert "is-missing" in html, "a row with no value must still be flagged as missing"
    assert "<b>ROE</b>" in html, "the label must survive a missing value"


def test_cross_currency_filings_are_labelled():
    """The badge must key on filing vs trading currency, not on 'is it USD'.

    TSM's ADR trades in USD while its 20-F is filed in TWD, so a
    `currency !== "USD"` test never fired for the one case it was written for,
    leaving TWD amounts (revenue 2.89e12, EPS 44.67) looking like US dollars.
    """
    same = _render_zh(ROW)
    assert "以 TWD 申报" not in same and "下列金额单位为" not in same

    tsmlike = _render_zh({**ROW, "ticker": "TSM", "currency": "USD", "filing_currency": "TWD"})
    assert "以 TWD 申报" in tsmlike, "the filing currency badge is missing"
    assert "下列金额单位为 TWD" in tsmlike, "the raw inputs are not labelled with their currency"


def test_unconfirmed_filing_currency_is_not_shown_as_a_currency():
    """`UNKNOWN` is the provider's "could not establish" sentinel, not a code."""
    html = _render_zh({**ROW, "ticker": "TSM", "currency": "USD", "filing_currency": "UNKNOWN"})
    assert "申报币种未确认" in html
    assert "金额单位未确认" in html
    assert "以 UNKNOWN 申报" not in html


def _render_zh(row: dict) -> str:
    js = (
        PROBE_JS.replace("__STATIC__", json.dumps(str(STATIC)))
        .replace("__ROW__", json.dumps(row))
        .replace("__NAME__", "__FRCompany")
    )
    result = subprocess.run(
        ["node", "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=120
    )
    assert result.returncode == 0, result.stderr[:400]
    return json.loads(result.stdout.strip().splitlines()[-1])["zh"]["html"]


def _main() -> int:
    if not node_available():
        print("  SKIP  node not found — company-page render checks not run")
        return 0
    tests = [
        (name, obj) for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
