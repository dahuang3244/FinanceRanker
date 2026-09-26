// End-to-end: load the REAL shell.js + i18n.js + options.js together, exactly as
// the page does. A raw i18n key on screen means t() missed; "is not a function"
// means a stale shell.js was served. Both happened once, so both are pinned.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(repoRoot, p), 'utf8');

function node(key) {
  return {
    _key: key, innerHTML: '', value: 'TSM', textContent: '', checked: false,
    dataset: {}, style: {}, children: [], firstChild: null, parentNode: null,
    addEventListener: () => {}, setAttribute: () => {}, removeAttribute: () => {},
    getAttribute: () => null, remove: () => {}, focus: () => {}, click: () => {},
    querySelector: () => null, querySelectorAll: () => [],
    classList: { add: () => {}, remove: () => {}, toggle: () => {}, contains: () => false },
    // The shell mounts the rail into `.app` and the mobile bar into `.main`.
    insertBefore: () => {}, appendChild: () => {}, prepend: () => {}, replaceChildren: () => {},
  };
}

function context(optionsPayload) {
  const nodes = new Map();
  const get = (k) => { if (!nodes.has(k)) nodes.set(k, node(k)); return nodes.get(k); };
  const ctx = {
    URLSearchParams,
    navigator: { language: 'zh-CN' },
    localStorage: { getItem: () => null, setItem: () => {} },
    location: { search: '', pathname: '/options.html', hash: '' },
    history: { replaceState: () => {}, pushState: () => {} },
    setTimeout, clearTimeout, setInterval: () => 0, clearInterval: () => {},
    // shell.js pings /api/health and dispatches a CustomEvent; that path is not
    // what this test exercises, so it is stubbed rather than simulated.
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init?.detail; } },
    Event: class { constructor(type) { this.type = type; } },
    console,
    window: {
      matchMedia: () => ({ matches: false, addEventListener: () => {} }),
      addEventListener: () => {}, location: { search: '', pathname: '/options.html' },
    },
    document: {
      documentElement: { dataset: {}, lang: 'zh-CN', classList: { add: () => {}, remove: () => {} } },
      body: { dataset: { page: 'op' }, classList: { add: () => {}, remove: () => {} },
              insertBefore: () => {}, appendChild: () => {}, firstChild: null,
              querySelector: () => null, querySelectorAll: () => [] },
      querySelector: (sel) => get(sel),
      querySelectorAll: () => [],
      getElementById: (id) => get(`#${id}`),
      addEventListener: () => {}, dispatchEvent: () => true,
      createElement: () => node('created'),
    },
    fetch: async () => ({ ok: true, status: 200, text: async () => '{}', json: async () => ({}) }),
    _nodes: nodes,
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);

  // The real files, in the order the page loads them. shell.js is an IIFE that
  // publishes FR without booting; the page calls FR.* at runtime.
  vm.runInContext(read('app/web/static/js/i18n.js') + ';globalThis.FRI18n=FRI18n;', ctx);
  ctx.t = ctx.FRI18n.t;
  vm.runInContext(read('app/web/static/js/shell.js') + ';globalThis.FR=FR;', ctx);
  vm.runInContext(read('app/web/static/js/options.js'), ctx);
  // Only the one API call the page makes needs a real answer.
  ctx.FR.api.options = async () => optionsPayload;
  return ctx;
}

