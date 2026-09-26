/* Data preview — pick a US ticker and inspect how each workbook parameter is
   derived from akshare data. Read-only: nothing here writes a snapshot. */
"use strict";

(() => {
  const {
    api, $, escapeHtml, toast, stateBlock, skeletonRows, num, DASH,
  } = FR;

  /* ---- English labels: the backend catalogue is authored in Chinese ---- */
  const EN_LABEL = {
  "price": "Price",
  "high_52w": "52W high",
  "low_52w": "52W low",
  "market_cap": "Market cap",
  "shares": "Shares (derived)",
  "revenue_fy0": "Revenue FY0",
  "revenue_growth_yoy": "Revenue growth YoY",
  "revenue_cagr_5y": "Revenue CAGR 5Y",
  "eps_growth_yoy": "EPS growth YoY",
  "eps_cagr_5y": "EPS CAGR 5Y",
  "gross_profit_fy0": "Gross profit FY0",
  "gross_margin": "Gross margin",
  "operating_income_fy0": "Operating income FY0",
  "operating_margin": "Operating margin",
  "net_income_fy0": "Net income FY0",
  "net_margin": "Net margin",
  "equity_fy0": "Shareholders' equity FY0",
  "roe": "ROE",
  "roic": "ROIC",
  "rd_expense": "R&D expense",
  "ocf_fy0": "Operating cash flow FY0",
  "capex_fy0": "Capital expenditure FY0",
  "fcf_fy0": "Free cash flow FY0",
  "fcf_margin": "FCF margin",
  "fcf_yield": "FCF yield",
  "ocf_to_net_income": "Cash conversion",
  "cash_fy0": "Cash FY0",
  "debt_fy0": "Debt FY0",
  "total_assets_fy0": "Total assets FY0",
  "debt_to_assets": "Debt / assets",
  "da_fy0": "D&A FY0",
  "sbc_fy0": "Share-based comp FY0",
  "effective_tax_rate": "Effective tax rate",
  "gaap_eps": "GAAP diluted EPS",
  "diluted_shares": "Diluted shares",
  "sbc_adj_share": "SBC adj/share",
  "amortization_adj_share": "Amortization adj/share",
  "restructuring_adj_share": "Restructuring adj/share",
  "model_adjusted_eps": "Model adjusted EPS",
  "reported_non_gaap_eps": "Reported non-GAAP EPS",
  "selected_adjusted_eps": "Selected adjusted EPS",
  "price_to_sales": "P/S",
  "price_to_book": "P/B",
  "price_to_fcf": "P/FCF",
  "ev_to_ebitda": "EV/EBITDA",
  "ntm_pe": "NTM P/E",
  "ev_fy0": "Enterprise value",
  "analyst_target": "Analyst target",
  "forward_pe_quote": "Forward P/E (quote source)",
  "return_1y": "1Y return",
  "drawdown_52w": "52W drawdown",
  "ROA": "Return on assets",
  "ROE_AVG": "Average ROE",
  "CURRENT_RATIO": "Current ratio",
  "SPEED_RATIO": "Quick ratio",
  "DEBT_ASSET_RATIO": "Debt / assets (akshare)",
  "GROSS_PROFIT_RATIO": "Gross margin (akshare)",
  "NET_PROFIT_RATIO": "Net margin (akshare)",
  "ACCOUNTS_RECE_TR": "Receivables turnover",
  "INVENTORY_TR": "Inventory turnover",
  "TOTAL_ASSETS_TR": "Total asset turnover",
  "BASIC_EPS": "Basic EPS",
  "BASIC_EPS_YOY": "Basic EPS YoY",
  "OPERATE_INCOME_YOY": "Revenue YoY (akshare)",
  "PARENT_HOLDER_NETPROFIT_YOY": "Net profit YoY"
};
  const EN_CAT = {
  "market": "Market & cap",
  "growth": "Growth",
  "profitability": "Profitability",
  "cash": "Cash flow & leverage",
  "pershare": "Per-share bridge (non-GAAP)",
  "valuation": "Valuation",
  "market_risk": "Market & risk",
  "akshare_extra": "Extra from akshare (not in the workbook)"
};

  /* ---- English names for the raw financial-statement rows ---- */
  const EN_ITEM = {
  "主营收入": "Revenue",
  "营业总收入": "Total revenue",
  "营业收入": "Revenue",
  "营业成本": "Cost of revenue",
  "营业总成本": "Total operating cost",
  "毛利": "Gross profit",
  "研发费用": "R&D expense",
  "销售费用": "Selling expense",
  "管理费用": "G&A expense",
  "营业利润": "Operating income",
  "营业利润(亏损以-号填列)": "Operating income",
  "利息费用": "Interest expense",
  "税前利润": "Pre-tax income",
  "所得税费用": "Income tax expense",
  "净利润": "Net income",
  "净利润(亏损以-号填列)": "Net income",
  "归属于母公司股东的净利润": "Net income to parent",
  "基本每股收益": "Basic EPS",
  "稀释每股收益": "Diluted EPS",
  "经营活动产生的现金流量净额": "Operating cash flow",
  "投资活动产生的现金流量净额": "Investing cash flow",
  "筹资活动产生的现金流量净额": "Financing cash flow",
  "现金及现金等价物净增加额": "Net change in cash",
  "折旧及摊销": "Depreciation & amortisation",
  "折旧与摊销": "Depreciation & amortisation",
  "资产总计": "Total assets",
  "负债合计": "Total liabilities",
  "所有者权益合计": "Total equity",
  "股东权益合计": "Total equity",
  "流动资产合计": "Current assets",
  "流动负债合计": "Current liabilities",
  "存货": "Inventory",
  "应收账款": "Accounts receivable",
  "货币资金": "Cash & equivalents",
  "长期借款": "Long-term debt",
  "短期借款": "Short-term debt",
  "商誉": "Goodwill",
  "固定资产": "Fixed assets",
  "无形资产": "Intangible assets",
  "总股本": "Shares outstanding",
  "普通股股本": "Common stock",
  "优先股": "Preferred stock",
  "留存收益": "Retained earnings"
};

  /** Backend provenance keys are Chinese; map them for the English legend. */
  const SOURCE_EN = {
    "日线行情": "Daily prices",
    "综合损益表": "Income statement",
    "现金流量表": "Cash flow statement",
    "资产负债表": "Balance sheet",
    "分析指标": "Analysis indicators",
  };

  function sourceOf(key) {
    if (FRI18n.current() !== "en") return key;
    return SOURCE_EN[key] || key;
  }

  function itemOf(label) {
    if (FRI18n.current() !== "en") return label;
    return EN_ITEM[label] || label;
  }

  /** Localised catalogue label: English from the map, Chinese as authored. */
  function labelOf(key, fallback) {
    if (FRI18n.current() === "en" && EN_LABEL[key]) return EN_LABEL[key];
    return fallback ?? key;
  }

  function categoryOf(key, fallback) {
    if (FRI18n.current() === "en" && EN_CAT[key]) return EN_CAT[key];
    return fallback ?? key;
  }

  const state = {
    payload: null,      // current preview payload
    catalog: null,      // static parameter catalogue
    category: "all",
    stmt: "income",
    picks: [],
  };

  /* ------------------------------------------------------------ formatting */
  const money = (v) => {
    if (v === null || v === undefined) return DASH;
    const a = Math.abs(v);
    if (a >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
    if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
    return `$${v.toFixed(2)}`;
  };
  const big = (v) => {
    if (v === null || v === undefined) return DASH;
    if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
    if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
    return num(v, 0);
  };
  const ratio = (v) => (v === null || v === undefined ? DASH : `${Number(v).toFixed(2)}x`);

  // Parameter values arrive pre-formatted from the API (`display`), so the page
  // never re-derives a number and can never disagree with the backend.
  const shown = (item) => (item && item.display ? item.display : DASH);

  function tag(status) {
    if (status === "manual") return `<span class="pv-tag pv-tag--manual">${t("pv.manual")}</span>`;
    if (status === "missing") return `<span class="pv-tag pv-tag--missing">${t("pv.noSource")}</span>`;
    return `<span class="pv-tag pv-tag--ok">${t("pv.calculated")}</span>`;
  }

  /* ---------------------------------------------------------------- picker */
  function renderPicks(list) {
    const host = $("#pvPicks");
    if (!list.length) { host.innerHTML = ""; return; }
    host.innerHTML = list
      .map((t) => `<button class="chip" type="button" data-t="${escapeHtml(t.ticker)}"
                     title="${escapeHtml(t.name || "")}">${escapeHtml(t.ticker)}</button>`)
      .join("");
    host.querySelectorAll("[data-t]").forEach((b) =>
      b.addEventListener("click", () => {
        $("#pvQuery").value = b.dataset.t;
        load(b.dataset.t);
      })
    );
  }

  function renderSuggest(list) {
    const box = $("#pvSuggest");
    if (!list.length) {
      box.innerHTML = '<div class="pv-opt pv-opt--empty">没有匹配的美股代码</div>';
      box.hidden = false;
      return;
    }
    box.innerHTML = list
      .map((t) => `<div class="pv-opt" data-t="${escapeHtml(t.ticker)}">
          <b>${escapeHtml(t.ticker)}</b><span>${escapeHtml(t.name || "")}</span>
          <span class="ex">${escapeHtml(t.source || "")}</span>
        </div>`)
      .join("");
    box.querySelectorAll("[data-t]").forEach((el) =>
      el.addEventListener("mousedown", (e) => {
        e.preventDefault();
        $("#pvQuery").value = el.dataset.t;
        box.hidden = true;
        load(el.dataset.t);
      })
    );
    box.hidden = false;
  }

  const debounce = (fn, ms) => {
    let t = null;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  };

  const search = debounce(async (q) => {
    if (!q.trim()) { $("#pvSuggest").hidden = true; return; }
    try {
      const d = await api.usTickers(q, 25);
      renderSuggest(d.tickers || []);
    } catch {
      $("#pvSuggest").hidden = true;
    }
  }, 220);

  /* -------------------------------------------------------------- overview */
  function renderOverview() {
    const d = state.payload;
    $("#pvOverview").hidden = false;
    $("#pvTitle").textContent = `${d.ticker} · ${d.company || ""}`;
    $("#pvMeta").textContent =
      `${(d.fiscal_years || []).slice(0, 5).join(" / ")} · 币种 ${d.currency}` +
      ` · 生成于 ${String(d.generated_at).replace("T", " ").slice(0, 19)}`;

    const items = {};
    d.groups.forEach((g) => g.items.forEach((i) => { items[i.key] = i; }));

    const cells = [
      [t("pv.stat.price"), shown(items.price), d.currency],
      [t("pv.stat.high"), shown(items.high_52w), t("pv.stat.closeBasis")],
      [t("pv.stat.low"), shown(items.low_52w), t("pv.stat.closeBasis")],
      [t("pv.stat.mcap"), shown(items.market_cap), t("pv.stat.sharesFromEps")],
      [t("m.revenue_fy0") || "Revenue FY0", shown(items.revenue_fy0), t("pv.stat.incomeBasis")],
      [t("m.net_margin"), shown(items.net_margin), t("pv.stat.netOverRevenue")],
      [t("m.roe"), shown(items.roe), t("pv.stat.netOverEquity")],
      [t("in.fcf_fy0"), shown(items.fcf_fy0), t("pv.stat.ocfCapex")],
      [t("in.gaap_eps"), shown(items.gaap_eps), t("pv.stat.incomeBasis")],
      [t("m.price_to_sales"), shown(items.price_to_sales), t("pv.stat.mcapOverRevenue")],
      [t("m.ev_to_ebitda"), shown(items.ev_to_ebitda), t("pv.stat.withDebtCash")],
      [t("m.return_1y"), shown(items.return_1y), t("pv.stat.trailing365")],
    ];
    $("#pvBoard").innerHTML = cells
      .map(([k, v, note]) => `<div class="cell"><div class="k">${k}</div>
        <div class="v">${v}</div><div class="d">${escapeHtml(note || "")}</div></div>`)
      .join("");

    drawSpark(d.prices || []);

    $("#pvSources").innerHTML = Object.entries(d.sources || {})
      .map(([k, v]) => `<div class="srccard"><b>${escapeHtml(sourceOf(k))}</b><span>${escapeHtml(v)}</span></div>`)
      .join("") || `<p class="hint">${t("pv.noSource")}</p>`;

    const warn = $("#pvWarn");
    if ((d.warnings || []).length) {
      warn.innerHTML = `<b>${t("pv.warn")}</b><ul style="margin:6px 0 0;padding-left:18px">${
        d.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join("")}</ul>`;
      warn.hidden = false;
    } else {
      warn.hidden = true;
    }
  }

  function drawSpark(bars) {
    const svg = $("#pvSpark");
    const yAxis = $("#pvSparkY");
    const xAxis = $("#pvSparkX");
    const usable = bars.filter((b) => b && b.close !== null && b.close !== undefined);
    $("#pvSparkNote").textContent = bars.length
      ? `${bars[0].d} → ${bars[bars.length - 1].d} · ${bars.length} ${t("pv.tradingDays")}`
      : t("pv.noPrice");
    if (usable.length < 2) {
      svg.innerHTML = "";
      if (yAxis) yAxis.innerHTML = "";
      if (xAxis) xAxis.innerHTML = "";
      svg.removeAttribute("aria-label");
      return;
    }

    // Fixed viewBox: the plot stretches horizontally to fill the row, so only
    // the geometry is scaled — the axis values are HTML in the gutters beside
    // and below, where they stay legible.
    const W = 1000, H = 150, padTop = 10, padBottom = 10;
    const closes = usable.map((b) => b.close);
    const min = Math.min(...closes), max = Math.max(...closes);
    const span = max - min || Math.max(1, Math.abs(max) * 0.01);
    // A flat series still gets a sane band instead of a hairline at the top.
    const lo = min - span * 0.02, hi = max + span * 0.02;
    const plotH = H - padTop - padBottom;
    const y = (c) => padTop + (1 - (c - lo) / (hi - lo)) * plotH;

    // Position by DATE, not by index: a holiday, a suspension or a gap in the
    // series would otherwise be drawn as ordinary elapsed time.
    const t0 = Date.parse(usable[0].d);
    const t1 = Date.parse(usable[usable.length - 1].d);
    const spanMs = t1 - t0 || 1;
    const x = (d) => ((Date.parse(d) - t0) / spanMs) * W;

    const pts = usable.map((b) => `${x(b.d).toFixed(2)},${y(b.close).toFixed(2)}`);
    const up = closes[closes.length - 1] >= closes[0];
    const stroke = up ? "var(--good)" : "var(--low)";

    // Gridlines at the labelled values, so a reader can read a level off the
    // curve without hovering.
    const ticks = [hi, (hi + lo) / 2, lo];
    const grid = ticks.map((v) => `<line x1="0" y1="${y(v).toFixed(2)}"
      x2="${W}" y2="${y(v).toFixed(2)}" class="grid" />`).join("");

    svg.innerHTML = `
      ${grid}
      <polyline class="area" points="${pts.join(" ")} ${W},${H} 0,${H}"
        fill="${stroke}" opacity="0.10" />
      <polyline class="line" points="${pts.join(" ")}" stroke="${stroke}" />`;
    svg.setAttribute(
      "aria-label",
      `${t("pv.spark")}: ${t("pv.stat.low")} ${min.toFixed(2)} → ${t("pv.stat.high")} ${max.toFixed(2)}`,
    );

    // Y-axis: the same values the gridlines sit on.
    if (yAxis) {
      yAxis.innerHTML = ticks.map((v, i) => `<span style="top:${
        (y(v) / H * 100).toFixed(2)}%" class="${i === 0 ? "hi" : i === ticks.length - 1 ? "lo" : "mid"}">${
        money(v)}</span>`).join("");
    }
    // X-axis: start, middle and end dates.
    if (xAxis) {
      const mid = usable[Math.floor(usable.length / 2)].d;
      const short = (d) => String(d).slice(2, 7).replace("-", "/");
      xAxis.innerHTML = `<span>${escapeHtml(short(usable[0].d))}</span>
        <span class="mid">${escapeHtml(short(mid))}</span>
        <span>${escapeHtml(short(usable[usable.length - 1].d))}</span>`;
    }
  }

  /* ------------------------------------------------------------ parameters */
  function renderParams() {
    const d = state.payload;
    $("#pvParams").hidden = false;
    const titles = d.category_titles || {};

    const tabs = [{ key: "all", title: t("pv.all") },
      ...Object.entries(titles).map(([key, title]) => ({ key, title: categoryOf(key, title) }))];
    $("#pvTabs").innerHTML = tabs
      .map((tab) => `<button type="button" data-cat="${tab.key}"
        aria-pressed="${tab.key === state.category}">${escapeHtml(tab.title)}</button>`)
      .join("");
    $("#pvTabs").querySelectorAll("[data-cat]").forEach((b) =>
      b.addEventListener("click", () => { state.category = b.dataset.cat; renderParams(); })
    );

    const groups = d.groups.filter((g) => state.category === "all" || g.category === state.category);
    $("#pvGroups").innerHTML = groups.map((g) => {
      const ok = g.items.filter((i) => i.status === "ok").length;
      const rows = g.items.map((i) => {
        const cls = i.status === "manual" ? "is-manual" : i.status === "missing" ? "is-missing" : "";
        // "(新增)" marks a column we derive that the workbook does not have
        const added = i.excel_ref === "(新增)";
        const ref = i.excel_ref && !["(N/A)", "(新增)", ""].includes(i.excel_ref)
          ? `<code>${escapeHtml(i.excel_ref)}</code>`
          : `<span class="none">${escapeHtml(added ? t("pv.added") : (i.excel_ref || "—"))}</span>`;
        return `<tr class="${cls}">
          <td class="name">${escapeHtml(labelOf(i.key ?? i.excel_ref, i.label))}</td>
          <td class="ref">${ref}</td>
          <td class="n">${escapeHtml(shown(i))}</td>
          <td class="formula">${escapeHtml(i.formula || DASH)}</td>
          <td>${tag(i.status)}</td>
          <td class="note">${escapeHtml(i.note || "")}</td>
        </tr>`;
      }).join("");
      return `<div class="pv-group">
        <div class="section-head">
          <div><h2 style="font-size:13.5px">${escapeHtml(categoryOf(g.category, g.title))}</h2></div>
          <div class="tools"><span class="pv-count">${t("pv.calculatedCount", { ok, total: g.items.length })}</span></div>
        </div>
        <div class="glass tablewrap">
          <table class="pv-table">
            <thead><tr>
              <th>${t("common.metric")}</th><th>${t("pv.form.excel")}</th>
              <th style="text-align:right">${t("pv.form.value")}</th>
              <th>${t("pv.form.derived")}</th><th>${t("pv.form.status")}</th>
              <th>${t("pv.form.note")}</th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>`;
    }).join("");
  }

  /* --------------------------------------------------------- extra ratios */
  function renderExtra() {
    const rows = state.payload.ratios || [];
    $("#pvExtra").hidden = rows.length === 0;
    if (!rows.length) return;

    const years = (state.payload.fiscal_years || []).slice(0, 3);
    // The catalogue is fetched with the active language, so its `why` copy is
    // already localised — no second translation table needed here.
    const why = {};
    ((state.catalog && state.catalog.extra_ratios) || []).forEach((e) => { why[e.key || e.column] = e.why; });

    $("#pvExtraTable").querySelector("thead").innerHTML =
      `<tr><th>${t("common.metric")}</th><th>${t("pv.unit")}</th>${years.map((y) => `<th>${y}</th>`).join("")}
        <th style="text-align:left">${t("sc.api.desc")}</th></tr>`;
    $("#pvExtraTable").querySelector("tbody").innerHTML = rows.map((r) => {
      const cells = years.map((y) => {
        const v = r.values[y];
        const text = v === null || v === undefined ? DASH
          : r.unit === "%" ? `${v.toFixed(2)}%` : v.toFixed(2);
        return `<td>${text}</td>`;
      }).join("");
      return `<tr><td>${escapeHtml(labelOf(r.key, r.label))}</td><td>${escapeHtml(r.unit || "")}</td>
        ${cells}<td>${escapeHtml(why[r.key] || "")}</td></tr>`;
    }).join("");
  }

  /* ------------------------------------------------------ raw statements */
  const STMTS = [["income", "pv.stmtTab.income"], ["cashflow", "pv.stmtTab.cashflow"], ["balance", "pv.stmtTab.balance"]];

  function renderRaw() {
    $("#pvRaw").hidden = false;
    $("#pvStmtTabs").innerHTML = STMTS
      .map(([k, labelKey]) => `<button type="button" data-stmt="${k}"
        aria-pressed="${k === state.stmt}">${t(labelKey)}</button>`)
      .join("");
    $("#pvStmtTabs").querySelectorAll("[data-stmt]").forEach((b) =>
      b.addEventListener("click", () => { state.stmt = b.dataset.stmt; renderRaw(); })
    );

    const lines = (state.payload.raw || {})[state.stmt] || [];
    const years = (state.payload.fiscal_years || []).slice(0, 4);
    $("#pvRawTable").querySelector("thead").innerHTML =
      `<tr><th>${t("common.metric")}</th>${years.map((y) => `<th>${y}</th>`).join("")}</tr>`;
    $("#pvRawTable").querySelector("tbody").innerHTML = lines.length
      ? lines.map((l) => `<tr><td>${escapeHtml(itemOf(l.item))}</td>${
          years.map((y) => {
            const v = l.values[y];
            return `<td>${v === null || v === undefined ? DASH : num(v, 0)}</td>`;
          }).join("")}</tr>`).join("")
      : `<tr><td colspan="${years.length + 1}">${t("common.noData")}</td></tr>`;
  }

  /* ------------------------------------------------------------ availability */
  // (endpoint, tone, usage key, note key) — copy lives in the dictionary
  const AVAIL = [
    ["stock_us_daily", "good", "pv.avail.daily.usage", "pv.avail.daily.note"],
    ["stock_financial_us_report_em", "good", "pv.avail.reports.usage", "pv.avail.reports.note"],
    ["stock_financial_us_analysis_indicator_em", "good", "pv.avail.ratios.usage", "pv.avail.ratios.note"],
    ["stock_us_spot_em", "low", "pv.avail.spot.usage", "pv.avail.spot.note"],
    ["stock_us_hist / _hist_min_em", "low", "pv.avail.hist.usage", "pv.avail.hist.note"],
    ["stock_individual_basic_info_us_xq", "low", "pv.avail.xq.usage", "pv.avail.xq.note"],
    ["stock_us_valuation_baidu", "low", "pv.avail.baidu.usage", "pv.avail.baidu.note"],
  ];
  const AVAIL_LABEL = { good: "pv.avail.good", low: "pv.avail.low" };
  const AVAIL_TONE = { good: "good", low: "low" };

  function renderAvail() {
    $("#pvAvail").innerHTML = AVAIL.map(([fn, tone, usageKey, noteKey]) => `
      <tr>
        <td><code>${escapeHtml(fn)}</code></td>
        <td><span class="badge badge--${AVAIL_TONE[tone]}">${t(AVAIL_LABEL[tone])}</span></td>
        <td>${t(usageKey)}</td>
        <td class="muted">${t(noteKey)}</td>
      </tr>`).join("");
  }

  /* ------------------------------------------------------------------ load */
  async function load(ticker) {
    // The catalogue carries labels/formulas/notes, so it must follow the
    // language as well — refetch it with every load instead of caching it.
    state.catalog = await api.usParams(FRI18n.current()).catch(() => state.catalog);
    const symbol = String(ticker || "").trim().toUpperCase();
    if (!symbol) { toast(t("pv.needTicker"), "warn"); return; }

    $("#pvOverview").hidden = false;
    $("#pvTitle").textContent = `${symbol} · 加载中…`;
    $("#pvMeta").textContent = "";
    $("#pvBoard").innerHTML = `<div class="cell"><div class="k">${t("pv.loading")}</div><div class="v">…</div></div>`;
    $("#pvParams").hidden = true;
    $("#pvExtra").hidden = true;
    $("#pvRaw").hidden = true;
    $("#pvWarn").hidden = true;
    $("#pvSuggest").hidden = true;

    try {
      // The catalogue carries labels/formulas/notes, so it follows the language
      // too: refetch on every load rather than caching it once at startup.
      state.catalog = await api.usParams(FRI18n.current()).catch(() => state.catalog);
      state.payload = await api.usPreview(symbol, 260, FRI18n.current());
      renderOverview();
      renderParams();
      renderExtra();
      renderRaw();
      history.replaceState(null, "", `preview.html?ticker=${encodeURIComponent(symbol)}`);
    } catch (err) {
      $("#pvOverview").hidden = true;
      $("#pvParams").hidden = false;
      $("#pvGroups").innerHTML = stateBlock({
        icon: "alert",
        title: `${symbol} 参数构建失败`,
        body: escapeHtml(err.message || t("pv.unknownError")),
      });
      toast(`${symbol}: ${err.message}`, "warn", 5000);
    }
  }

  /* ---------------------------------------------------------------- page init */
  window.pageInit = async () => {
    renderAvail();

    try {
      state.catalog = await api.usParams(FRI18n.current());
    } catch { /* catalogue is optional */ }

    // Quick picks + universe metadata
    try {
      const d = await api.usTickers("", 16);
      state.picks = d.tickers || [];
      renderPicks(state.picks);
      $("#uniMeta").textContent = t("pv.universe.ready");
    } catch {
      $("#uniMeta").textContent = t("pv.universe.failed");
    }

    // Search wiring
    const input = $("#pvQuery");
    input.addEventListener("input", () => search(input.value));
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); load(input.value.trim().split(/\s+/)[0]); }
      if (e.key === "Escape") $("#pvSuggest").hidden = true;
    });
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".pv-search")) $("#pvSuggest").hidden = true;
    });
    $("#pvGo").addEventListener("click", () => load(input.value.trim().split(/\s+/)[0]));
    $("#pvLoad").addEventListener("click", () => load(input.value.trim().split(/\s+/)[0]));
    $("#pvReload").addEventListener("click", () => {
      if (state.payload) load(state.payload.ticker);
    });
    $("#pvReload").textContent = t("pv.reload");
    $("#pvLoad").textContent = t("pv.load");

    // A language flip refetches: part of the copy (formulas, notes, sources)
    // is authored by the backend, so a client-side repaint would leave it in
    // the previous language.
    document.addEventListener("fr:lang", () => {
      FRI18n.applyI18n();
      $("#pvReload").textContent = t("pv.reload");
      $("#pvLoad").textContent = t("pv.load");
      $("#uniMeta").textContent = t("pv.universe.ready");
      renderAvail();
      if (state.payload) load(state.payload.ticker);
    });

    const preset = new URLSearchParams(location.search).get("ticker") || "MSFT";
    input.value = preset.toUpperCase();
    await load(preset);
  };
})();
