/* Ranking — client-side filter, sort, column control and export. */
"use strict";

(() => {
  const {
    api, icon, solid, $, $$, el, escapeHtml, toast, stateBlock, debounce,
    num, pct, signedPct, mult, money, when, DASH,
  } = FR;

  const PREF_KEY = "fr.ranking.prefs";

  const VIEWS = [
    { key: "overall", i18n: "rk.view.overall", col: "score_overall" },
    { key: "growth", i18n: "rk.view.growth", col: "score_growth" },
    { key: "profitability", i18n: "rk.view.profitability", col: "score_profitability" },
    { key: "cash", i18n: "rk.view.cash", col: "score_cash" },
    { key: "valuation", i18n: "rk.view.valuation", col: "score_valuation" },
    { key: "market", i18n: "rk.view.market", col: "score_market" },
  ];

  const numCell = (v, d = 2, cls = "") =>
    `<td class="right${cls ? " " + cls : ""}"><span class="mono">${num(v, d)}</span></td>`;
  const pctCell = (v, d = 1) =>
    `<td class="right"><span class="mono${v > 0 ? "" : ""}" style="color:${v === null || v === undefined ? "var(--faint)" : v >= 0 ? "var(--good)" : "var(--low)"}">${signedPct(v, d)}</span></td>`;

  // keys whose data is numeric → header + cells align right
  const RIGHT = new Set([
    "rank", "score_growth", "score_profitability", "score_cash", "score_valuation", "score_market",
    "score_overall", "price", "market_cap", "return_1y", "revenue_growth_yoy", "revenue_cagr_5y",
    "gross_margin", "operating_margin", "net_margin", "roe", "roic", "fcf_yield", "forward_pe",
    "price_to_sales", "ev_to_ebitda", "price_to_book", "price_to_fcf", "beta", "drawdown_52w",
    "data_coverage",
  ]);

  const COLS = [
    {
      key: "rank", label: () => "#", sortable: true, width: "48px",
      value: (r) => (r.rank === null || r.rank === undefined ? Number.POSITIVE_INFINITY : r.rank),
      render: (r) => `<td class="rank-cell num${r.rank && r.rank <= 3 ? " top" : ""}">${r.rank ?? DASH}</td>`,
    },
    {
      key: "ticker", label: () => t("common.ticker"), sortable: true,
      width: "82px", value: (r) => r.ticker,
      render: (r) => `<td class="ticker"><a class="tick-link" href="${detailHref(r.ticker)}">${escapeHtml(r.ticker)}</a></td>`,
    },
    {
      key: "company", label: () => t("common.company"), sortable: true,
      width: "190px", value: (r) => (r.company || "").toLowerCase(),
      render: (r) => `<td class="company" title="${escapeHtml(r.company || "")}">${escapeHtml(r.company || DASH)}</td>`,
    },
    {
      key: "detail", label: () => t("action.expand"), sortable: false, width: "82px",
      value: () => "",
      render: (r) => `<td><a class="detail-link" href="${detailHref(r.ticker)}"
        title="${escapeHtml(t("action.detail"))} · ${escapeHtml(r.ticker)}">${
        t("action.expand")}<span class="chev">${icon("chevron")}</span></a></td>`,
    },
    { key: "score_growth", label: () => t("dim.growth"), sortable: true, width: "92px", value: (r) => r.score_growth, render: (r) => `<td class="right dimcell">${FR.meter(r.score_growth)}</td>` },
    { key: "score_profitability", label: () => t("dim.profitability"), sortable: true, width: "92px", value: (r) => r.score_profitability, render: (r) => `<td class="right dimcell">${FR.meter(r.score_profitability)}</td>` },
    { key: "score_cash", label: () => t("dim.cash"), sortable: true, width: "92px", value: (r) => r.score_cash, render: (r) => `<td class="right dimcell">${FR.meter(r.score_cash)}</td>` },
    { key: "score_valuation", label: () => t("dim.valuation"), sortable: true, width: "92px", value: (r) => r.score_valuation, render: (r) => `<td class="right dimcell">${FR.meter(r.score_valuation)}</td>` },
    { key: "score_market", label: () => t("dim.market"), sortable: true, width: "92px", value: (r) => r.score_market, render: (r) => `<td class="right dimcell">${FR.meter(r.score_market)}</td>` },
    { key: "score_overall", label: () => t("common.overall"), sortable: true, width: "104px", value: (r) => r.score_overall, render: (r) => `<td class="right dimcell">${FR.meter(r.score_overall)}</td>` },
    { key: "profile", label: () => t("common.profile"), sortable: false, width: "84px", value: (r) => r.profile, render: (r) => `<td>${FR.profilePill(r.profile)}</td>` },
    { key: "price", label: () => t("col.price"), sortable: true, width: "84px", value: (r) => r.price, render: (r) => numCell(r.price) },
    { key: "market_cap", label: () => t("col.marketCap"), sortable: true, width: "92px", value: (r) => r.market_cap, render: (r) => `<td class="right"><span class="mono">${money(r.market_cap)}</span></td>` },
    { key: "return_1y", label: () => t("m.return_1y"), sortable: true, width: "92px", value: (r) => r.return_1y, render: (r) => pctCell(r.return_1y) },
    { key: "revenue_growth_yoy", label: () => t("m.revenue_growth_yoy"), sortable: true, width: "104px", value: (r) => r.revenue_growth_yoy, render: (r) => pctCell(r.revenue_growth_yoy) },
    { key: "revenue_cagr_5y", label: () => t("m.revenue_cagr_5y"), sortable: true, width: "104px", value: (r) => r.revenue_cagr_5y, render: (r) => pctCell(r.revenue_cagr_5y) },
    { key: "gross_margin", label: () => t("m.gross_margin"), sortable: true, width: "92px", value: (r) => r.gross_margin, render: (r) => numCell(r.gross_margin === null || r.gross_margin === undefined ? null : r.gross_margin * 100, 1) },
    { key: "operating_margin", label: () => t("m.operating_margin"), sortable: true, width: "116px", value: (r) => r.operating_margin, render: (r) => numCell(r.operating_margin === null || r.operating_margin === undefined ? null : r.operating_margin * 100, 1) },
    { key: "net_margin", label: () => t("m.net_margin"), sortable: true, width: "92px", value: (r) => r.net_margin, render: (r) => numCell(r.net_margin === null || r.net_margin === undefined ? null : r.net_margin * 100, 1) },
    { key: "roe", label: () => t("m.roe"), sortable: true, width: "84px", value: (r) => r.roe, render: (r) => numCell(r.roe === null || r.roe === undefined ? null : r.roe * 100, 1) },
    { key: "roic", label: () => t("m.roic"), sortable: true, width: "84px", value: (r) => r.roic, render: (r) => numCell(r.roic === null || r.roic === undefined ? null : r.roic * 100, 1) },
    { key: "fcf_yield", label: () => t("m.fcf_yield"), sortable: true, width: "104px", value: (r) => r.fcf_yield, render: (r) => pctCell(r.fcf_yield) },
    { key: "forward_pe", label: () => t("m.forward_pe"), sortable: true, width: "96px", value: (r) => r.forward_pe, render: (r) => `<td class="right"><span class="mono">${mult(r.forward_pe)}</span></td>` },
    { key: "price_to_sales", label: () => t("m.price_to_sales"), sortable: true, width: "80px", value: (r) => r.price_to_sales, render: (r) => `<td class="right"><span class="mono">${mult(r.price_to_sales)}</span></td>` },
    { key: "ev_to_ebitda", label: () => t("m.ev_to_ebitda"), sortable: true, width: "108px", value: (r) => r.ev_to_ebitda, render: (r) => `<td class="right"><span class="mono">${mult(r.ev_to_ebitda)}</span></td>` },
    { key: "price_to_book", label: () => t("m.price_to_book"), sortable: true, width: "76px", value: (r) => r.price_to_book, render: (r) => `<td class="right"><span class="mono">${mult(r.price_to_book)}</span></td>` },
    { key: "price_to_fcf", label: () => t("m.price_to_fcf"), sortable: true, width: "88px", value: (r) => r.price_to_fcf, render: (r) => `<td class="right"><span class="mono">${mult(r.price_to_fcf)}</span></td>` },
    { key: "beta", label: () => t("m.beta"), sortable: true, width: "76px", value: (r) => r.beta, render: (r) => numCell(r.beta, 2) },
    { key: "drawdown_52w", label: () => t("m.drawdown_52w"), sortable: true, width: "104px", value: (r) => r.drawdown_52w, render: (r) => pctCell(r.drawdown_52w) },
    { key: "data_coverage", label: () => t("common.coverage"), sortable: true, width: "76px", value: (r) => r.data_coverage, render: (r) => `<td class="right"><span class="mono">${r.data_coverage}/21</span></td>` },
    { key: "status", label: () => t("rf.jobs.status"), sortable: true, width: "92px", value: (r) => r.status || "", render: (r) => `<td><span class="faint" style="font-size:11.5px">${escapeHtml(r.status || DASH)}</span></td>` },
  ];

  const COL_BY_KEY = Object.fromEntries(COLS.map((c) => [c.key, c]));

  const state = {
    rows: [],
    view: "overall",
    sortKey: "rank",
    sortDir: 1,
    q: "",
    profile: "",
    eligibleOnly: false,
    hidden: new Set([
      "status", "price", "market_cap", "return_1y", "revenue_growth_yoy", "revenue_cagr_5y",
      "gross_margin", "operating_margin", "net_margin", "roe", "roic", "fcf_yield",
      "forward_pe", "price_to_sales", "ev_to_ebitda", "price_to_book", "price_to_fcf",
      "beta", "drawdown_52w", "company",
    ]),
    sourceLabel: "最近快照",
  };

  /* -------------------------------------------------------------- preferences */
  function loadPrefs() {
    try {
      const raw = JSON.parse(localStorage.getItem(PREF_KEY) || "{}");
      if (raw.view && VIEWS.some((v) => v.key === raw.view)) state.view = raw.view;
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
        view: state.view, hidden: [...state.hidden],
      }));
    } catch { /* storage may be unavailable */ }
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

  function renderHead() {
    const cols = visibleCols();
    $("#cols").innerHTML = cols.map((c) => `<col style="width:${c.width}" />`).join("");
    $("#headRow").innerHTML = cols.map((c) => {
      const active = state.sortKey === c.key;
      const aria = active ? ` aria-sort="${state.sortDir === 1 ? "ascending" : "descending"}"` : "";
      const arrow = active ? (state.sortDir === 1 ? "▲" : "▼") : "▲";
      const cls = [c.sortable ? "sortable" : "", RIGHT.has(c.key) ? "right" : ""].filter(Boolean).join(" ");
      const label = typeof c.label === "function" ? c.label() : c.label;
      return `<th class="${cls}"${c.width ? ` style="width:${c.width}"` : ""}${aria}${
        c.sortable ? ` data-sort="${c.key}"` : ""}>${label}<span class="arrow">${arrow}</span></th>`;
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
    const cols = visibleCols();
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
    const html = rows.map((r, i) => `<tr class="rowlink" data-row="${escapeHtml(r.ticker)}"
        style="animation:tag-in .4s var(--ease) backwards;animation-delay:${Math.min(i * 14, 320)}ms">
      ${cols.map((c) => c.render(r)).join("")}
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
      });
      state.rows = data.rows || [];
      state.errors = Object.entries(data.errors || {});
      state.sourceKey = qs.get("run_id")
        ? ["rk.source.run", { id: qs.get("run_id") }]
        : qs.get("job_id") ? ["rk.source.job", null] : ["common.latestSnapshot", null];
      state.sourceLabel = t(state.sourceKey[0], state.sourceKey[1]);
      $("#sourceBadge").innerHTML = `<span class="dot"></span>${escapeHtml(state.sourceLabel)}`;
      render();
      if (state.errors.length) {
        toast(t("rk.toast.failed", { n: state.errors.length }), "warn", 4200);
      }
    } catch (err) {
      state.rows = [];
      $("#sourceBadge").textContent = err.status === 404 ? t("common.noData") : t("common.readFailed");
      render();
      if (err.status !== 404) toast(err.message || t("rk.err.load"), "err");
    }
  }

  window.pageInit = async function pageInit() {
    loadPrefs();

    $("#searchIcon").innerHTML = icon("search");
    $("#toRefresh").innerHTML = `${solid("play")}<span data-i18n="action.go.refresh">${t("action.go.refresh")}</span>`;
    $("#resetFilters").innerHTML = `${icon("x")}<span data-i18n="action.reset">${t("action.reset")}</span>`;
    $("#csvBtn").innerHTML = `${icon("download")}<span data-i18n="action.export.csv">${t("action.export.csv")}</span>`;
    $("#xlsxBtn").innerHTML = `${icon("download")}<span data-i18n="action.export.xlsx">${t("action.export.xlsx")}</span>`;
    $("#copyBtn").innerHTML = `${icon("copy")}<span data-i18n="action.copy.table">${t("action.copy.table")}</span>`;

    renderViewSeg();
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
      renderColToggles();
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
