/* ==========================================================================
   FinanceRanker — shared shell + utilities.
   Every page renders its own markup; this file injects the navigation chrome,
   exposes the formatting helpers used by the tables and wires the live health
   indicator. No dependencies, no build step.
   ========================================================================== */
"use strict";

const FR = (() => {
  /* ------------------------------------------------------------------ icons */
  const P = {
    overview: '<path d="M3 13h8V3H3zM13 21h8V11h-8zM13 3v6h8V3zM3 21h8v-6H3z"/>',
    play: '<path d="M6 4l13 8-13 8z"/>',
    table: '<path d="M4 5h16v14H4zM4 10h16M10 10v9"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3 2"/>',
    database: '<ellipse cx="12" cy="6" rx="7.5" ry="3"/><path d="M4.5 6v12c0 1.66 3.36 3 7.5 3s7.5-1.34 7.5-3V6M4.5 12c0 1.66 3.36 3 7.5 3s7.5-1.34 7.5-3"/>',
    download: '<path d="M12 3v12m0 0l-4.5-4.5M12 15l4.5-4.5M4 20h16"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4.2-4.2"/>',
    refresh: '<path d="M20 11a8 8 0 10-2.3 5.7M20 5v6h-6"/>',
    chevron: '<path d="M9 6l6 6-6 6"/>',
    chevronDown: '<path d="M6 9l6 6 6-6"/>',
    alert: '<path d="M12 3l9 16H3zM12 9v5M12 17.5v.01"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 7.6v.01"/>',
    check: '<path d="M4 12.5l5 5L20 6.5"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>',
    menu: '<path d="M3 6h18M3 12h18M3 18h18"/>',
    external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1h5"/>',
    zap: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
    shield: '<path d="M12 3l7 3v6c0 4.2-2.9 7.6-7 9-4.1-1.4-7-4.8-7-9V6z"/>',
    trend: '<path d="M3 17l6-6 4 4 8-8M21 7h-5m5 0v5"/>',
    layers: '<path d="M12 3l9 5-9 5-9-5zM3 13l9 5 9-5"/>',
    coins: '<ellipse cx="9" cy="7" rx="5.5" ry="2.6"/><path d="M3.5 7v6c0 1.44 2.46 2.6 5.5 2.6s5.5-1.16 5.5-2.6V7"/><path d="M12.6 11.6c.9.3 1.9.5 2.9.5 3.04 0 5.5-1.16 5.5-2.6V7m0 4.6v6"/>',
    dot: '<circle cx="12" cy="12" r="4"/>',
    filter: '<path d="M4 5h16l-6.2 7.4V19l-3.6-2v-4.6z"/>',
    copy: '<path d="M9 9h10v12H9zM5 15H4V3h10v1"/>',
    broom: '<path d="M4 20h16M6 20l3-6h6l3 6M12 14V9M9 6.5A3 3 0 1115 6.5"/>',
  };

  const icon = (name, cls = "") =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" ` +
    `stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[name] || P.dot}</svg>`;

  const solid = (name, cls = "") =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">${P[name] || P.dot}</svg>`;

  /* --------------------------------------------------------------- nav model */
  const NAV = [
    { key: "overview", i18n: "nav.overview", href: "index.html", icon: "overview", hintKey: "nav.overview.hint" },
    { key: "preview", i18n: "nav.preview", href: "preview.html", icon: "search", hintKey: "nav.preview.hint" },
    { key: "refresh", i18n: "nav.refresh", href: "refresh.html", icon: "play", hintKey: "nav.refresh.hint" },
    { key: "ranking", i18n: "nav.ranking", href: "ranking.html", icon: "table", hintKey: "nav.ranking.hint" },
    { key: "history", i18n: "nav.history", href: "history.html", icon: "clock", hintKey: "nav.history.hint" },
    { key: "sources", i18n: "nav.sources", href: "sources.html", icon: "database", hintKey: "nav.sources.hint" },
  ];

  /* ------------------------------------------------------------- API client */
  async function parse(response) {
    const text = await response.text();
    let body = null;
    try { body = text ? JSON.parse(text) : null; } catch { body = text; }
    if (!response.ok) {
      const detail = body && typeof body === "object" && body.detail ? body.detail : `HTTP ${response.status}`;
      const err = new Error(detail);
      err.status = response.status;
      err.body = body;
      throw err;
    }
    return body;
  }

  const api = {
    get(path, params) {
      const qs = params ? new URLSearchParams(
        Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")
      ).toString() : "";
      return fetch(path + (qs ? `?${qs}` : ""), { headers: { Accept: "application/json" } }).then(parse);
    },
    post(path, body) {
      return fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      }).then(parse);
    },
    del(path) { return fetch(path, { method: "DELETE" }).then(parse); },
    health: () => api.get("/api/health"),
    config: () => api.get("/api/config"),
    runs: (limit = 60) => api.get("/api/runs", { limit }),
    run: (id) => api.get(`/api/runs/${encodeURIComponent(id)}`),
    dropRun: (id) => api.del(`/api/runs/${encodeURIComponent(id)}`),
    ranking: (opts = {}) => api.get("/api/ranking", opts),
    history: (ticker, limit = 60) => api.get(`/api/history/${encodeURIComponent(ticker)}`, { limit }),
    jobs: (limit = 20) => api.get("/api/jobs", { limit }),
    job: (id) => api.get(`/api/jobs/${encodeURIComponent(id)}`),
    clearCache: (namespace) => api.post("/api/cache/clear" + (namespace ? `?namespace=${encodeURIComponent(namespace)}` : "")),
    // US-stock parameter preview (akshare-backed)
    usTickers: (q = "", limit = 40) => api.get("/api/us/tickers", { q, limit }),
    usParams: (lang) => api.get("/api/us/params", { lang }),
    usPreview: (ticker, pricePoints = 260, lang) =>
      api.get(`/api/us/preview/${encodeURIComponent(ticker)}`, { price_points: pricePoints, lang }),
    // US sector classification + sector-relative ranking
    sectors: () => api.get("/api/sectors"),
    resolveSector: (ticker) => api.get(`/api/sectors/resolve/${encodeURIComponent(ticker)}`),
    sectorRanking: (sector, opts = {}) =>
      api.get(`/api/sectors/${encodeURIComponent(sector)}/ranking`, opts),
    buildSectorIndex: (body) => api.post("/api/sectors/build", body || {}),
  };

  /* ------------------------------------------------------------ formatting */
  const DASH = "—";

  /** Locale-aware fixed-point number (en-US groups thousands). */
  function fmt(value, digits = 2) {
    const n = Number(value);
    if (!Number.isFinite(n)) return DASH;
    try {
      return n.toLocaleString(FRI18n.locale(), {
        minimumFractionDigits: digits, maximumFractionDigits: digits,
      });
    } catch {
      return n.toFixed(digits);
    }
  }

  function num(v, d = 2) {
    return v === null || v === undefined || Number.isNaN(v) ? DASH : fmt(v, d);
  }
  function int(v) {
    return v === null || v === undefined || Number.isNaN(v) ? DASH : fmt(Math.round(Number(v)), 0);
  }
  function pct(v, d = 1) {
    return v === null || v === undefined || Number.isNaN(v) ? DASH : fmt(Number(v) * 100, d) + "%";
  }
  function signedPct(v, d = 1) {
    if (v === null || v === undefined || Number.isNaN(v)) return DASH;
    const n = Number(v) * 100;
    return (n > 0 ? "+" : "") + fmt(n, d) + "%";
  }
  function mult(v, d = 1) {
    return v === null || v === undefined || Number.isNaN(v) ? DASH : fmt(v, d) + "×";
  }
  function money(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return DASH;
    const n = Number(v);
    const a = Math.abs(n);
    const sign = n < 0 ? "-" : "";
    if (a >= 1e12) return `${sign}$${fmt(a / 1e12, 2)}T`;
    if (a >= 1e9) return `${sign}$${fmt(a / 1e9, 1)}B`;
    if (a >= 1e6) return `${sign}$${fmt(a / 1e6, 1)}M`;
    if (a >= 1e3) return `${sign}$${fmt(a / 1e3, 1)}K`;
    return `${sign}$${fmt(a, 2)}`;
  }
  function compact(v, d = 1) {
    if (v === null || v === undefined || Number.isNaN(v)) return DASH;
    const n = Number(v);
    const a = Math.abs(n);
    if (a >= 1e12) return fmt(n / 1e12, d) + "T";
    if (a >= 1e9) return fmt(n / 1e9, d) + "B";
    if (a >= 1e6) return fmt(n / 1e6, d) + "M";
    return fmt(n, d);
  }
  function when(iso) {
    if (!iso) return DASH;
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    const p = (x) => String(x).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  function ago(iso) {
    if (!iso) return DASH;
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return DASH;
    const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
    if (s < 90) return t("time.justNow");
    const m = s / 60;
    if (m < 60) return t("time.minutesAgo", { n: Math.round(m) });
    const h = m / 60;
    if (h < 24) return t("time.hoursAgo", { n: Math.round(h) });
    const dd = h / 24;
    if (dd < 30) return t("time.daysAgo", { n: Math.round(dd) });
    return when(iso).slice(0, 10);
  }

  /* ---------------------------------------------------------- score visuals */
  function tone(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return "na";
    const n = Number(v);
    if (n >= 7.5) return "good";
    if (n >= 6) return "mid";
    if (n >= 4.5) return "soft";
    return "low";
  }

  const PROFILE_TONE = {
    leading: "good", strong: "mid", average: "soft", weak: "low", lagging: "low",
  };
  /** Localised profile label; keeps the workbook's English term as the key. */
  function profileLabel(profile) {
    const key = String(profile || "").toLowerCase();
    return FR.I18N.DICT[`profile.${key}`] ? t(`profile.${key}`) : (profile || DASH);
  }

  /** `<div class="meter">` inner HTML for a 1–10 score. */
  function meter(v) {
    // note: no local named `t` here — that would shadow the global translator
    const shade = tone(v);
    if (shade === "na") return `<div class="meter is-na"><span class="val mono">${DASH}</span></div>`;
    const scale = Math.max(0, Math.min(1, Number(v) / 10));
    // Bar colour comes from the shared diverging scale so the meter matches the
    // matrix swatches on the overview page (red = weak, green = strong).
    const colour = typeof FRScale !== "undefined" ? FRScale.fill(v) : null;
    const style = colour ? ` style="transform:scaleX(${scale.toFixed(3)});background:${colour}"` : ` style="transform:scaleX(${scale.toFixed(3)})"`;
    return `<div class="meter meter--${shade}" title="${num(v, 2)} / 10 · ${t("meter.neutral", { mid: (typeof FRScale !== "undefined" ? FRScale.MID : 5.5) })}">
      <span class="track"><i class="fill"${style}></i></span>
      <span class="val mono">${num(v, 2)}</span>
    </div>`;
  }

  function profilePill(profile) {
    const key = String(profile || "").toLowerCase();
    const label = profileLabel(profile);
    const tone = PROFILE_TONE[key] || "";
    return `<span class="profile-pill"${tone ? ` data-tone="${tone}"` : ""}>${label}</span>`;
  }

  /* ------------------------------------------------------------- DOM helpers */
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function el(tag, attrs = {}, html) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : String(v));
    }
    if (html !== undefined) node.innerHTML = html;
    return node;
  }

  function debounce(fn, ms = 220) {
    let timer = null;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), ms);
    };
  }

  const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));

  /* ------------------------------------------------------------ toast + busy */
  function toastHost() {
    let host = $(".toasts");
    if (!host) {
      host = el("div", { class: "toasts", role: "status", "aria-live": "polite" });
      document.body.appendChild(host);
    }
    return host;
  }

  function toast(message, kind = "ok", ms = 3200) {
    const host = toastHost();
    const node = el("div", { class: `toast ${kind}` }, `<span class="dot"></span><span>${escapeHtml(message)}</span>`);
    host.appendChild(node);
    setTimeout(() => {
      node.classList.add("out");
      setTimeout(() => node.remove(), 320);
    }, ms);
    return node;
  }

  /* ------------------------------------------------------------ empty state */
  function stateBlock({ icon: name = "info", title, body, action } = {}) {
    const act = action
      ? `<div class="act"><a class="btn btn--primary" href="${action.href}">${escapeHtml(action.label)}</a></div>`
      : "";
    return `<div class="state">
      <span class="glyph">${icon(name)}</span>
      <h3>${escapeHtml(title || t("common.noData"))}</h3>
      <p>${body || ""}</p>
      ${act}
    </div>`;
  }

  function skeletonRows(cols, rows = 6) {
    let out = "";
    for (let r = 0; r < rows; r++) {
      out += "<tr>";
      for (let c = 0; c < cols; c++) {
        const w = 40 + ((r * 7 + c * 13) % 55);
        out += `<td><span class="skeleton line" style="display:block;width:${w}%"></span></td>`;
      }
      out += "</tr>";
    }
    return out;
  }

  /* -------------------------------------------------------------- health ping */
  async function ping() {
    try {
      const [health, runs, config] = await Promise.all([api.health(), api.runs(60), api.config()]);
      const state = { health, runs: runs.runs || [], config, ok: true };
      document.dispatchEvent(new CustomEvent("fr:health", { detail: state }));
      return state;
    } catch (err) {
      const state = { ok: false, error: err };
      document.dispatchEvent(new CustomEvent("fr:health", { detail: state }));
      return state;
    }
  }

  /* ------------------------------------------------------------ shell markup */
  function escapePath() {
    const p = location.pathname.split("/").pop();
    return !p || p === "" ? "index.html" : p;
  }

  function mountShell() {
    const here = escapePath();
    const current = NAV.find((n) => n.href === here) || NAV[0];

    // ambient background
    if (!$(".wash")) {
      document.body.insertBefore(el("div", { class: "wash", "aria-hidden": "true" }, "<i></i><i></i><i></i>"), document.body.firstChild);
    }

    // rail
    const rail = el("aside", { class: "rail", id: "rail", "aria-label": t("nav.group") });
    rail.innerHTML = `
      <a class="brand" href="index.html">
        <span class="mark">FR</span>
        <span class="txt">
          <b>FinanceRanker</b>
          <span data-i18n="brand.sub">${t("brand.sub")}</span>
        </span>
      </a>
      <nav class="rail-group" aria-label="${escapeHtml(t("nav.group"))}">
        <p data-i18n="nav.group">${t("nav.group")}</p>
        ${NAV.map((n) => `
          <a class="navlink" href="${n.href}" title="${escapeHtml(t(n.hintKey))}"${n.key === current.key ? ' aria-current="page"' : ""}>
            ${icon(n.icon, "ico")}
            <span data-i18n="${n.i18n}">${t(n.i18n)}</span>
            ${n.key === "history" ? '<span class="n" data-nav-runs>—</span>' : ""}
          </a>`).join("")}
      </nav>
      <div class="rail-foot">
        <div class="langswitch" role="group" aria-label="${escapeHtml(t("lang.toggle"))}">
          <button type="button" data-fr-lang="zh">${t("lang.switch.zh")}</button>
          <button type="button" data-fr-lang="en">${t("lang.switch.en")}</button>
        </div>
        <a class="health" href="sources.html" id="railHealth">
          <span class="dot"></span>
          <span><b>${t("health.checking")}</b><span>${t("health.waiting")}</span></span>
        </a>
        <button class="btn btn--primary" type="button" data-goto-refresh style="width:100%">
          ${solid("play")}<span data-i18n="action.start">${t("action.start")}</span>
        </button>
      </div>`;

    // mobile bar
    const mobilebar = el("div", { class: "mobilebar" });
    mobilebar.innerHTML = `
      <button class="btn btn--ghost btn--icon" type="button" id="railToggle" aria-expanded="false"
        data-i18n-aria="nav.group" aria-label="${escapeHtml(t("nav.group"))}">
        ${icon("menu")}
      </button>
      <span class="mark">FR</span>
      <b data-i18n="${current.i18n}">${t(current.i18n)}</b>
      <span class="spacer"></span>
      <button class="langbtn" type="button" data-fr-lang-toggle>${FRI18n.current() === "zh" ? "EN" : "中文"}</button>
      <button class="btn btn--ghost btn--icon" type="button" data-ping aria-label="${escapeHtml(t("action.refresh"))}">${icon("refresh")}</button>`;

    const scrim = el("div", { class: "scrim", id: "scrim", "aria-hidden": "true" });

    const app = $(".app") || document.body;
    app.insertBefore(rail, app.firstChild);

    const main = $(".main");
    if (main) main.insertBefore(mobilebar, main.firstChild);
    document.body.appendChild(scrim);

    // rail toggle (mobile)
    const closeRail = () => {
      rail.classList.remove("open");
      scrim.classList.remove("in");
      const toggle = $("#railToggle");
      if (toggle) toggle.setAttribute("aria-expanded", "false");
    };
    $("#railToggle")?.addEventListener("click", () => {
      const open = !rail.classList.contains("open");
      rail.classList.toggle("open", open);
      scrim.classList.toggle("in", open);
      $("#railToggle").setAttribute("aria-expanded", String(open));
    });
    scrim.addEventListener("click", closeRail);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeRail(); });

    // global actions
    const goRefresh = () => { location.href = "refresh.html"; };
    $$("[data-goto-refresh]").forEach((b) => b.addEventListener("click", goRefresh));
    $("[data-ping]")?.addEventListener("click", ping);

    let lastHealth = null;

    const paintHealth = () => {
      const node = $("#railHealth");
      if (!node) return;
      const detail = lastHealth;
      if (!detail || !detail.ok) {
        node.className = "health bad";
        node.innerHTML = `<span class="dot"></span><span><b>${t("health.offline")}</b><span>${t("health.offline.hint")}</span></span>`;
        return;
      }
      const { health } = detail;
      const cache = Object.values(health.cache || {}).reduce((a, b) => a + b, 0);
      node.className = "health" + (health.store.runs ? "" : " warn");
      node.innerHTML = `<span class="dot"></span><span>
        <b>${t("health.ready")}${health.providers.yahoo_enabled ? " · Yahoo" : ""}</b>
        <span>${t("health.snapshots", { n: health.store.runs, m: cache })}</span></span>`;
    };

    document.addEventListener("fr:health", (event) => {
      lastHealth = event.detail || null;
      paintHealth();
      const badge = $("[data-nav-runs]");
      if (badge && lastHealth && lastHealth.runs) badge.textContent = lastHealth.runs.length;
    });

    // ---- language switch (rail + mobile bar)
    // `data-fr-*` rather than `data-lang`: the latter also exists on <html> as
    // the document language, and writing textContent into the root element
    // would replace the whole page.
    const paintLangSwitch = () => {
      $$("[data-fr-lang]").forEach((b) => {
        if (b === document.documentElement) return;
        const code = b.dataset.frLang === "zh" ? "lang.switch.zh" : "lang.switch.en";
        b.textContent = t(code);
        b.setAttribute("aria-pressed", String(b.dataset.frLang === FRI18n.current()));
      });
      $$("[data-fr-lang-toggle]").forEach((b) => {
        if (b === document.documentElement) return;
        b.textContent = FRI18n.current() === "zh" ? t("lang.switch.en") : t("lang.switch.zh");
        b.setAttribute("title", t("lang.toggle"));
      });
    };

    const setLang = (next) => {
      FRI18n.set(next);
      paintLangSwitch();
      paintHealth();
      document.dispatchEvent(new CustomEvent("fr:lang", { detail: { lang: FRI18n.current() } }));
    };

    $$("[data-fr-lang]").forEach((b) => b.addEventListener("click", () => setLang(b.dataset.frLang)));
    $$("[data-fr-lang-toggle]").forEach((b) =>
      b.addEventListener("click", () => setLang(FRI18n.current() === "zh" ? "en" : "zh")));
    FRI18n.onChange(() => {
      FRI18n.applyI18n();
      paintLangSwitch();
      paintHealth();
    });
    paintLangSwitch();

    return { current, ping, setLang };
  }

  /* ---------------------------------------------------------------- boot */
  function boot() {
    const shell = mountShell();
    // Health lands independently of page data so the nav status updates even
    // when a page is still loading its own tables.
    ping();
    if (typeof window.pageInit === "function") window.pageInit(shell);
    // keep the sidebar status fresh without re-rendering the page
    setInterval(ping, 60000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  return {
    api, icon, solid, NAV, I18N: FRI18n,
    num, int, pct, signedPct, mult, money, compact, fmt, when, ago,
    tone, meter, profilePill, profileLabel, DASH,
    $, $$, el, debounce, escapeHtml, toast, stateBlock, skeletonRows, ping,
  };
})();
