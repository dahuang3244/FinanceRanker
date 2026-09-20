// Render the real supplemental company panel in both languages, including untrusted headlines.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const host = { innerHTML: '' };
const fixture = {
  technical: { close: 100, ma20: 95, ma50: 90, ma200: 80, rsi14: 55,
    high_52w: 110, from_high: -0.091, as_of: '2026-09-18', source: 'Sina', adjusted: false },
  report: { fiscal_end: '2025-12-31', next_earnings_date: null,
    revenue_growth: 0.12, roe: 0.25, fcf_margin: 0.15, source: 'SEC' },
  risk: { drawdown_52w: -0.091, debt_to_assets: 0.1, beta: 1.3, market_source: 'Sina' },
  news: { source: 'Google News RSS', temperature: null, articles: [
    { title: '<img src=x onerror=alert(1)>', url: 'javascript:alert(1)', published: '2026-09-18' },
    { title: '<img src=x onerror=alert(1)>', url: 'https://news.google.com/example', published: '2026-09-18' },
  ] },
  facets: Object.fromEntries(['profit', 'growth', 'valuation', 'recent', 'risk'].map(key => [key,
    { score: 4.5, count: 2, total: 3 }])),
  return_3m: 0.12,
};
const context = {
  URLSearchParams, URL, console,
  navigator: { language: 'zh-CN' },
  localStorage: { getItem: () => null, setItem: () => {} },
  location: { search: '?ticker=TSM' },
  document: { documentElement: { dataset: {} }, body: { dataset: {} },
    querySelector: selector => selector === '#companyInsights' ? host : null,
    querySelectorAll: () => [] },
  FR: { escapeHtml: value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;').replaceAll('"', '&quot;') },
  fetch: async () => ({ ok: true, json: async () => fixture }),
};
vm.createContext(context);
context.window = context;
// Resolve the app sources from the repository root so this test works no
// matter which directory it is invoked from.
const repoRoot = require('node:path').join(__dirname, '..');
for (const [path, name] of [['app/web/static/js/i18n.js', 'FRI18n'],
                             ['app/web/static/js/company-insights.js', 'FRInsights']]) {
  vm.runInContext(
    fs.readFileSync(require('node:path').join(repoRoot, path), 'utf8')
      + `;globalThis.${name}Ref=${name}`,
    context,
  );
}
(async () => {
  await context.FRInsightsRef.load();
  assert.match(host.innerHTML, /趋势结构/);
  assert.match(host.innerHTML, /3 个月回报/);
  assert.match(host.innerHTML, /&lt;img/);
  assert.doesNotMatch(host.innerHTML, /href="javascript:/);
  context.FRI18nRef.set('en');
  context.FRInsightsRef.render();
  assert.match(host.innerHTML, /Trend structure/);
  assert.match(host.innerHTML, /3-month return/);
  console.log('Company insights render in both languages with escaped news.');
})().catch(error => { console.error(error); process.exitCode = 1; });
