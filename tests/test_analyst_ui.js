// The analyst section must render from a real payload shape, in both languages,
// and must state its own data limits rather than implying a longer history.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.join(__dirname, '..');

// A payload shaped exactly like `GET /api/analyst/{ticker}` returns.
const PAYLOAD = {
  ticker: 'TSM',
  available: true,
  target_mean: 552.2594, target_median: 538.5, target_high: 700, target_low: 440,
  recommendation: 'strong_buy', recommendation_mean: 1.38095, current_price: 434.67,
  next_earnings_date: '2026-10-15',
  ratings: [
    { period: '0m', strong_buy: 6, buy: 14, hold: 1, sell: 0, strong_sell: 0 },
    { period: '-1m', strong_buy: 6, buy: 12, hold: 1, sell: 0, strong_sell: 0 },
    { period: '-3m', strong_buy: 5, buy: 12, hold: 2, sell: 0, strong_sell: 0 },
  ],
  actions: [
    { date: '2026-09-02', firm: 'Stifel', action: 'init', from_grade: null, to_grade: 'Buy',
      price_target_action: 'Announces', price_target: 515, prior_price_target: 0 },
    { date: '2026-08-14', firm: 'Morgan Stanley', action: 'up', from_grade: 'Equal-Weight',
      to_grade: 'Overweight', price_target_action: 'Raises', price_target: 588, prior_price_target: 520 },
  ],
  earnings_history: [
    { period: '-4q', quarter_end: '2025-09-30', eps_actual: 2.92, eps_estimate: 2.63,
      eps_difference: 0.29, surprise_pct: 0.1123, currency: 'USD' },
    { period: '-3q', quarter_end: '2025-12-31', eps_actual: 3.14, eps_estimate: 2.98,
      eps_difference: 0.16, surprise_pct: 0.0553, currency: 'USD' },
    { period: '-2q', quarter_end: '2026-03-31', eps_actual: 3.49, eps_estimate: 3.33,
      eps_difference: 0.16, surprise_pct: 0.0468, currency: 'USD' },
    { period: '-1q', quarter_end: '2026-06-30', eps_actual: 4.31, eps_estimate: 3.89,
      eps_difference: 0.42, surprise_pct: 0.1089, currency: 'USD' },
  ],
  estimates: [
    { period: '0q', end_date: '2026-09-30', eps_avg: 4.4614, eps_low: 4.2, eps_high: 4.7,
      eps_year_ago: 2.92, analyst_count: 9, growth: 0.5279, revenue_avg: null },
    { period: '0y', end_date: '2026-12-31', eps_avg: 16.93, eps_low: 16, eps_high: 18,
      eps_year_ago: 10.6, analyst_count: 13, growth: 0.59, revenue_avg: null },
  ],
  source: 'Yahoo quoteSummary (crumb-authenticated)',
  as_of: '2026-09-20T14:00:00',
  notes: ['Yahoo publishes four quarters of reported-vs-consensus EPS.'],
};

function context() {
  const host = { innerHTML: '' };
  const ctx = {
    URLSearchParams,
    navigator: { language: 'zh-CN' },
    localStorage: { getItem: () => null, setItem: () => {} },
    window: { matchMedia: () => ({ matches: false }) },
    document: {
      documentElement: { dataset: {} },
      body: { dataset: {} },
      querySelectorAll: () => [],
      getElementById: (id) => (id === 'analystSection' ? host : null),
    },
    FR: {
      api: { analyst: async () => PAYLOAD },
      icon: () => '<svg></svg>',
      escapeHtml: (v) => (v == null ? '' : String(v).replace(/[&<>"]/g, (c) =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))),
      num: (v) => (v == null ? '—' : String(v)),
      money: (v) => (v == null ? '—' : Number(v).toFixed(2)),
      when: String, tone: () => 'mid', meter: () => '', profilePill: () => '',
      profileLabel: () => '', DASH: '—', fmt: String, toast: () => {}, stateBlock: () => '',
      $: () => null, $$: () => [],
    },
    _host: host,
  };
  vm.createContext(ctx);
  for (const file of ['app/web/static/js/i18n.js', 'app/web/static/js/analyst.js']) {
    vm.runInContext(fs.readFileSync(path.join(repoRoot, file), 'utf8')
      + `;globalThis.${file.includes('i18n') ? 'FRI18n' : 'FRAnalyst'} = ${
        file.includes('i18n') ? 'FRI18n' : 'FRAnalyst'};`, ctx);
  }
  return ctx;
}

(async () => {
  const ctx = context();
  ctx.t = ctx.FRI18n.t;

  await ctx.FRAnalyst.mount('TSM');
  const zh = ctx._host.innerHTML;
  assert.ok(zh.length > 200, 'section rendered nothing');
  assert.match(zh, /市场分析师/, 'Chinese title missing');
  assert.match(zh, /分析师平均目标价/, 'mean-target label missing');
  assert.match(zh, /评级分布/, 'rating distribution heading missing');
  assert.match(zh, /<svg/, 'charts must render as inline SVG');
  assert.match(zh, /共 22 家机构|共 2[0-9] 家机构/, 'analyst coverage count missing');
  assert.match(zh, /Stifel/, 'published actions table missing');
  assert.match(zh, /Yahoo publishes four quarters/, 'data-limit note must be surfaced');
  // A beat is a positive surprise and must not be coloured as a miss.
  assert.match(zh, /tone-good/, 'surprise colours missing');

  // The rating bar segments must total the reported analyst count.
  const widths = [...zh.matchAll(/width:([\d.]+)%/g)].map((m) => Number(m[1]));
  const sum = widths.reduce((a, b) => a + b, 0);
  assert.ok(sum > 90 && sum < 110, `rating segments total ${sum.toFixed(1)}%, expected ~100%`);

  ctx.FRI18n.set('en');
  ctx.FRAnalyst.relabel();
  const en = ctx._host.innerHTML;
  assert.match(en, /Market analysts/, 'English title missing');
  assert.match(en, /Mean target/, 'English mean-target label missing');
  assert.match(en, /Recent rating and target changes/, 'English actions heading missing');

  // An unavailable symbol must say so, not render an empty panel.
  ctx.FR.api.analyst = async () => ({ ticker: 'ZZZZ', available: false, reason: 'no coverage' });
  await ctx.FRAnalyst.mount('ZZZZ');
  assert.match(ctx._host.innerHTML, /no coverage/, 'unavailable reason must be shown');

  console.log('Analyst section renders ratings, targets, surprises and estimates in both languages.');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
