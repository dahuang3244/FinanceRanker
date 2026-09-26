// The charts must emit real geometry for real payloads. An undefined or NaN
// coordinate renders as a silently broken chart, which nothing else catches.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.join(__dirname, '..');
const payloadPath = process.argv[2];
if (!payloadPath) {
  // This one needs a payload captured from a running app:
  //   curl /api/analyst/{ticker} to a file and pass it in.
  // `test_analyst_ui.js` covers the same rendering with a fixed fixture, so
  // running without one is a skip rather than a failure.
  console.log('SKIP  no payload given — pass JSON from GET /api/analyst/{ticker}');
  process.exit(0);
}
const PAYLOAD = JSON.parse(fs.readFileSync(payloadPath, 'utf8'));

const ctx = {
  URLSearchParams,
  navigator: { language: 'en' },
  localStorage: { getItem: () => null, setItem: () => {} },
  window: { matchMedia: () => ({ matches: false }) },
  document: { documentElement: { dataset: {} }, body: { dataset: {} },
              querySelectorAll: () => [], getElementById: () => null },
  FR: {
    api: {}, icon: () => '', escapeHtml: (v) => (v == null ? '' : String(v)),
    num: (v) => (v == null ? '—' : String(v)), money: (v) => (v == null ? '—' : String(v)),
    when: String, tone: () => 'mid', meter: () => '', profilePill: () => '',
    profileLabel: () => '', DASH: '—', fmt: String, toast: () => {}, stateBlock: () => '',
    $: () => null, $$: () => [],
  },
};
vm.createContext(ctx);
for (const file of ['app/web/static/js/i18n.js', 'app/web/static/js/analyst.js']) {
  const name = file.includes('i18n') ? 'FRI18n' : 'FRAnalyst';
  vm.runInContext(fs.readFileSync(path.join(repoRoot, file), 'utf8')
    + `;globalThis.${name}=${name};`, ctx);
}
ctx.t = ctx.FRI18n.t;
ctx.FRI18n.set('en');

const { targetChart, surpriseChart, ratingBars } = ctx.FRAnalyst._internals;

function checkSvg(svg, label) {
  assert.ok(svg.includes('<svg'), `${label}: no svg emitted`);
  assert.ok(!/NaN|undefined|null/.test(svg), `${label}: bad coordinate in output`);
  // Every coordinate attribute must parse as a finite number.
  const coords = [...svg.matchAll(/\b(?:cx|cy|x|y|x1|x2|y1|y2|r|width|height)="([^"]+)"/g)];
  assert.ok(coords.length > 0, `${label}: no coordinates at all`);
  for (const [, raw] of coords) {
    const value = Number(raw);
    assert.ok(Number.isFinite(value), `${label}: non-finite coordinate "${raw}"`);
  }
  // Geometry must sit inside the declared viewBox.
  const viewBox = svg.match(/viewBox="0 0 (\d+) (\d+)"/);
  assert.ok(viewBox, `${label}: no viewBox`);
  const [, w, h] = viewBox.map(Number);
  for (const [attr, raw] of [...svg.matchAll(/\b(cx|cy|x1|x2|y1|y2|r|width|height)="([^"]+)"/g)]
    .map((m) => [m[1], Number(m[2])])) {
    const limit = /^c[xy]$|^[xy][12]$|^r$|^width$/.test(attr) && /^(x|x1|x2|cx|width)$/.test(attr) ? w : h;
    assert.ok(raw >= -1 && raw <= limit + 1,
      `${label}: ${attr}=${raw} outside 0..${limit}`);
  }
  return coords.length;
}

const n1 = checkSvg(targetChart(PAYLOAD), 'targetChart');
const n2 = checkSvg(surpriseChart(PAYLOAD.earnings_history), 'surpriseChart');
const bars = ratingBars(PAYLOAD.ratings);
assert.ok(!/NaN|undefined/.test(bars), 'ratingBars: bad output');

// A payload with no actions or no history must degrade to an empty string,
// not to a half-drawn chart.
assert.equal(targetChart({ ...PAYLOAD, actions: [] }), '');
assert.equal(surpriseChart([]), '');
assert.equal(ratingBars([]), '');

console.log(`charts OK (targetChart ${n1} coords, surpriseChart ${n2} coords, ${PAYLOAD.ticker})`);
