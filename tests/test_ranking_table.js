// Guard the ranking table's column definitions: unique keys, no inline <td>,
// every renderer paired with a known group, and the group labels translated.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const src = fs.readFileSync(path.join(root, 'app', 'web', 'static', 'js', 'ranking.js'), 'utf8');
const i18n = fs.readFileSync(path.join(root, 'app', 'web', 'static', 'js', 'i18n.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'app', 'web', 'static', 'css', 'components.css'), 'utf8');
const html = fs.readFileSync(path.join(root, 'app', 'web', 'static', 'ranking.html'), 'utf8');

// 1. Column keys are unique — a duplicate silently drops a column.
const keys = [...src.matchAll(/key:\s*"([A-Za-z0-9_]+)"/g)].map((m) => m[1]);
const dupes = [...new Set(keys.filter((k, i) => keys.indexOf(k) !== i))];
assert.equal(dupes.length, 0, `duplicate column keys: ${dupes.join(', ')}`);
assert.ok(keys.length >= 40, `expected the full column catalogue, saw ${keys.length}`);

// 2. Renderers must return cell *contents*; the wrapper adds alignment/freeze.
const inline = [...src.matchAll(/^\s*\{[^}]*render:[^}]*<td /gm)];
assert.equal(inline.length, 0, `${inline.length} renderer(s) still emit their own <td>`);

// 3. `decorateCell` is what wraps them.
assert.match(src, /function decorateCell\(/);
assert.match(src, /decorateCell\(c\.render\(r\), byKey\[c\.key\]\)/);

// 4. Group metadata resolves to real i18n keys, in both languages.
const groups = [...new Set([...src.matchAll(/(\w+):\s*"(scores|returns|risk|consensus|fundamentals)"/g)]
  .map((m) => m[2]))];
assert.ok(groups.length === 5, `expected 5 groups, saw ${groups.join(', ')}`);
for (const g of groups) {
  const key = `"rk.grp.${g}":`;
  const at = i18n.indexOf(key);
  assert.ok(at >= 0, `missing i18n label rk.grp.${g}`);
  const entry = i18n.slice(at, i18n.indexOf(']', at));
  assert.match(entry, /"[^"]+?"\s*,\s*"[^"]+?"/, `rk.grp.${g} needs a zh and an en label`);
}

// 5. Frozen identity columns have CSS for both offsets.
assert.match(css, /col-freeze-1/);
assert.match(css, /col-freeze-2/);
assert.match(src, /freeze: 1/);
assert.match(src, /freeze: 2/);

// 6. The staleness notice has a mount point and a render path.
assert.match(html, /id="staleNotice"/);
assert.match(src, /function renderStale\(/);
assert.match(src, /renderStale\(state\.stale\)/);
for (const key of ['rk.stale.title', 'rk.stale.body', 'rk.stale.old', 'rk.stale.cta']) {
  assert.ok(i18n.includes(`"${key}":`), `missing i18n entry ${key}`);
}

// 7. Header tooltips fall back to the label when no hint exists.
assert.match(src, /function hintFor\(/);

console.log(`ranking table OK (${keys.length} columns, ${groups.length} groups, staleness notice wired)`);