const HEALTHY = {
  ticker: 'AAPL', available: true, spot: 336.73, currency: 'USD',
  source: 'Yahoo options chain', expiries_available: 22, expiries_used: 2,
  max_pain: 340, max_pain_expiry: '2026-09-23', straddle_move: 0.015,
  totals: { volume_pcr: 0.563, oi_pcr: 0.824, near_volume_pcr: 0.563, near_oi_pcr: 0.824,
            volume_calls: 254650, volume_puts: 143399, oi_calls: 204268, oi_puts: 168336,
            call_premium: 311113.57, put_premium: 84364.08 },
  verdict: {
    stance: 'bullish', stance_label: 'Upside chasing is crowded',
    lean_key: 'bullish', lean: 'careful about paying up for protection',
    novelty: 'new_bullish', novelty_label: 'The bullish tilt is new today', flow_gap: -0.261,
    chase_safety: 3, put_value: 5, wait: 'yes', max_pain_distance: 0.003,
    concentration_ratio: 1.0, concentration_label: 'Near-month book is concentrated',
  },
  summaries: [{ expiration: '2026-09-23', days: 1, call_volume: 1, put_volume: 1,
                call_oi: 1, put_oi: 1, volume_pcr: 0.487, oi_pcr: 0.691,
                max_pain: 340, buckets: { directional_call: 5 } }],
  concentration: [{ strike: 340, call_oi: 7166, put_oi: 11510, total_oi: 18676, distance: 0.003 }],
  unusual: [], notes: ['note'],
  volatility: {
    iv_atm: 0.2392, iv_call: 0.2361, iv_put: 0.2481,
    rv_21d: 0.2118, iv_rv: 1.129, iv_rank: 0.68, skew_points: 1.2,
    skew: { put_strike: 330, put_iv: 0.2481, call_strike: 350, call_iv: 0.2361 },
    expiry: '2026-10-09',
    basis: 'ATM implied from the 2026-10-09 straddle legs; realised from 21 daily log returns',
  },
};

