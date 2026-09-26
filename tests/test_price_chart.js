// The price chart must be date-proportional and carry a readable scale.
// Previously it was index-spaced with no y-axis at all, so a 3% drift and a 90%
// run drew identically and a data gap was drawn as ordinary elapsed time.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.join(__dirname, '..');

/** Run the shipped drawSpark() with a stubbed DOM. */
function render(bars) {
  const svg = { innerHTML: '', attrs: {},
    setAttribute(k, v) { this.attrs[k] = v; },
    removeAttribute(k) { delete this.attrs[k]; },
    clientWidth: 2000, clientHeight: 150 };
  const note = { textContent: '' };
  const yAxis = { innerHTML: '' };
  const xAxis = { innerHTML: '' };
  const nodes = { '#pvSpark': svg, '#pvSparkNote': note, '#pvSparkY': yAxis, '#pvSparkX': xAxis };

  const ctx = {
    console,
    document: { querySelector: (s) => nodes[s] || null, querySelectorAll: () => [] },
    window: { matchMedia: () => ({ matches: false }) },
    navigator: { language: 'en' },
    localStorage: { getItem: () => null, setItem: () => {} },
    t: (k) => k,
    FR: {
      api: {}, icon: () => '', $: (s) => nodes[s] || null, $$: () => [],
      escapeHtml: (v) => (v == null ? '' : String(v)),
      num: (v) => (v == null ? '—' : String(v)),
      money: (v) => (v == null ? '—' : `$${Number(v).toFixed(2)}`),
      pct: () => '', DASH: '—', toast: () => {}, stateBlock: () => '', el: () => null,
      debounce: (f) => f, when: String, tone: () => 'mid', meter: () => '',
      profilePill: () => '', profileLabel: () => '', fmt: String,
    },
  };
  vm.createContext(ctx);
  const src = fs.readFileSync(path.join(repoRoot, 'app/web/static/js/preview.js'), 'utf8')
    .replace(/\n\}\)\(\);\s*$/, '\nglobalThis.__drawSpark = drawSpark;\n})();\n');
  vm.runInContext(src, ctx);
  ctx.__drawSpark(bars);
  return { html: svg.innerHTML, note: note.textContent, y: yAxis.innerHTML, x: xAxis.innerHTML,
           attrs: svg.attrs };
}

const series = (n, fn, startDay = 0) => Array.from({ length: n }, (_, i) => {
  const d = new Date(Date.UTC(2025, 0, 1) + (startDay + i) * 86400000);
  return { d: d.toISOString().slice(0, 10), close: fn(i) };
});

function points(html) {
  const m = html.match(/class="line" points="([^"]+)"/);
  assert.ok(m, 'no line emitted');
  return m[1].trim().split(/\s+/).map((p) => p.split(',').map(Number));
}

// 1. A rising series uses the full plot height and is labelled.
{
  const bars = series(60, (i) => 100 + i * 2);
  const out = render(bars);
  const pts = points(out.html);
  const ys = pts.map((p) => p[1]);
  const travel = Math.max(...ys) - Math.min(...ys);
  assert.ok(travel > 100, `expected the curve to use the height, got ${travel}`);
  assert.match(out.y, /\$/, 'y-axis must show currency values');
  assert.match(out.y, /hi/, 'y-axis must mark the high');
  assert.match(out.y, /lo/, 'y-axis must mark the low');
  assert.match(out.html, /class="grid"/, 'gridlines missing');
  assert.equal(pts.length, 60, 'every bar must be plotted');
  assert.match(out.x, /25\/0[12]/, 'x-axis must show the start date');
  assert.ok(out.attrs['aria-label'], 'chart needs an accessible label');
}

// 2. X positions are proportional to DATE, not to index. A long gap in the
//    middle must leave a visible gap rather than being skipped over.
{
  const withGap = [
    ...series(20, (i) => 100 + i, 0),
    // a 90-day hole, then continue
    ...series(20, (i) => 120 + i, 110),
  ];
  const out = render(withGap);
  const pts = points(out.html);
  const xs = pts.map((p) => p[0]);
  // The gap should occupy roughly 90/(total span) of the width, far more than
  // the single-bar stride an index-based plot would give it.
  const strides = xs.slice(1).map((x, i) => x - xs[i]);
  const gapStride = strides[19];
  const typical = strides.filter((_, i) => i !== 19).reduce((a, b) => a + b, 0) / (strides.length - 1);
  assert.ok(gapStride > typical * 5,
    `gap should be date-scaled: gap step ${gapStride.toFixed(1)} vs typical ${typical.toFixed(1)}`);
}

// 3. A flat series must not blow up or vanish.
{
  const out = render(series(30, () => 250));
  const pts = points(out.html);
  for (const [, y] of pts) assert.ok(Number.isFinite(y), 'flat series produced a bad coordinate');
  assert.ok(!/NaN|undefined/.test(out.html), 'NaN in output');
}

// 4. Too few bars clears the chart instead of drawing nonsense.
{
  const out = render([{ d: '2026-01-01', close: 10 }]);
  assert.equal(out.html, '');
  assert.equal(out.y, '');
  assert.equal(out.x, '');
}

// 5. Every coordinate stays inside the viewBox.
{
  const out = render(series(200, (i) => 100 + Math.sin(i / 9) * 40));
  for (const [x, y] of points(out.html)) {
    assert.ok(x >= 0 && x <= 1000, `x out of range: ${x}`);
    assert.ok(y >= 0 && y <= 150, `y out of range: ${y}`);
  }
}

console.log('Price chart is date-proportional and carries a readable price scale.');
