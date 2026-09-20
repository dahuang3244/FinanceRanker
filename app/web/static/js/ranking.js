/* Ranking — client-side filter, sort, column control and export. */
"use strict";

(() => {
  const {
    api, icon, solid, $, $$, el, escapeHtml, toast, stateBlock, debounce,
    num, pct, signedPct, mult, money, when, DASH,
  } = FR;

  const PREF_KEY = "fr.ranking.prefs";

  // Size of the scoring universe, used to label the coverage meter honestly.
  // Kept in step with `app/engine/scoring.py::METRICS`.
  const METRIC_COUNT = 32;

  const VIEWS = [
    { key: "overall", i18n: "rk.view.overall", col: "score_overall" },
    { key: "growth", i18n: "rk.view.growth", col: "score_growth" },
    { key: "profitability", i18n: "rk.view.profitability", col: "score_profitability" },
    { key: "cash", i18n: "rk.view.cash", col: "score_cash" },
    { key: "valuation", i18n: "rk.view.valuation", col: "score_valuation" },
    { key: "market", i18n: "rk.view.market", col: "score_market" },
  ];

  // A renderer returns the cell *contents*; `decorateCell` wraps it so the
  // class list (alignment, frozen identity, group boundary) is applied in one
  // place and can never drift between <th> and <td>.
  const numCell = (v, d = 2) => `<span class="mono">${num(v, d)}</span>`;
  const pctCell = (v, d = 1) =>
    `<span class="mono" style="color:${v === null || v === undefined ? "var(--faint)" : v >= 0 ? "var(--good)" : "var(--low)"}">${signedPct(v, d)}</span>`;
  const multCell = (v) => `<span class="mono">${mult(v)}</span>`;

  // keys whose data is numeric → header + cells align right
  const RIGHT = new Set([
    "rank", "score_growth", "score_profitability", "score_cash", "score_valuation", "score_market",
    "score_overall", "price", "market_cap", "return_1m", "return_3m", "return_6m", "return_1y",
    "return_ytd", "return_3y", "excess_return_3m", "excess_return_6m", "excess_return_1y",
    "benchmark_return_3m", "benchmark_return_6m", "benchmark_return_1y", "revenue_growth_yoy",
    "revenue_cagr_5y", "gross_margin", "operating_margin", "net_margin", "roe", "roic", "fcf_yield",
    "forward_pe", "price_to_sales", "ev_to_ebitda", "price_to_book", "price_to_fcf", "beta",
    "beta_1y", "volatility", "downside_deviation", "sharpe_ratio", "sortino_ratio",
    "max_drawdown_1y", "drawdown_52w", "analyst_target", "analyst_upside", "coverage_pct",
    "data_coverage",
  ]);

  const COLS = [
    {
      key: "rank", label: () => t("common.rank"), sortable: true, width: "48px",
      freeze: 1,
      value: (r) => (r.rank === null || r.rank === undefined ? Number.POSITIVE_INFINITY : r.rank),
      cell: "rank-cell num",
      cellExtra: (r) => (r.rank && r.rank <= 3 ? "top" : ""),
      render: (r) => `${r.rank ?? DASH}`,
    },
    {
      key: "ticker", label: () => t("common.ticker"), sortable: true,
      width: "82px", freeze: 2, value: (r) => r.ticker, cell: "ticker",
      render: (r) => `<a class="tick-link" href="${detailHref(r.ticker)}">${escapeHtml(r.ticker)}</a>`,
    },
    {
      key: "company", label: () => t("common.company"), sortable: true,
      width: "190px", value: (r) => (r.company || "").toLowerCase(), cell: "company",
      render: (r) => `<span title="${escapeHtml(r.company || "")}">${escapeHtml(r.company || DASH)}</span>`,
    },
    {
      key: "detail", label: () => t("action.expand"), sortable: false, width: "82px",
      value: () => "",
      render: (r) => `<a class="detail-link" href="${detailHref(r.ticker)}"
        title="${escapeHtml(t("action.detail"))} · ${escapeHtml(r.ticker)}">${
        t("action.expand")}<span class="chev">${icon("chevron")}</span></a>`,
    },
    { key: "score_growth", label: () => t("dim.growth"), sortable: true, width: "92px", cell: "dimcell", value: (r) => r.score_growth, render: (r) => FR.meter(r.score_growth) },
    { key: "score_profitability", label: () => t("dim.profitability"), sortable: true, width: "92px", cell: "dimcell", value: (r) => r.score_profitability, render: (r) => FR.meter(r.score_profitability) },
    { key: "score_cash", label: () => t("dim.cash"), sortable: true, width: "92px", cell: "dimcell", value: (r) => r.score_cash, render: (r) => FR.meter(r.score_cash) },
    { key: "score_valuation", label: () => t("dim.valuation"), sortable: true, width: "92px", cell: "dimcell", value: (r) => r.score_valuation, render: (r) => FR.meter(r.score_valuation) },
    { key: "score_market", label: () => t("dim.market"), sortable: true, width: "92px", cell: "dimcell", value: (r) => r.score_market, render: (r) => FR.meter(r.score_market) },
    { key: "score_overall", label: () => t("common.overall"), sortable: true, width: "104px", cell: "dimcell", value: (r) => r.score_overall, render: (r) => FR.meter(r.score_overall) },
    { key: "profile", label: () => t("common.profile"), sortable: false, width: "84px", value: (r) => r.profile, render: (r) => FR.profilePill(r.profile) },
    { key: "price", label: () => t("col.price"), sortable: true, width: "84px", value: (r) => r.price, render: (r) => numCell(r.price) },
    { key: "market_cap", label: () => t("col.marketCap"), sortable: true, width: "92px", value: (r) => r.market_cap, render: (r) => `<span class="mono">${money(r.market_cap)}</span>` },

    /* ------------------------------------------------ returns & vs SPY */
    { key: "return_1m", label: () => t("m.return_1m"), sortable: true, width: "86px", value: (r) => r.return_1m, render: (r) => pctCell(r.return_1m) },
    { key: "return_3m", label: () => t("m.return_3m"), sortable: true, width: "86px", value: (r) => r.return_3m, render: (r) => pctCell(r.return_3m) },
    { key: "return_6m", label: () => t("m.return_6m"), sortable: true, width: "86px", value: (r) => r.return_6m, render: (r) => pctCell(r.return_6m) },
    { key: "return_1y", label: () => t("m.return_1y"), sortable: true, width: "88px", value: (r) => r.return_1y, render: (r) => pctCell(r.return_1y) },
    { key: "return_ytd", label: () => t("m.return_ytd"), sortable: true, width: "88px", value: (r) => r.return_ytd, render: (r) => pctCell(r.return_ytd) },
    { key: "return_3y", label: () => t("m.return_3y"), sortable: true, width: "88px", value: (r) => r.return_3y, render: (r) => pctCell(r.return_3y) },
    { key: "excess_return_3m", label: () => t("m.excess_return_3m"), sortable: true, width: "104px", value: (r) => r.excess_return_3m, render: (r) => pctCell(r.excess_return_3m) },
    { key: "excess_return_6m", label: () => t("m.excess_return_6m"), sortable: true, width: "104px", value: (r) => r.excess_return_6m, render: (r) => pctCell(r.excess_return_6m) },
    { key: "excess_return_1y", label: () => t("m.excess_return_1y"), sortable: true, width: "104px", value: (r) => r.excess_return_1y, render: (r) => pctCell(r.excess_return_1y) },
    { key: "benchmark_return_3m", label: () => t("m.benchmark_return_3m"), sortable: true, width: "92px", value: (r) => r.benchmark_return_3m, render: (r) => pctCell(r.benchmark_return_3m) },
    { key: "benchmark_return_6m", label: () => t("m.benchmark_return_6m"), sortable: true, width: "92px", value: (r) => r.benchmark_return_6m, render: (r) => pctCell(r.benchmark_return_6m) },
    { key: "benchmark_return_1y", label: () => t("m.benchmark_return_1y"), sortable: true, width: "92px", value: (r) => r.benchmark_return_1y, render: (r) => pctCell(r.benchmark_return_1y) },

    /* ------------------------------------------------------------- risk */
    { key: "volatility", label: () => t("m.volatility"), sortable: true, width: "96px", value: (r) => r.volatility, render: (r) => pctCell(r.volatility) },
    { key: "sharpe_ratio", label: () => t("m.sharpe_ratio"), sortable: true, width: "84px", value: (r) => r.sharpe_ratio, render: (r) => numCell(r.sharpe_ratio, 2) },
    { key: "sortino_ratio", label: () => t("m.sortino_ratio"), sortable: true, width: "88px", value: (r) => r.sortino_ratio, render: (r) => numCell(r.sortino_ratio, 2) },
    { key: "beta", label: () => t("m.beta"), sortable: true, width: "92px", value: (r) => r.beta, render: (r) => numCell(r.beta, 2) },
    { key: "beta_1y", label: () => t("m.beta_1y"), sortable: true, width: "84px", value: (r) => r.beta_1y, render: (r) => numCell(r.beta_1y, 2) },
    { key: "max_drawdown_1y", label: () => t("m.max_drawdown_1y"), sortable: true, width: "104px", value: (r) => r.max_drawdown_1y, render: (r) => pctCell(r.max_drawdown_1y) },
    { key: "drawdown_52w", label: () => t("m.drawdown_52w"), sortable: true, width: "96px", value: (r) => r.drawdown_52w, render: (r) => pctCell(r.drawdown_52w) },

    /* -------------------------------------------------------- consensus */
    { key: "analyst_target", label: () => t("m.analyst_target"), sortable: true, width: "92px", value: (r) => r.analyst_target, render: (r) => numCell(r.analyst_target) },
    { key: "analyst_upside", label: () => t("m.analyst_upside"), sortable: true, width: "96px", value: (r) => r.analyst_upside, render: (r) => pctCell(r.analyst_upside) },

    /* ------------------------------------------------------ fundamentals */
    { key: "revenue_growth_yoy", label: () => t("m.revenue_growth_yoy"), sortable: true, width: "96px", value: (r) => r.revenue_growth_yoy, render: (r) => pctCell(r.revenue_growth_yoy) },
    { key: "revenue_cagr_5y", label: () => t("m.revenue_cagr_5y"), sortable: true, width: "96px", value: (r) => r.revenue_cagr_5y, render: (r) => pctCell(r.revenue_cagr_5y) },
    { key: "gross_margin", label: () => t("m.gross_margin"), sortable: true, width: "88px", value: (r) => r.gross_margin, render: (r) => numCell(r.gross_margin === null || r.gross_margin === undefined ? null : r.gross_margin * 100, 1) },
    { key: "operating_margin", label: () => t("m.operating_margin"), sortable: true, width: "96px", value: (r) => r.operating_margin, render: (r) => numCell(r.operating_margin === null || r.operating_margin === undefined ? null : r.operating_margin * 100, 1) },
    { key: "net_margin", label: () => t("m.net_margin"), sortable: true, width: "88px", value: (r) => r.net_margin, render: (r) => numCell(r.net_margin === null || r.net_margin === undefined ? null : r.net_margin * 100, 1) },
    { key: "roe", label: () => t("m.roe"), sortable: true, width: "80px", value: (r) => r.roe, render: (r) => numCell(r.roe === null || r.roe === undefined ? null : r.roe * 100, 1) },
    { key: "roic", label: () => t("m.roic"), sortable: true, width: "80px", value: (r) => r.roic, render: (r) => numCell(r.roic === null || r.roic === undefined ? null : r.roic * 100, 1) },
    { key: "fcf_yield", label: () => t("m.fcf_yield"), sortable: true, width: "96px", value: (r) => r.fcf_yield, render: (r) => pctCell(r.fcf_yield) },
    { key: "forward_pe", label: () => t("m.forward_pe"), sortable: true, width: "108px", value: (r) => r.forward_pe, render: (r) => multCell(r.forward_pe) },
    { key: "price_to_sales", label: () => t("m.price_to_sales"), sortable: true, width: "92px", value: (r) => r.price_to_sales, render: (r) => multCell(r.price_to_sales) },
    { key: "ev_to_ebitda", label: () => t("m.ev_to_ebitda"), sortable: true, width: "108px", value: (r) => r.ev_to_ebitda, render: (r) => multCell(r.ev_to_ebitda) },
    { key: "price_to_book", label: () => t("m.price_to_book"), sortable: true, width: "96px", value: (r) => r.price_to_book, render: (r) => multCell(r.price_to_book) },
    { key: "price_to_fcf", label: () => t("m.price_to_fcf"), sortable: true, width: "96px", value: (r) => r.price_to_fcf, render: (r) => multCell(r.price_to_fcf) },

    /* ---------------------------------------------------------- metadata */
    {
      key: "coverage_pct", label: () => t("common.coverage"), sortable: true, width: "96px",
      cell: "right",
      value: (r) => r.coverage_pct,
      render: (r) => `<span title="${r.data_coverage} / ${METRIC_COUNT} ${escapeHtml(t("common.metricsPresent"))}">${
        FR.meter(r.coverage_pct === null || r.coverage_pct === undefined ? null : r.coverage_pct * 10)}</span>`,
    },
    { key: "status", label: () => t("rf.jobs.status"), sortable: true, width: "92px",
      value: (r) => r.status || "",
      render: (r) => `<span class="faint" style="font-size:11.5px">${escapeHtml(r.status || DASH)}</span>` },
  ];

  const COL_BY_KEY = Object.fromEntries(COLS.map((c) => [c.key, c]));

  /* Column groups. `grp` drives the boundary rule and the small caption above
     the first column of each block, so 29 metrics read as four labelled sets
     instead of one undifferentiated field. */
  const GROUP_OF = {
    score_growth: "scores", score_profitability: "scores", score_cash: "scores",
    score_valuation: "scores", score_market: "scores", score_overall: "scores",
    profile: "scores",
    return_1m: "returns", return_3m: "returns", return_6m: "returns",
    return_1y: "returns", return_ytd: "returns", return_3y: "returns",
    excess_return_3m: "returns", excess_return_6m: "returns", excess_return_1y: "returns",
    benchmark_return_3m: "returns", benchmark_return_6m: "returns",
    benchmark_return_1y: "returns",
    volatility: "risk", sharpe_ratio: "risk", sortino_ratio: "risk",
    beta: "risk", beta_1y: "risk", max_drawdown_1y: "risk", drawdown_52w: "risk",
    analyst_target: "consensus", analyst_upside: "consensus",
    revenue_growth_yoy: "fundamentals", revenue_cagr_5y: "fundamentals",
    gross_margin: "fundamentals", operating_margin: "fundamentals",
    net_margin: "fundamentals", roe: "fundamentals", roic: "fundamentals",
    fcf_yield: "fundamentals", forward_pe: "fundamentals",
    price_to_sales: "fundamentals", ev_to_ebitda: "fundamentals",
    price_to_book: "fundamentals", price_to_fcf: "fundamentals",
  };
  const GROUP_LABEL = {
    scores: "rk.grp.scores", returns: "rk.grp.returns",
    risk: "rk.grp.risk", consensus: "rk.grp.consensus",
    fundamentals: "rk.grp.fundamentals",
  };

  /** Header tooltips: the long-form hint for a metric, falling back to its label. */
  function hintFor(col) {
    const label = typeof col.label === "function" ? col.label() : col.label;
    const hint = t(`m.${col.key}.hint`);
    return hint === `m.${col.key}.hint` ? label : `${label} — ${hint}`;
  }

  const state = {
    rows: [],
    stale: null,
    view: "overall",
    strategy: null,          // resolved from GET /api/strategies on first load
    strategies: [],
    sortKey: "rank",
    sortDir: 1,
    q: "",
    profile: "",
    eligibleOnly: false,
    // Start with the comparison the screen is actually for: the five dimension
    // scores plus the return/risk block. Everything else stays one click away
    // in the column picker rather than making the default view unreadable.
    hidden: new Set([
      "company", "detail", "status", "profile", "price", "market_cap",
      "return_ytd", "return_3y", "benchmark_return_3m", "benchmark_return_6m",
      "benchmark_return_1y", "sortino_ratio", "analyst_target", "coverage_pct",
      "revenue_growth_yoy", "revenue_cagr_5y", "gross_margin", "operating_margin",
      "net_margin", "roe", "roic", "fcf_yield", "forward_pe", "price_to_sales",
      "ev_to_ebitda", "price_to_book", "price_to_fcf",
    ]),
    sourceLabel: "最近快照",
  };

  /* -------------------------------------------------------------- preferences */
  function loadPrefs() {
    try {
      const raw = JSON.parse(localStorage.getItem(PREF_KEY) || "{}");
      if (raw.view && VIEWS.some((v) => v.key === raw.view)) state.view = raw.view;
      if (typeof raw.strategy === "string" && raw.strategy) state.strategy = raw.strategy;
      if (Array.isArray(raw.hidden) && raw.hidden.length) {
        // only keep keys that still exist, and never hide everything
        const valid = raw.hidden.filter((k) => COL_BY_KEY[k]);
        if (valid.length && valid.length < COLS.length) state.hidden = new Set(valid);
      }
    } catch { /* first visit */ }
  }

  function savePrefs() {
    try {
      localStorage.setItem(PREF_KEY, JSON.stringify({
        view: state.view, hidden: [...state.hidden], strategy: state.strategy,
      }));
    } catch { /* storage may be unavailable */ }
  }

  /* ---------------------------------------------------------------- strategy */
  /* The component blend is a stated choice, not a constant buried in config.
     Changing it re-ranks the same percentile scores, so the numbers a reader
     already saw do not change — only their weighting does. */
  async function ensureStrategies() {
    if (state.strategies.length) return;
    try {
      const data = await api.strategies();
      state.strategies = data.strategies || [];
      const keys = state.strategies.map((s) => s.key);
      if (!state.strategy || !keys.includes(state.strategy)) state.strategy = data.default;
    } catch {
      // Without the endpoint the ranking still loads under the server default.
      state.strategies = [];
    }
  }

  function activeStrategy() {
    return state.strategies.find((s) => s.key === state.strategy) || null;
  }

  function renderStrategySeg() {
    const host = $("#strategySeg");
    if (!host) return;
    if (!state.strategies.length) {
      host.innerHTML = "";
      const blurb = $("#strategyBlurb");
      if (blurb) blurb.textContent = "";
      return;
    }
    host.innerHTML = state.strategies.map((s) =>
      `<button type="button" data-strategy="${escapeHtml(s.key)}"
        aria-pressed="${state.strategy === s.key}"
        title="${escapeHtml(weightsSummary(s))}">${escapeHtml(strategyLabel(s))}</button>`).join("");
    $$("#strategySeg button").forEach((b) =>
      b.addEventListener("click", async () => {
        if (state.strategy === b.dataset.strategy) return;
        state.strategy = b.dataset.strategy;
        savePrefs();
        renderStrategySeg();
        await reload();
      }));
    const active = activeStrategy();
    const blurb = $("#strategyBlurb");
    if (blurb && active) {
      blurb.innerHTML = `${escapeHtml(strategyBlurb(active))} <span class="mono faint">${
        escapeHtml(weightsSummary(active))}</span>`;
    }
  }

  /** Label in the current language, from the {zh, en} pair the API returns. */
  function strategyLang() {
    return (typeof FRI18n !== "undefined" && FRI18n.current && FRI18n.current()) || "zh";
  }

  function strategyLabel(s) {
    const pair = s.label || {};
    const lang = strategyLang();
    return pair[lang] || pair.en || pair.zh || s.key;
  }

  function strategyBlurb(s) {
    const pair = s.blurb || {};
    const lang = strategyLang();
    return pair[lang] || pair.en || pair.zh || "";
  }

  function weightsSummary(s) {
    const w = s.weights || {};
    return Object.keys(w).map((k) => `${t(`dim.${k}`)} ${Math.round(w[k] * 100)}%`).join(" · ");
  }

  /* ------------------------------------------------------------------ render */
  function visibleCols() {
    return COLS.filter((c) => !state.hidden.has(c.key));
  }

  function compare(a, b) {
    const col = COL_BY_KEY[state.sortKey] || COL_BY_KEY.rank;
    const va = col.value(a);
    const vb = col.value(b);
    const na = va === null || va === undefined || Number.isNaN(va);
    const nb = vb === null || vb === undefined || Number.isNaN(vb);
    if (na && nb) return 0;
    if (na) return 1;   // missing values always sink
    if (nb) return -1;
    const cmp = typeof va === "string" ? va.localeCompare(vb, "zh-Hans-CN") : va - vb;
    return cmp * state.sortDir;
  }

  function filtered() {
    const q = state.q.trim().toLowerCase();
    return state.rows.filter((r) => {
      if (state.profile && (r.profile || "").toLowerCase() !== state.profile) return false;
      if (q) {
        const hay = `${r.ticker} ${r.company || ""}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    }).sort(compare);
  }

  /** Extra classes for a column: frozen identity columns and group boundaries. */
  function colClasses(col, visible) {
    const cols = visible || visibleCols();
    const index = cols.findIndex((c) => c.key === col.key);
    const previous = index > 0 ? cols[index - 1] : null;
    const group = GROUP_OF[col.key] || "";
    const prevGroup = previous ? (GROUP_OF[previous.key] || "") : null;
    const opensGroup = Boolean(group) && group !== prevGroup;
    return [
      RIGHT.has(col.key) ? "right" : "",
      col.freeze ? `col-sticky col-freeze-${col.freeze}` : "",
      // Group caption only when this column opens a new labelled block.
      opensGroup ? "grp-start" : "",
    ].filter(Boolean).join(" ");
  }

  /** Precompute the per-column class list once per render (not once per cell). */
  function classMap() {
    const cols = visibleCols();
    return { cols, byKey: Object.fromEntries(cols.map((c) => [c.key, colClasses(c, cols)])) };
  }

  /** Wrap rendered contents in a <td> carrying alignment, freeze and group rules. */
  function decorateCell(inner, classes) {
    const cls = classes ? ` class="${classes}"` : "";
    return `<td${cls}>${inner}</td>`;
  }

  function renderHead() {
    const { cols, byKey } = classMap();
    // Size by content: a column ends up as wide as its widest of {header, cells},
    // and the declared `width` is only a floor used as a nudge.
    const minWidth = cols.reduce((sum, c) => sum + (parseFloat(c.width) || 90), 0);
    const head = $("#headRow");
    const table = head && head.closest("table");
    if (table) table.style.minWidth = `${Math.max(minWidth, 720)}px`;
    $("#cols").innerHTML = cols.map((c) => `<col style="width:${c.width}" />`).join("");
    head.innerHTML = cols.map((c) => {
      const active = state.sortKey === c.key;
      const aria = active ? ` aria-sort="${state.sortDir === 1 ? "ascending" : "descending"}"` : "";
      const arrow = active ? (state.sortDir === 1 ? "▲" : "▼") : "▲";
      const cls = [c.sortable ? "sortable" : "", byKey[c.key]].filter(Boolean).join(" ");
      const label = typeof c.label === "function" ? c.label() : c.label;
      const group = GROUP_OF[c.key];
      const groupAttr = group && byKey[c.key].includes("grp-start")
        ? ` data-group="${escapeHtml(t(GROUP_LABEL[group]))}"` : "";
      return `<th class="${cls}"${c.width ? ` style="width:${c.width}"` : ""}${aria}${groupAttr}${
        c.sortable ? ` data-sort="${c.key}"` : ""} title="${escapeHtml(hintFor(c))}">${
        label}<span class="arrow">${arrow}</span></th>`;
    }).join("");

    $$("#headRow th[data-sort]").forEach((th) =>
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        if (state.sortKey === key) state.sortDir = -state.sortDir;
        else { state.sortKey = key; state.sortDir = 1; }
        render();
      }));
  }

  function renderBody(rows) {
    const body = $("#body");
    if (!state.rows.length) {
      body.innerHTML = "";
      $("#emptyState").innerHTML = `<div class="glass" style="margin-top:16px">${stateBlock({
        icon: "table",
        title: t("rk.empty.title"),
        body: t("rk.empty.body"),
        action: { href: "refresh.html", label: t("action.start") },
      })}</div>`;
      $("#tableWrap").hidden = true;
      return;
    }
    $("#tableWrap").hidden = false;
    if (!rows.length) {
      body.innerHTML = "";
      $("#emptyState").innerHTML = `<div class="glass" style="margin-top:16px">${stateBlock({
        icon: "search",
        title: t("rk.nomatch.title"),
        body: t("rk.nomatch.body"),
      })}<div style="display:flex;justify-content:center;padding-bottom:26px">
        <button class="btn btn--quiet" type="button" id="clearFilters">清空筛选</button></div></div>`;
      $("#clearFilters").addEventListener("click", resetFilters);
      return;
    }

    $("#emptyState").innerHTML = "";
    // Reuse the class list the header just computed, so group rules and frozen
    // columns line up between <th> and <td> (and no per-cell recomputation).
    const { cols, byKey } = classMap();
    const html = rows.map((r, i) => `<tr class="rowlink" data-row="${escapeHtml(r.ticker)}"
        style="animation:tag-in .4s var(--ease) backwards;animation-delay:${Math.min(i * 14, 320)}ms">
      ${cols.map((c) => decorateCell(c.render(r), byKey[c.key])).join("")}
    </tr>`).join("");
    body.innerHTML = html;

    body.querySelectorAll("tr[data-row]").forEach((tr) => {
      tr.addEventListener("click", (event) => {
        if (event.target.closest("button, a")) return;
        location.href = detailHref(tr.dataset.row);
      });
    });
  }

  /** Company detail page, carrying the current snapshot context. */
  function detailHref(ticker) {
    const qs = params();
    qs.set("ticker", ticker);
    return `company.html?${qs.toString()}`;
  }

  function renderChips(rows) {
    const all = state.rows;
    const eligible = all.filter((r) => r.rank_eligible);
    const scores = all.map((r) => r.score_overall).filter((v) => v !== null && v !== undefined);
    const avg = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null;
    const shown = all.length === rows.length
      ? `${rows.length} ${t("common.tickers")}`
      : `${rows.length} / ${all.length} ${t("common.tickers")}`;
    $("#rowMeta").textContent = rows.length
      ? t("rk.meta", { shown, eligible: eligible.length, avg: num(avg, 2) })
      : t("rk.meta.none");
    $("#exportCount").textContent = String(rows.length);
    $("#exportUnit").textContent = t("common.rows");
    $("#exportBar").hidden = rows.length === 0;
  }

  function updateExportBar() {
    const unit = $("#exportUnit");
    if (unit) unit.textContent = t("common.rows");
  }

  /* Same 0–10 diverging scale as the overview matrix, so a score reads the same
     whether you meet it in a swatch or in a meter bar. */
  function renderLegend() {
    const host = $("#rankLegend");
    if (!host || typeof FRScale === "undefined") return;
    host.innerHTML = FRScale.legendSteps().map((i) => {
      const { background, color } = FRScale.swatch(i);
      return `<span class="scale-legend-cell" style="background:${background};color:${color}"
        title="${i} / 10">${i}</span>`;
    }).join("");
  }

  function render() {
    const rows = filtered();
    renderHead();
    renderBody(rows);
    renderChips(rows);
    renderLegend();
  }

  /* ---------------------------------------------------------------- controls */
  function renderViewSeg() {
    $("#viewSeg").innerHTML = VIEWS.map((v) =>
      `<button type="button" data-view="${v.key}" aria-pressed="${state.view === v.key}">${t(v.i18n)}</button>`).join("");
    $$("#viewSeg button").forEach((b) =>
      b.addEventListener("click", () => {
        state.view = b.dataset.view;
        state.sortKey = VIEWS.find((v) => v.key === state.view).col;
        state.sortDir = -1;
        savePrefs();
        renderViewSeg();
        render();
      }));
  }

  /* ------------------------------------------------- column picker (popover) */
  function colParts() {
    const panel = document.querySelector(".colpanel");
    return {
      panel,
      summary: panel && panel.querySelector("summary"),
      list: panel && panel.querySelector(".coltoggles"),
    };
  }

  /** Place the top-layer picker directly under its trigger button. */
  function placeColPanel() {
    const { panel, summary, list } = colParts();
    if (!panel || !summary || !list || !panel.open) return;
    const r = summary.getBoundingClientRect();
    const width = Math.min(420, window.innerWidth - 32);
    list.style.width = `${width}px`;
    list.style.top = `${Math.round(r.bottom + 8)}px`;
    list.style.left = `${Math.round(Math.max(16, Math.min(r.right - width, window.innerWidth - width - 16)))}px`;
  }

  function openColPanel() {
    const { list } = colParts();
    if (!list) return;
    try {
      if (!list.matches(":popover-open")) list.showPopover();
    } catch { /* popover unsupported: it already renders inline */ }
    placeColPanel();
  }

  function closeColPanel() {
    const { panel, list } = colParts();
    if (panel) panel.open = false;
    if (!list) return;
    try {
      if (list.matches(":popover-open")) list.hidePopover();
    } catch { /* nothing to close */ }
  }

  function renderColToggles() {
    $("#colToggles").innerHTML = COLS.map((c) => {
      const label = typeof c.label === "function" ? c.label() : c.label;
      return `
      <button class="chip${state.hidden.has(c.key) ? "" : " is-on"}" type="button" data-col="${c.key}"
        aria-pressed="${state.hidden.has(c.key) ? "false" : "true"}" title="${escapeHtml(label)}">${escapeHtml(label)}</button>`;
    }).join("");
    $$("#colToggles [data-col]").forEach((b) =>
      b.addEventListener("click", () => {
        const key = b.dataset.col;
        if (state.hidden.has(key)) state.hidden.delete(key);
        else if (visibleCols().length > 1) state.hidden.add(key);
        else return;
        savePrefs();
        renderColToggles();
        placeColPanel();
        render();
      }));
  }

  function resetFilters() {
    state.q = "";
    state.profile = "";
    $("#q").value = "";
    $("#profileFilter").value = "";
    if (state.eligibleOnly) {
      state.eligibleOnly = false;
      $("#eligibleOnly").checked = false;
      reload();
    } else {
      render();
    }
  }

  /* ------------------------------------------------------------------ export */
  async function copyTsv() {
    const cols = visibleCols();
    const rows = filtered();
    const head = cols.map((c) => c.label).join("\t");
    const lines = rows.map((r) => cols.map((c) => {
      const v = c.value(r);
      return v === null || v === undefined ? "" : String(v);
    }).join("\t"));
    const text = [head, ...lines].join("\n");
    try {
      await navigator.clipboard.writeText(text);
      toast(t("rk.toast.copied", { n: rows.length }));
    } catch {
      const area = el("textarea", { style: "position:fixed;opacity:0" });
      area.value = text;
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
      toast(t("rk.toast.copiedShort"), "ok");
    }
  }

  /* ---------------------------------------------------------------- staleness */
  /* A snapshot written before the current metric set renders as a screen full
     of blanks that is indistinguishable from a broken data source. Say which
     it is, and offer the one action that fixes it. */
  function metricName(key) {
    const label = t(`m.${key}`);
    return label === `m.${key}` ? key : label;
  }

  function renderStale(info) {
    const host = $("#staleNotice");
    if (!host) return;
    if (!info || !info.stale) {
      host.hidden = true;
      host.innerHTML = "";
      return;
    }
    const qs = params();
    qs.delete("job_id");
    qs.delete("run_id");
    const href = `refresh.html?${qs.toString()}`;
    let body;
    if (info.reason === "older_metrics") {
      const names = (info.missing_metrics || []).slice(0, 6).map(metricName).join("、");
      const more = (info.missing_metrics || []).length > 6
        ? ` 等 ${info.missing_metrics.length} 项` : "";
      body = t("rk.stale.body", {
        n: (info.missing_metrics || []).length,
        metrics: names + more,
      });
    } else {
      body = t("rk.stale.old", {
        hours: Math.round(info.age_hours || 0),
        limit: Math.round(info.stale_after_hours || 36),
      });
    }
    host.hidden = false;
    host.innerHTML = `<div class="stale-card" role="status">
      <b>${escapeHtml(t("rk.stale.title"))}</b>
      <p>${escapeHtml(body)}</p>
      <a class="btn btn--primary" href="${href}">${escapeHtml(t("rk.stale.cta"))}</a>
    </div>`;
  }

  /* -------------------------------------------------------------------- load */
  function params() {
    const qs = new URLSearchParams(location.search);
    return qs;
  }

  async function reload() {
    const qs = params();
    try {
      const data = await api.ranking({
        job_id: qs.get("job_id") || undefined,
        run_id: qs.get("run_id") || undefined,
        eligible_only: state.eligibleOnly || undefined,
        strategy: state.strategy || undefined,
      });
      state.rows = data.rows || [];
      state.errors = Object.entries(data.errors || {});
      state.stale = data.staleness || null;
      if (data.strategy) state.strategy = data.strategy;
      state.weights = data.weights || null;
      state.sourceKey = qs.get("run_id")
        ? ["rk.source.run", { id: qs.get("run_id") }]
        : qs.get("job_id") ? ["rk.source.job", null] : ["common.latestSnapshot", null];
      state.sourceLabel = t(state.sourceKey[0], state.sourceKey[1]);
      $("#sourceBadge").innerHTML = `<span class="dot"></span>${escapeHtml(state.sourceLabel)}`;
      renderStale(state.stale);
      render();
      if (state.errors.length) {
        toast(t("rk.toast.failed", { n: state.errors.length }), "warn", 4200);
      }
    } catch (err) {
      state.rows = [];
      state.stale = null;
      renderStale(null);
      $("#sourceBadge").textContent = err.status === 404 ? t("common.noData") : t("common.readFailed");
      render();
      if (err.status !== 404) toast(err.message || t("rk.err.load"), "err");
    }
  }

  window.pageInit = async function pageInit() {
    loadPrefs();
    await ensureStrategies();

    $("#searchIcon").innerHTML = icon("search");
    $("#toRefresh").innerHTML = `${solid("play")}<span data-i18n="action.go.refresh">${t("action.go.refresh")}</span>`;
    $("#resetFilters").innerHTML = `${icon("x")}<span data-i18n="action.reset">${t("action.reset")}</span>`;
    $("#csvBtn").innerHTML = `${icon("download")}<span data-i18n="action.export.csv">${t("action.export.csv")}</span>`;
    $("#xlsxBtn").innerHTML = `${icon("download")}<span data-i18n="action.export.xlsx">${t("action.export.xlsx")}</span>`;
    $("#copyBtn").innerHTML = `${icon("copy")}<span data-i18n="action.copy.table">${t("action.copy.table")}</span>`;

    renderViewSeg();
    renderStrategySeg();
    renderColToggles();

    const { panel: colPanel, list: colList } = colParts();
    if (colPanel) {
      colPanel.addEventListener("toggle", () => {
        if (colPanel.open) openColPanel();
        else {
          try { if (colList.matches(":popover-open")) colList.hidePopover(); } catch { /* noop */ }
        }
      });
      // dismissing the popover (Esc / outside click) must collapse the trigger too
      if (colList) colList.addEventListener("toggle", (e) => {
        if (e.newState === "closed" && colPanel.open) colPanel.open = false;
      });
      window.addEventListener("scroll", () => placeColPanel(), { passive: true });
      window.addEventListener("resize", () => placeColPanel(), { passive: true });
    }
    state.sortKey = VIEWS.find((v) => v.key === state.view).col;
    state.sortDir = -1;

    $("#q").addEventListener("input", debounce((e) => {
      state.q = e.target.value;
      render();
    }, 200));
    $("#profileFilter").addEventListener("change", (e) => {
      state.profile = e.target.value;
      render();
    });
    $("#eligibleOnly").addEventListener("change", (e) => {
      state.eligibleOnly = e.target.checked;
      reload();
    });
    $("#resetFilters").addEventListener("click", resetFilters);

    const qs = params();
    $("#csvBtn").addEventListener("click", () => {
      location.href = `/api/export/csv?${qs.toString()}`;
    });
    $("#xlsxBtn").addEventListener("click", () => {
      location.href = `/api/export/xlsx?${qs.toString()}`;
    });
    $("#copyBtn").addEventListener("click", copyTsv);

    document.addEventListener("fr:lang", () => {
      renderViewSeg();
      renderStrategySeg();
      renderColToggles();
      renderStale(state.stale);
      render();
      if (state.sourceKey) {
        state.sourceLabel = t(state.sourceKey[0], state.sourceKey[1]);
        $("#sourceBadge").innerHTML = `<span class="dot"></span>${escapeHtml(state.sourceLabel)}`;
      }
      updateExportBar();
    });

    // initial paint is a skeleton so the table never flashes empty
    $("#body").innerHTML = FR.skeletonRows(10, 7);
    await reload();
  };
})();