(async () => {
  // 1. The API surface the page depends on must exist on the real object.
  const ctx = context({ ticker: 'TSM', available: false, reason: 'no listed option chain for this symbol' });
  assert.equal(typeof ctx.FR.api.options, 'function',
    'FR.api.options must be a function — an old shell.js produced "api.options is not a function"');

  // 2. Every i18n key the options page uses must resolve to real copy.
  const i18n = ctx.FRI18n;
  const used = new Set(
    [...read('app/web/static/js/options.js').matchAll(/\bt\(\s*[`"'](op\.[a-zA-Z0-9_.]+)[`"']/g)]
      .map((m) => m[1]));
  // Keys built from a variable at runtime cannot be seen by the regex above, so
  // they are listed explicitly — otherwise a missing one ships silently.
  for (const k of ['op.bucket.directional_call', 'op.bucket.directional_put',
                   'op.bucket.upside_call', 'op.bucket.downside_put',
                   'op.bucket.deep_call', 'op.bucket.deep_put',
                   'op.tab.overview', 'op.tab.flow', 'op.tab.position',
                   'op.tab.threshold', 'op.tab.analysis',
                   'op.th.ivrv', 'op.th.ivrv.hint', 'op.th.ivrv.when',
                   'op.th.skew', 'op.th.skew.hint', 'op.th.skew.when',
                   'op.th.pcr', 'op.th.pcr.hint', 'op.th.pcr.when',
                   'op.an.change.ivrv', 'op.an.change.skew', 'op.an.change.pcr',
                   'op.an.tag.defensive', 'op.an.tag.bullish', 'op.an.tag.mid',
                   'op.an.wait.yes', 'op.an.wait.no', 'op.an.wait.unknown']) used.add(k);
  const unresolved = [...used].filter((k) => i18n.t(k) === k);
  assert.deepEqual(unresolved, [],
    `unresolved i18n keys would render literally: ${unresolved.join(', ')}`);

  assert.match(i18n.t('op.unavailable.title', { ticker: 'TSM' }), /TSM/,
    'the unavailable title must interpolate the ticker');

  // 3. The unavailable state: no raw key, no crash, ticker named.
  await ctx.window.__opLoad('TSM');
  const bad = ctx._nodes.get('#opBody').innerHTML;
  assert.ok(bad.length > 100, 'unavailable state rendered nothing');
  assert.doesNotMatch(bad, /op\.unavailable\.title/, 'a raw i18n key reached the page');
  assert.doesNotMatch(bad, /is not a function/, 'an API call failed');
  assert.match(bad, /TSM/, 'the ticker must appear in the message');

  // 4. The healthy path.
  const ok = context(HEALTHY);
  await ok.window.__opLoad('AAPL');
  const good = ok._nodes.get('#opBody').innerHTML;
  assert.match(good, /AAPL/);
  assert.match(good, /\$336\.73/, 'spot missing');
  assert.match(good, /0\.563/, 'volume PCR missing');
  assert.match(good, /\$340\.00/, 'max pain missing');
  assert.doesNotMatch(good, /op\.unavailable/, 'unavailable copy leaked into the healthy path');

  // Every other tab renders without throwing.
  for (const [tab, marker] of [['flow', /按到期月拆解/], ['position', /异动合约/],
                               ['threshold', /结论何时改变/]]) {
    ok.window.__opState.tab = tab;
    ok.window.__opRender();
    assert.match(ok._nodes.get('#opBody').innerHTML, marker, `${tab} tab missing its heading`);
  }

  // The thresholds tab must carry the three conditions at their stated triggers,
  // with the volatility figures actually rendered rather than left blank.
  ok.window.__opState.tab = 'threshold';
  ok.window.__opRender();
  const thresholds = ok._nodes.get('#opBody').innerHTML;
  assert.match(thresholds, /IV\/RV/, 'IV/RV card missing');
  assert.match(thresholds, /Skew/, 'skew card missing');
  assert.match(thresholds, /Volume PCR/, 'volume PCR card missing');
  assert.match(thresholds, /1\.05/, 'IV/RV trigger 1.05 missing');
  assert.match(thresholds, /\+2\.5 vol pts/, 'skew trigger +2.5 missing');
  assert.match(thresholds, /0\.90/, 'PCR trigger 0.90 missing');
  // The measured values must appear, not just the thresholds.
  assert.match(thresholds, /1\.129/, 'IV/RV value missing');
  assert.match(thresholds, /\+1\.2 vol pts/, 'skew value missing');
  assert.match(thresholds, /0\.563/, 'volume PCR value missing');
  // And the derivation of the volatility figures must be disclosed.
  assert.match(thresholds, /straddle legs/, 'volatility basis missing');
  assert.match(thresholds, /25-delta/, 'skew derivation missing');
  // IV/RV 1.129 >= 1.05 must read as triggered; it is the only one that is.
  assert.match(thresholds, /is-breached/, 'a breached threshold must be marked');

  // 5. The personal-analysis tab: verdict, the three question boxes, the
  //    attribution blocks, what would change the reading, and the limits.
  ok.window.__opState.tab = 'analysis';
  ok.window.__opRender();
  const analysis = ok._nodes.get('#opBody').innerHTML;
  assert.doesNotMatch(analysis, /op\.(an|th)\.[a-zA-Z.]*/, 'a raw i18n key reached the analysis tab');
  // The verdict must follow the UI language, not arrive pre-worded from the
  // server: the payload carries English, the screen shows Chinese.
  assert.match(analysis, /追多气氛偏浓/, 'stance must be named in the UI language');
  assert.match(analysis, /偏谨慎对待防守/, 'lean must be named in the UI language');
  assert.doesNotMatch(analysis, /Upside chasing is crowded/,
    'a backend-written label leaked into a Chinese screen');
  // The three question boxes, each scored as a scale rather than a number.
  assert.match(analysis, /追 Call 安全度/, 'chase-safety question missing');
  assert.match(analysis, /保护 Put 性价比/, 'put-value question missing');
  assert.match(analysis, /现在是否值得等待/, 'wait question missing');
  assert.match(analysis, /op-stars/, 'scores must render as a scale');
  const filled = (analysis.match(/<i class="on"><\/i>/g) || []).length;
  assert.ok(filled >= 8, `expected filled score dots, found ${filled}`);
  // Attribution: premium actually traded, concentration, and the watch list.
  // FR.money abbreviates, so $311,113.57 renders as "$311.1K".
  assert.match(analysis, /\$311\.1K/, 'call premium missing');
  assert.match(analysis, /\$84\.4K/, 'put premium missing');
  assert.match(analysis, /近月持仓集中/, 'concentration label must be named in the UI language');
  assert.match(analysis, /我会盯哪些价位/, 'watch list heading missing');
  // What would change it, and the limits it refuses to cross.
  assert.match(analysis, /什么会推翻我的判断/, 'change table missing');
  assert.match(analysis, /没有做市商 Gamma 口径/, 'must refuse a gamma figure');
  assert.match(analysis, /不含隐含波动率/, 'must state that no IV is reported');
  assert.match(analysis, /未平仓量看不出持有人是谁/, 'must state the open-interest limit');

  console.log('Real shell + i18n + options: no raw keys, no missing functions, every tab renders.');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
