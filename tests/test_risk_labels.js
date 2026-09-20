// Guard: every i18n key referenced by the ranking/company metric lists exists in
// both languages, and the new risk metrics are wired end to end.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const jsDir = path.join(root, 'app', 'web', 'static', 'js');

const i18nSrc = fs.readFileSync(path.join(jsDir, 'i18n.js'), 'utf8');
const rankingSrc = fs.readFileSync(path.join(jsDir, 'ranking.js'), 'utf8');
const companySrc = fs.readFileSync(path.join(jsDir, 'company-view.js'), 'utf8');
const sourcesSrc = fs.readFileSync(path.join(jsDir, 'sources.js'), 'utf8');

// Collect the keys the dictionary defines, with their [zh, en] pairs. Hint
// entries can wrap across lines, so the value pattern is multi-line.
const defined = new Map();
for (const m of i18nSrc.matchAll(/^\s*"([^"]+)":\s*(\[[\s\S]*?\]),\s*$/gm)) {
  defined.set(m[1], m[2]);
}
assert.ok(defined.size > 300, `expected a large dictionary, saw ${defined.size}`);

// Every `t("...")` / `["..."]` metric key used by the UI must be defined.
const used = new Set();
for (const src of [rankingSrc, companySrc, sourcesSrc]) {
  for (const m of src.matchAll(/\bt\("([a-z]+\.[A-Za-z0-9_.]+)"/g)) used.add(m[1]);
  for (const m of src.matchAll(/\["(m\.[A-Za-z0-9_]+)"\]/g)) used.add(m[1]);
}

const missing = [...used].filter((k) => !defined.has(k));
assert.equal(missing.length, 0, `undefined i18n keys: ${missing.join(', ')}`);

// Each new metric label must be present in BOTH languages, non-empty.
const NEW_METRICS = [
  'm.return_1m', 'm.return_3m', 'm.return_6m', 'm.return_1y', 'm.return_ytd', 'm.return_3y',
  'm.excess_return_3m', 'm.excess_return_6m', 'm.excess_return_1y',
  'm.benchmark_return_3m', 'm.benchmark_return_6m', 'm.benchmark_return_1y',
  'm.volatility', 'm.downside_deviation', 'm.sharpe_ratio', 'm.sortino_ratio',
  'm.beta', 'm.beta_1y', 'm.max_drawdown_1y', 'm.drawdown_52w',
  'm.analyst_target', 'm.analyst_upside',
];
for (const key of NEW_METRICS) {
  assert.ok(defined.has(key), `missing i18n entry for ${key}`);
  assert.ok(defined.has(`${key}.hint`), `missing hint for ${key}`);
  const pair = defined.get(key);
  assert.match(pair, /"[\s\S]+?"\s*,\s*"[\s\S]+?"/, `${key} must have a zh and an en label`);
}

// The ranking table must actually expose the return/risk columns.
for (const col of ['return_1m', 'return_3m', 'return_6m', 'return_1y',
                   'excess_return_3m', 'excess_return_6m', 'excess_return_1y',
                   'volatility', 'sharpe_ratio', 'sortino_ratio', 'beta_1y',
                   'max_drawdown_1y', 'drawdown_52w']) {
  assert.match(rankingSrc, new RegExp(`key:\\s*"${col}"`), `ranking.js lacks column ${col}`);
}

// Private (full-history) beta is displayed but must not be scored twice.
assert.match(rankingSrc, /key:\s*"beta"/, 'full-history beta column removed');

console.log(`i18n + ranking wiring OK (${defined.size} keys, ${used.size} referenced)`);
