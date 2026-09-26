// The options page must render from a real payload shape, in both languages, and
// must state what it cannot report rather than leaving a silent gap where an
// implied-volatility row would be.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.join(__dirname, '..');

// Shaped exactly like `GET /api/options/{ticker}`.
const PAYLOAD = {
  ticker: 'AAPL', available: true, spot: 338.98, currency: 'USD',
  source: 'Yahoo options chain (crumb-authenticated)',
  expiries_available: 22, expiries_used: 2, max_pain_expiry: '2026-09-23',
  max_pain: 340.0, straddle_move: 0.015,
  totals: {
    volume_calls: 254650, volume_puts: 143399, oi_calls: 204268, oi_puts: 168336,
    volume_pcr: 0.5631, oi_pcr: 0.8241, near_volume_pcr: 0.5631, near_oi_pcr: 0.8241,
  },
  summaries: [
    { expiration: '2026-09-23', days: 1, call_volume: 120000, put_volume: 58000,
      call_oi: 90000, put_oi: 62000, volume_pcr: 0.487, oi_pcr: 0.691,
      max_pain: 340.0, straddle_move: 0.015,
      buckets: { directional_call: 41372, directional_put: 25759, downside_put: 4914 } },
    { expiration: '2026-09-25', days: 3, call_volume: 134650, put_volume: 85399,
      call_oi: 114268, put_oi: 106336, volume_pcr: 0.690, oi_pcr: 0.899,
      max_pain: 332.5, straddle_move: 0.021,
      buckets: { directional_call: 113322, downside_put: 89307 } },
  ],
  concentration: [
    { strike: 340.0, call_oi: 7166, put_oi: 11510, total_oi: 18676, distance: 0.003 },
    { strike: 345.0, call_oi: 6625, put_oi: 147, total_oi: 6772, distance: 0.0178 },
  ],
  unusual: [
    { kind: 'put', strike: 337.5, expiration: '2026-09-23', days: 1, volume: 12021,
      open_interest: 2043, ratio: 5.9, last_price: 1.82, direction: 'directional_put' },
    { kind: 'call', strike: 340.0, expiration: '2026-09-23', days: 1, volume: 34931,
      open_interest: 7166, ratio: 4.9, last_price: 2.07, direction: 'directional_call' },
  ],
  notes: ["Call volume leads puts (0.563): today's flow leans bullish.",
          'Open interest shows where contracts exist, never who holds them.'],
};

