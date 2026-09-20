// Render the actual company detail view with both language settings.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// The detail view now builds its metric blocks from the backend catalogue
// (`GET /api/metrics`), so the stub has to serve one. This mirrors what the
// real endpoint returns for a scored metric plus a reference-only one.
const CATALOG = {
  metrics_version: 3,
  scored_count: 2,
  metrics: [
    { attr: 'revenue_growth_yoy', component: 'growth', higher_is_better: true,
      kind: 'ratio', label: 'Revenue growth YoY', scored: true, order: 0, note: null },
    { attr: 'return_3m', component: 'market', higher_is_better: true,
      kind: 'ratio', label: '3M return', scored: true, order: 0, note: null },
    { attr: 'beta', component: 'market', higher_is_better: false,
      kind: 'num', label: 'Beta (full history)', scored: false, order: 0,
      note: 'duplicates the scored 1-year beta on a different window' },
  ],
};

const context = {
  URLSearchParams,
  navigator: { language: 'zh-CN' },
  localStorage: { getItem: () => null, setItem: () => {} },
  window: { matchMedia: () => ({ matches: false }) },
  document: {
    documentElement: { dataset: {} },
    body: { dataset: {} },
    querySelectorAll: () => [],
  },
  FR: {
    api: { metrics: async () => CATALOG },
    icon: () => '', $: () => null, $$: () => [],
    escapeHtml: (value) => value == null ? '' : String(value),
    toast: () => {}, stateBlock: () => '',
    num: (value) => value == null ? '—' : String(value),
    money: (value) => value == null ? '—' : String(value),
    when: String, tone: () => 'mid', meter: () => '',
    profilePill: () => '', DASH: '—', fmt: String,
    profileLabel: () => '',
  },
};
vm.createContext(context);
// Resolve the app sources from the repository root so this test works no
// matter which directory it is invoked from.
const repoRoot = require('node:path').join(__dirname, '..');
for (const [file, exportName, alias] of [
  ['app/web/static/js/i18n.js', 'FRI18n', 'i18n'],
  ['app/web/static/js/company-view.js', 'FRCompany', 'company'],
]) {
  vm.runInContext(
    fs.readFileSync(require('node:path').join(repoRoot, file), 'utf8')
      + `;globalThis.${alias}=${exportName};`,
    context,
  );
}
context.t = context.i18n.t;
const row = {
  ticker: 'TSM', company: 'TSM', score_overall: 6, rank: 1,
  data_coverage: 2, revenue_growth_yoy: 0.151, gross_margin: 0.597,
  return_3m: 0.05, beta: 1.41, z_return_3m: 7.5,
};

(async () => {
  // `render` is synchronous but needs the catalogue loaded first.
  await context.company.ensureCatalog();

  const chinese = context.company.render(row);
  assert.match(chinese, /<b>营收同比/, 'Chinese label missing for a scored metric');
  assert.match(chinese, /<em>Revenue growth YoY<\/em>/, 'Chinese view needs the English sub-label');

  // A reference-only metric must be tagged and must NOT show a score, because
  // an empty score cell is indistinguishable from a metric that scored badly.
  assert.match(chinese, /class="reftag"/, 'reference-only metric is not tagged');
  assert.match(chinese, /is-reference/, 'reference-only row is not marked');
  assert.match(chinese, /不计分/, 'reference-only row does not say it is unscored');

  context.i18n.set('en');
  const english = context.company.render(row);
  assert.match(english, /<b>Revenue growth YoY/, 'English label missing for a scored metric');
  assert.match(english, /<em>营收同比<\/em>/, 'English view needs the Chinese sub-label');
  assert.match(english, /not scored/, 'English reference-only marker missing');

  console.log('Company detail metric labels render in Chinese and English.');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
