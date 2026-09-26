/* Render the quarterly section from REAL server payloads, per ticker.
 *
 * The fixture-based tests prove the renderer works for shapes I thought of. This
 * one proves it works for every shape the server actually returns, which is how a
 * ticker that renders blank while others are fine gets caught.
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const STATIC = path.join(ROOT, "app", "web", "static");

/* A small but functional DOM stub. shell.js boots on load and builds the rail, so
   the stub has to support the handful of things it does rather than return
   nothing — a stub that throws hides the render result this test is after. */
function node(name = "div") {
  const n = {
    tagName: String(name).toUpperCase(),
    nodeName: String(name).toUpperCase(),
    children: [],
    attrs: {},
    style: {},
    dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    innerHTML: "",
    textContent: "",
    className: "",
    setAttribute(k, v) { this.attrs[k] = v; },
    getAttribute(k) { return this.attrs[k] ?? null; },
    removeAttribute(k) { delete this.attrs[k]; },
    hasAttribute(k) { return k in this.attrs; },
    appendChild(child) { this.children.push(child); return child; },
    removeChild(child) { return child; },
    insertBefore(child) { this.children.push(child); return child; },
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() { return true; },
    querySelector: () => null,
    querySelectorAll: () => [],
    closest: () => null,
    contains: () => false,
    focus() {},
    blur() {},
    click() {},
    cloneNode() { return node(name); },
  };
  return n;
}

function makeContext() {
  const doc = {
    scripts: [],
    documentElement: node("html"),
    head: node("head"),
    body: node("body"),
    createElement: (tag) => node(tag),
    createElementNS: (_ns, tag) => node(tag),
    createTextNode: (text) => ({ textContent: text }),
    createDocumentFragment: () => node("fragment"),
    querySelector: () => null,
    querySelectorAll: () => [],
    getElementById: () => null,
    addEventListener() {},
    removeEventListener() {},
    readyState: "complete",
    title: "",
    lang: "en",
  };
  const ctx = {
    console,
    document: doc,
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    navigator: { language: "en", userAgent: "node" },
    location: {
      search: "", href: "http://x/company.html", replace() {},
      pathname: "/company.html", origin: "http://x", hash: "",
    },
    history: { replaceState() {}, pushState() {} },
    URLSearchParams,
    URL,
    fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
    matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
    getComputedStyle: () => ({ getPropertyValue: () => "" }),
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() { return true; },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
  };
  ctx.window = ctx;
  ctx.globalThis = ctx;
  return ctx;
}

const sandbox = makeContext();
vm.createContext(sandbox);
// The company module is an IIFE that exposes itself as FRCompany; capture it under
// a local name so the payload can be handed to its renderer by reference.
for (const file of ["js/i18n.js", "js/shell.js", "js/scale.js", "js/company-view.js"]) {
  vm.runInContext(fs.readFileSync(path.join(STATIC, file), "utf8"), sandbox, { filename: file });
}
vm.runInContext("globalThis.__cv = FRCompany;", sandbox);

const payloads = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
let failures = 0;

for (const [ticker, payload] of Object.entries(payloads)) {
  let html;
  try {
    sandbox.__payload = payload;
    html = vm.runInContext("__cv.quarterBlock(__payload)", sandbox);
  } catch (err) {
    console.log(`FAIL  ${ticker}: render threw ${err.message}`);
    failures += 1;
    continue;
  }

  const quarters = (payload.quarters || []).length;
  // The card element is `<details class="epsq ...">`; inner elements use
  // `epsq-fig`, `epsq-head` and so on, so the boundary has to be matched or the
  // count picks those up too (35 for 4 quarters, which is how this was caught).
  const cards = (html.match(/class="epsq[\s"]/g) || []).length;
  // A raw i18n key left on screen is the real failure this looks for. Keys named
  // inside a title attribute are not rendered text, so they are allowed.
  const rawKeys = [...new Set(html.match(/co\.eps\.[a-zA-Z]+/g) || [])]
    .filter((key) => html.includes(`>${key}<`) || html.includes(`>${key} `));
  const expected = payload.available ? quarters : 0;
  const ok = cards === expected && rawKeys.length === 0;

  console.log(
    `${ok ? "PASS" : "FAIL"}  ${ticker.padEnd(7)} available=${String(payload.available).padEnd(5)} ` +
    `source=${(payload.source || "-").padEnd(8)} quarters=${quarters} cards=${cards}` +
    (rawKeys.length ? `  RAW KEYS: ${rawKeys.join(", ")}` : "")
  );
  if (!ok) failures += 1;
}

console.log(failures ? `\n${failures} ticker(s) failed to render` : "\nevery ticker rendered");
process.exit(failures ? 1 : 0);