function context(payload) {
  const nodes = new Map();
  const node = (key) => {
    if (!nodes.has(key)) {
      nodes.set(key, {
        innerHTML: '', value: '4', checked: false,
        addEventListener: () => {}, setAttribute: () => {}, removeAttribute: () => {},
        querySelectorAll: () => [], classList: { add: () => {}, remove: () => {} },
      });
    }
    return nodes.get(key);
  };
  const ctx = {
    URLSearchParams,
    navigator: { language: 'zh-CN' },
    localStorage: { getItem: () => null, setItem: () => {} },
    location: { search: '', pathname: '/options.html' },
    history: { replaceState: () => {} },
    window: { matchMedia: () => ({ matches: false }) },
    document: {
      documentElement: { dataset: {} },
      body: { dataset: {} },
      querySelector: (sel) => (sel === '#opExpiries' ? { value: '4' } : node(sel)),
      querySelectorAll: () => [],
      getElementById: (id) => node(`#${id}`),
      addEventListener: () => {},
    },
    FR: {
      api: {
        options: async () => payload,
        ranking: async () => ({ rows: [] }),
      },
      icon: () => '<svg></svg>',
      escapeHtml: (v) => (v == null ? '' : String(v).replace(/[&<>"]/g, (c) =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))),
      num: (v) => (v == null ? '—' : String(v)),
      money: (v) => (v == null ? '—' : `$${Number(v).toFixed(2)}`),
      pct: (v) => (v == null ? '—' : `${v}%`),
      when: String, tone: () => 'mid', meter: () => '', profilePill: () => '',
      profileLabel: () => '', DASH: '—', fmt: String, toast: () => {},
      // Mirrors the real empty/unavailable block so its copy is asserted,
      // rather than returning nothing and hiding what a user would actually see.
      stateBlock: ({ title, body: text, action } = {}) =>
        `<div class="state"><h3>${title || ''}</h3><p>${text || ''}</p>${
          action ? `<a href="${action.href}">${action.label}</a>` : ''}</div>`,
      el: () => null, debounce: (f) => f,
      $: (sel) => node(sel), $$: () => [],
    },
    _nodes: nodes,
  };
  vm.createContext(ctx);
  // options.js is an IIFE that publishes its hooks on `window`; it declares no
  // module global, so nothing is rebound here.
  for (const file of ['app/web/static/js/i18n.js', 'app/web/static/js/options.js']) {
    vm.runInContext(
      fs.readFileSync(path.join(repoRoot, file), 'utf8')
        + (file.includes('i18n') ? ';globalThis.FRI18n=FRI18n;' : ''),
      ctx,
    );
  }
  return ctx;
}

function body(ctx) {
  return ctx._nodes.get('#opBody').innerHTML;
}

(async () => {
  const ctx = context(PAYLOAD);
  ctx.t = ctx.FRI18n.t;
  ctx.FRI18n.set('zh');

  await ctx.window.__opLoad('AAPL');

  const overview = body(ctx);
  assert.ok(overview.length > 400, 'overview rendered nothing');
  assert.match(overview, /AAPL/, 'ticker missing');
  assert.match(overview, /\$338\.98/, 'spot missing');
  assert.match(overview, /PUT\/CALL/i, 'put/call tiles missing');
  assert.match(overview, /0\.563/, 'volume PCR missing');
  assert.match(overview, /0\.824/, 'open-interest PCR missing');
  assert.match(overview, /\$340\.00/, 'max pain missing');
  assert.match(overview, /op-tabs/, 'tab bar missing');
  assert.match(overview, /Call \/ Put 结构/, 'call/put structure heading missing');
  assert.match(overview, /持仓集中区/, 'concentration heading missing');
  // The concentration bar must be sized from open interest, not a fixed width.
  assert.match(overview, /op-weight"><i style="width:100\.0%"/, 'largest strike must fill the bar');

  // Every tab must render without throwing, and each must add its own content.
  for (const [tab, marker] of [
    ['flow', /按到期月拆解/],
    ['position', /异动合约/],
    ['threshold', /结论何时改变/],
    ['analysis', /这份读法的边界/],
  ]) {
    ctx.window.__opState.tab = tab;
    ctx.window.__opRender();
    const html = body(ctx);
    assert.ok(html.length > 300, `${tab} tab rendered nothing`);
    assert.match(html, marker, `${tab} tab missing its heading`);
  }

  // The unusual table carries the real ratio and both contract sides.
  ctx.window.__opState.tab = 'position';
  ctx.window.__opRender();
  const position = body(ctx);
  assert.match(position, /5\.9×/, 'unusual ratio missing');
  assert.match(position, /337\.50/, 'unusual strike missing');
  assert.match(position, /看跌 Put/, 'directional put label missing');

  // The analysis tab must disclose what is *not* reported, not quietly omit it.
  ctx.window.__opState.tab = 'analysis';
  ctx.window.__opRender();
  const analysis = body(ctx);
  assert.match(analysis, /不含隐含波动率/, 'must state that no IV is reported');
  assert.match(analysis, /未平仓量看不出持有人是谁/, 'must state the open-interest limit');
  assert.match(analysis, /Call volume leads puts/, 'backend notes must be surfaced');

  // Thresholds must compare against the stated trigger levels, and must render
  // the volatility figures when the payload carries them.
  ctx.window.__opState.tab = 'threshold';
  ctx.window.__opRender();
  const threshold = body(ctx);
  assert.match(threshold, /IV\/RV/, 'IV/RV card missing');
  assert.match(threshold, /Skew/, 'skew card missing');
  assert.match(threshold, /Volume PCR/, 'volume PCR card missing');
  assert.match(threshold, /1\.05/, 'IV/RV trigger missing');
  assert.match(threshold, /2\.5 vol pts/, 'skew trigger missing');
  assert.match(threshold, /0\.90/, 'PCR trigger missing');
  // With no volatility block in this payload the two figures must read as
  // unmeasurable rather than silently showing a stale or invented number.
  assert.match(threshold, /无法测算/, 'an unmeasurable threshold must say so');

  // A ticker with no options must say so rather than rendering an empty shell.
  const empty = context({ ticker: 'BRK.B', available: false,
    reason: 'no listed option chain for this symbol' });
  empty.t = empty.FRI18n.t;
  await empty.window.__opLoad('BRK.B');
  assert.match(body(empty), /BRK\.B/, 'unavailable state must name the ticker');
  assert.match(body(empty), /没有可用的期权链/, 'unavailable state must explain itself');

  console.log('Options page renders all five tabs, both states, and states its limits.');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
