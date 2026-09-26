/* ==========================================================================
   Company detail — the full, dimension-grouped breakdown of one ticker.
   Shared by company.html (full page). Mirrors the workbook's `Stock Detail`
   sheet: five weighted dimensions, every metric against its peer median with
   a within-group rank and a 1–10 score, then the raw filing inputs.
   ========================================================================== */
"use strict";

const FRCompany = (() => {
  const {
    api, icon, $, $$, escapeHtml, toast, stateBlock,
    num, money, when, tone, meter, profilePill, DASH,
  } = FR;

  /* ------------------------------------------------------------- dimensions */
  // Weights and display order only. The metric *lists* come from the backend
  // (`/api/metrics`) so the scores the backend computes and the rows the UI
  // renders can never disagree — a hand-maintained list here previously showed
  // metrics with an empty score column and no explanation.
  const DIMENSIONS = [
    { key: "growth", i18n: "dim.growth", score: "score_growth", weight: 0.20, icon: "trend" },
    { key: "profitability", i18n: "dim.profitability", score: "score_profitability", weight: 0.15, icon: "coins" },
    { key: "cash", i18n: "dim.cash", score: "score_cash", weight: 0.20, icon: "layers" },
    { key: "valuation", i18n: "dim.valuation", score: "score_valuation", weight: 0.25, icon: "shield" },
    {
      key: "market", i18n: "dim.market", score: "score_market", weight: 0.20, icon: "zap",
      // The market block answers two questions; showing them separately is what
      // makes "ranked well because it went up" vs "because it was steady"
      // visible instead of hidden inside one blended number.
      subs: [
        { field: "score_market_performance", i18n: "dim.market.performance", weight: 0.60 },
        { field: "score_market_risk", i18n: "dim.market.risk", weight: 0.40 },
      ],
    },
  ];

  /** Populated from the backend catalog on first mount. */
  let CATALOG = null;
  let CATALOG_BY_COMPONENT = null;

  function buildCatalog(payload) {
    CATALOG = payload.metrics || [];
    CATALOG_BY_COMPONENT = {};
    for (const m of CATALOG) {
      (CATALOG_BY_COMPONENT[m.component] ||= []).push(m);
    }
    // Scored metrics first inside each block, then reference-only ones, so the
    // numbers that drive the dimension score lead.
    for (const list of Object.values(CATALOG_BY_COMPONENT)) {
      list.sort((a, b) => (a.scored === b.scored ? a.order - b.order : a.scored ? -1 : 1));
    }
  }

  function metricsFor(dim) {
    if (!CATALOG_BY_COMPONENT) return [];
    return CATALOG_BY_COMPONENT[dim.key] || [];
  }

  const INPUT_GROUPS = [
    {
      i18n: "co.group.scale", icon: "database",
      rows: [
        ["revenue_fy0", "money"], ["revenue_fy_minus_1", "money"],
        ["market_cap", "money"], ["total_assets_fy0", "money"],
        ["equity_fy0", "money"], ["cash_fy0", "money"],
        ["debt_fy0", "money"], ["share_count", "money"],
      ],
    },
    {
      i18n: "co.group.earnings", icon: "coins",
      rows: [
        ["operating_income_fy0", "money"], ["gross_profit_fy0", "money"],
        ["cost_of_revenue_fy0", "money"], ["da_fy0", "money"],
        ["ocf_fy0", "money"], ["capex_fy0", "money"],
        ["fcf_fy0", "money"], ["sbc_fy0", "money"],
      ],
    },
    {
      i18n: "co.group.pershare", icon: "layers",
      rows: [
        ["gaap_eps", "num"], ["diluted_shares_fy0", "money"],
        ["sbc_adj_share", "num"], ["restructuring_adj_share", "num"],
        ["amortization_adj_share", "num"], ["tax_adj_share", "num"],
        ["model_adjusted_eps", "num"], ["effective_tax_rate", "ratio"],
      ],
    },
  ];

  /** The active language's metric name (shared with every other page). */
  function labelFor(attr) {
    return t(`m.${attr}`);
  }

  /** Secondary line under the metric name: the other language's term. */
  function subLabelFor(attr) {
    const entry = FRI18n.DICT[`m.${attr}`];
    if (!entry || entry[0] === entry[1]) return "";
    return FRI18n.current() === "zh" ? entry[1] : entry[0];
  }

  /* ----------------------------------------------------------- pool helpers */
  let pool = [];
  let lastRender = null;                 // last painted company, for language flips
  let context = new URLSearchParams();   // snapshot context: job_id / run_id
  let sourceQS = "";                     // ready-made "?..." for static links
  let rankingHref = "ranking.html";

  /** Company detail URL for a ticker, keeping the snapshot context. */
  function companyHref(ticker) {
    const qs = new URLSearchParams(context);
    if (ticker) qs.set("ticker", ticker);
    return `company.html?${qs.toString()}`;
  }

  async function ensurePool(params = {}, { force = false } = {}) {
    if (pool.length && !force) return pool;
    const data = await api.ranking(params);
    pool = data.rows || [];
    return pool;
  }

  /** Scoring population for a metric — eligible rows when they exist. */
  function population(attr) {
    const eligible = pool.filter((r) => r.rank_eligible).map((r) => r[attr]).filter((v) => v !== null && v !== undefined);
    if (eligible.length) return eligible;
    return pool.map((r) => r[attr]).filter((v) => v !== null && v !== undefined);
  }

  function median(values) {
    if (!values.length) return null;
    const sorted = values.slice().sort((a, b) => a - b);
    const mid = sorted.length >> 1;
    return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
  }

  function peerMedian(attr) {
    return median(population(attr));
  }

  function withinGroupRank(attr, value, higher) {
    if (value === null || value === undefined) return null;
    const values = population(attr);
    if (!values.length) return null;
    const better = higher ? values.filter((v) => v > value).length : values.filter((v) => v < value).length;
    return better + 1;
  }

  /** How many metrics the backend scores, for the coverage badge. */
  function scoredCount() {
    return CATALOG ? CATALOG.filter((m) => m.scored).length : 0;
  }

  /**
   * Whether this row's value is a disclosed substitute for the named metric.
   * Mirrors `app.engine.scoring.is_substituted`: the engine records the
   * substitution in a basis string that begins with the metric's own name.
   */
  /**
   * Whether this row's value is a disclosed substitute for the named metric.
   * The catalogue states which provenance string carries that flag, so this
   * never reimplements the engine's rule — it only reads it.
   */
  function substitutionNote(metric, row) {
    if (!row || !metric.basis_fields || !metric.basis_fields.length) return "";
    const token = (labelFor(metric.attr) || metric.attr).split(" ")[0].toLowerCase();
    for (const field of metric.basis_fields) {
      const description = row[field] || "";
      if (token && description.toLowerCase().includes(token)) return description;
    }
    return "";
  }

  /* ------------------------------------------------------------- formatting */
  function fmt(value, kind, digits = 1) {
    if (value === null || value === undefined || Number.isNaN(value)) return DASH;
    if (kind === "ratio") return FR.fmt(Number(value) * 100, digits) + "%";
    if (kind === "multiple") return FR.fmt(value, 1) + "×";
    if (kind === "money") return money(value);
    return FR.fmt(value, 2);
  }

  function dimensionScore(row, dim) {
    return row[dim.score];
  }

  /* -------------------------------------------------------------- fragments */
  function heroCard(row, { rankedNeighbours = [] } = {}) {
    const score = row.score_overall;
    const shade = tone(score);
    const facets = DIMENSIONS.map((d) => `
      <div class="scorerow">
        <span class="sl">${t(d.i18n)}<em>${(d.weight * 100).toFixed(0)}%</em></span>
        ${meter(dimensionScore(row, d))}
      </div>`).join("");

    return `
      <div class="glass herocard">
        <div class="herohead">
          <div class="who">
            <div class="row" style="gap:8px">
              ${profilePill(row.profile)}
              ${row.rank ? `<span class="badge badge--accent">${t("common.rank")} #${row.rank}</span>` : ""}
              <span class="badge ${row.rank_eligible ? "badge--good" : "badge--low"}">${row.rank_eligible ? t("common.eligible") : t("common.notEligible")}</span>
              <span class="badge">${t("common.coverage")} ${row.data_coverage}/${scoredCount()}</span>
              ${row.currency && row.currency !== "USD"
                ? `<span class="badge badge--mid">${t("co.currencyNonUsd", { ccy: escapeHtml(row.currency) })}</span>` : ""}
            </div>
            <h1 class="cname">${escapeHtml(row.company || row.ticker)}</h1>
            <p class="cmeta">
              <span class="mono">${escapeHtml(row.ticker)}</span>
              ${row.fiscal_end ? ` · ${t("co.fiscalEnd")} <span class="mono">${escapeHtml(row.fiscal_end)}</span>` : ""}
              ${row.fetched_at ? ` · ${t("co.fetched")} ${escapeHtml(when(row.fetched_at))}` : ""}
            </p>
            ${row.notes ? `<p class="note" style="margin-top:12px">${escapeHtml(row.notes)}</p>` : ""}
          </div>
          <div class="bigscore tone-${shade}">
            <b>${num(score, 2)}</b>
            <span>${t("common.overallOfTen")}</span>
          </div>
        </div>
        <div class="scorerows">${facets}</div>
      </div>`;
  }

  function scoreRail(row) {
    const cells = DIMENSIONS.map((d) => {
      const v = dimensionScore(row, d);
      return `<div class="railcell">
        <span class="rl">${t(d.i18n)}<em>${(d.weight * 100).toFixed(0)}%</em></span>
        <span class="rv tone-${tone(v)}">${num(v, 2)}</span>
        <span class="rbar"><i style="transform:scaleX(${v === null || v === undefined ? 0 : Math.max(0, Math.min(1, v / 10)).toFixed(3)})"></i></span>
      </div>`;
    }).join("");
    return `<div class="glass scorerail">
      ${cells}
      <div class="railcell is-total">
        <span class="rl">${t("common.overall")}</span>
        <span class="rv tone-${tone(row.score_overall)}">${num(row.score_overall, 2)}</span>
        <span class="rbar"><i style="transform:scaleX(${row.score_overall === null || row.score_overall === undefined ? 0 : Math.max(0, Math.min(1, row.score_overall / 10)).toFixed(3)})"></i></span>
      </div>
    </div>`;
  }

  function metricRow(row, metric) {
    const value = row[metric.attr];
    const median = peerMedian(metric.attr);
    const higher = metric.higher_is_better;
    const rank = withinGroupRank(metric.attr, value, higher);
    // Only a scored metric has a z-score. For a reference-only metric the score
    // column says so instead of rendering an unexplained blank, which is
    // indistinguishable from a metric that scored badly.
    const score = metric.scored ? row["z_" + (Z_SUFFIX[metric.attr] || metric.attr)] : null;
    const hasValue = value !== null && value !== undefined;
    const delta = hasValue && median !== null ? value - median : null;
    const deltaBetter = delta === null ? null : (higher ? delta >= 0 : delta <= 0);

    const present = pool.filter((r) => r[metric.attr] !== null && r[metric.attr] !== undefined).length;
    const refTitle = metric.note ? `${t("co.referenceOnly")} — ${metric.note}` : t("co.referenceOnly");
    // A value that is not a plain percentage (a sign flip, or a shortened CAGR
    // window) is flagged here, at the number itself, because a reader comparing
    // peers will otherwise read -19.6 as a growth rate.
    const basis = value === null || value === undefined ? "" : substitutionNote(metric, row);
    const cells = {
      metric: `<td class="mlabel">
        <b>${escapeHtml(labelFor(metric.attr))}${
          metric.scored ? "" : `<span class="reftag" title="${escapeHtml(refTitle)}">${t("co.refTag")}</span>`}</b>
        <em>${escapeHtml(subLabelFor(metric.attr))}</em>
        ${basis ? `<i class="basistag" title="${escapeHtml(basis)}">${t("co.substituted")}</i>` : ""}
      </td>`,
      value: `<td class="right mnum">
        <span class="mono ${delta === null ? "" : deltaBetter ? "up" : "down"}">${fmt(value, metric.kind)}</span>
        ${rank === null ? "" : `<em class="rankchip">${rank}<i>/${present}</i></em>`}
      </td>`,
      median: `<td class="right mmedian"><span class="mono">${fmt(median, metric.kind)}</span></td>`,
      delta: `<td class="right mdelta">
        ${delta === null ? `<span class="faint mono">${DASH}</span>`
          : `<span class="mono ${deltaBetter ? "up" : "down"}">${delta > 0 ? "+" : ""}${metric.kind === "ratio" ? (delta * 100).toFixed(1) + "pt" : delta.toFixed(metric.kind === "num" ? 2 : 1)}</span>`}
      </td>`,
      rank: `<td class="right mrank">${rank === null ? `<span class="faint mono">${DASH}</span>` : `<span class="mono">${rank}<em>/${present}</em></span>`}</td>`,
      bar: metric.scored
        ? `<td class="mbar"><span class="mbarline"><i class="tone-${tone(score)}" style="transform:scaleX(${score === null || score === undefined ? 0 : Math.max(0, Math.min(1, score / 10)).toFixed(3)})"></i></span></td>`
        : `<td class="mbar is-ref"><span class="faint" style="font-size:10.5px">${t("co.notScored")}</span></td>`,
      score: metric.scored
        ? `<td class="right mscore tone-${tone(score)}"><span class="mono">${num(score, 2)}</span></td>`
        : `<td class="right mscore"><span class="faint mono" title="${escapeHtml(refTitle)}">${DASH}</span></td>`,
    };
    return `<tr class="${hasValue ? "" : "is-missing"}${metric.scored ? "" : " is-reference"}" data-attr="${metric.attr}">
      ${columns().map((c) => cells[c.key]).join("")}
    </tr>`;
  }

  /**
   * Column set + widths per breakpoint. Narrow screens render fewer columns
   * instead of hiding cells (hidden cells still reserve their column).
   */
  function columns() {
    const narrow = typeof window !== "undefined" && window.matchMedia("(max-width: 860px)").matches;
    return narrow
      ? [
        { key: "metric", label: () => t("common.metric"), w: "42%" },
        { key: "value", label: () => t("common.stock"), w: "32%", right: true },
        { key: "score", label: () => t("common.score"), w: "26%", right: true },
      ]
      : [
        { key: "metric", label: () => t("common.metric"), w: "30%" },
        { key: "value", label: () => t("common.stock"), w: "11%", right: true },
        { key: "median", label: () => t("common.peerMedian"), w: "12%", right: true },
        { key: "delta", label: () => t("common.delta"), w: "11%", right: true },
        { key: "rank", label: () => t("common.rank"), w: "11%", right: true },
        { key: "bar", label: () => t("common.bar"), w: "17%" },
        { key: "score", label: () => t("common.score"), w: "8%", right: true },
      ];
  }

  function colgroupHTML() {
    return columns().map((c) => `<col style="width:${c.w}" />`).join("");
  }

  function dimensionBlock(row, dim) {
    const score = dimensionScore(row, dim);
    const metrics = metricsFor(dim);
    const scored = metrics.filter((m) => m.scored);
    const present = scored.filter((m) => row[m.attr] !== null && row[m.attr] !== undefined).length;
    const subs = (dim.subs || []).map((s) => {
      const v = row[s.field];
      if (v === null || v === undefined) return "";
      return `<span class="subscore tone-${tone(v)}" title="${escapeHtml(t("co.subscoreHint"))}">
        <em>${escapeHtml(t(s.i18n))}<i>${Math.round(s.weight * 100)}%</i></em>
        <b>${num(v, 2)}</b></span>`;
    }).join("");
    return `
      <section class="glass dimblock" data-dim="${dim.key}">
        <header class="dimhead">
          <span class="dimico">${icon(dim.icon)}</span>
          <div class="dimtitle">
            <h2>${t(dim.i18n)}<em>${escapeHtml(t(`dim.${dim.key}`))}</em></h2>
            <p>${escapeHtml(t(`dim.${dim.key}.blurb`))}</p>
          </div>
          <div class="dimmeta">
            <span class="badge">${t("common.weight")} ${(dim.weight * 100).toFixed(0)}%</span>
            <span class="badge">${present}/${scored.length} ${t("common.itemsWithValue")}</span>
            ${subs}
          </div>
          <div class="dimscore tone-${tone(score)}">
            <b>${num(score, 2)}</b><span>/ 10</span>
          </div>
        </header>
        <div class="tablewrap tablewrap--flat dimtable">
          <table class="data">
            <colgroup class="metric-cols">${colgroupHTML()}</colgroup>
            <thead>
              <tr>${columns().map((c) => {
                const label = typeof c.label === "function" ? c.label() : c.label;
                return `<th${c.right ? ' class="right"' : ""}>${label}</th>`;
              }).join("")}</tr>
            </thead>
            <tbody>${metrics.map((m) => metricRow(row, m)).join("")}</tbody>
          </table>
        </div>
      </section>`;
  }

  function inputBlock(row) {
    const filingUnit = row.filing_currency && !row.fx_usd_per_twd
      ? row.filing_currency : row.currency;
    const displayInput = (attr, kind) => {
      const value = row[attr];
      if (value === null || value === undefined) return DASH;
      if (attr === "share_count" || attr === "diluted_shares_fy0") return FR.compact(value) + " ADS";
      if (kind === "money" && attr !== "market_cap" && filingUnit !== "USD")
        return `${escapeHtml(filingUnit)} ${FR.compact(value)}`;
      if (["gaap_eps", "sbc_adj_share", "restructuring_adj_share", "amortization_adj_share", "tax_adj_share", "model_adjusted_eps"].includes(attr))
        return `${escapeHtml(filingUnit || "USD")} ${fmt(value, kind)}`;
      return fmt(value, kind);
    };
    return `<section class="glass dimblock inputs">
      <header class="dimhead">
        <span class="dimico">${icon("database")}</span>
        <div class="dimtitle">
          <h2>${t("co.calcInputs")}<em>${t("co.calcInputs.en")}</em></h2>
          <p>${t("co.calcInputs.hint")}</p>
        </div>
      </header>
      <div class="inputgrid">
        ${INPUT_GROUPS.map((g) => `
          <div class="inputgroup">
            <h3>${icon(g.icon)}<span>${t(g.i18n)}</span></h3>
            <dl class="dl">
              ${g.rows.map(([attr, kind]) => `
                <dt>${t(`in.${attr}`)}</dt>
                <dd class="${row[attr] === null || row[attr] === undefined ? "na" : ""}">${displayInput(attr, kind)}</dd>`).join("")}
            </dl>
          </div>`).join("")}
      </div>
    </section>`;
  }

  function provenanceBlock(row) {
    const items = [
      [t("co.field.secSource"), row.sec_source || DASH],
      [t("co.field.marketSource"), row.market_source || DASH],
      [t("co.field.filingCurrency"), row.filing_currency || row.currency || DASH],
      [t("co.field.fxRate"), row.fx_usd_per_twd ? `1 TWD = ${row.fx_usd_per_twd.toFixed(6)} USD; ${row.fx_source || ""}` : DASH],
      [t("co.field.nongaapSource"), row.non_gaap_source || DASH],
      [t("co.field.quality"), row.source_quality || DASH],
      [t("co.field.shareBasis"), row.share_basis || DASH],
      [t("co.field.fetchedAt"), row.fetched_at ? when(row.fetched_at) : DASH],
    ];
    return `<section class="glass glass--solid" style="padding:18px 20px">
      <div class="section-head" style="margin-bottom:12px">
        <div><h2 style="font-size:13.5px">${t("co.provenance")}</h2>
        <p class="hint">${t("co.provenance.hint")}</p></div>
      </div>
      <div class="kvgrid">${items.map(([k, v]) => `
        <div class="kv"><div class="k">${escapeHtml(k)}</div>
        <div class="v" style="font-size:11.5px;word-break:break-all">${escapeHtml(String(v))}</div></div>`).join("")}</div>
    </section>`;
  }

  /* --------------------------------------------------------------- skeleton */
  function skeleton() {
    return `
      <div class="glass herocard"><div class="skeleton line" style="width:34%"></div>
        <div class="skeleton line" style="width:52%;margin-top:14px"></div>
        <div class="skeleton" style="height:96px;margin-top:22px"></div></div>
      <div class="glass scorerail">${DIMENSIONS.map(() =>
        `<div class="railcell"><div class="skeleton line" style="width:70%"></div>
         <div class="skeleton line" style="width:44%;margin-top:10px"></div></div>`).join("")}</div>
      ${DIMENSIONS.map(() => `<div class="glass dimblock">
        <div class="skeleton line" style="width:26%"></div>
        <div class="skeleton" style="height:120px;margin-top:18px"></div></div>`).join("")}`;
  }

  /* ------------------------------------------------------------- options link */
  /* A link, not an embedded panel: a chain costs several requests, and most
     visits to a company page are not asking about its options. */
  function optionsLink(row) {
    const href = `options.html?ticker=${encodeURIComponent(row.ticker)}`;
    return `<section class="glass dimblock">
      <header class="dimhead">
        <span class="dimico">${icon("zap")}</span>
        <div class="dimtitle">
          <h2>${t("co.options.title")}<em>OPTIONS</em></h2>
          <p>${t("co.options.lede")}</p>
        </div>
        <div class="dimmeta">
          <a class="btn btn--quiet" href="${href}">${t("co.options.open")}</a>
        </div>
      </header>
    </section>`;
  }

  /* ------------------------------------------------- quarterly EPS, last 4 */
  /* Quarterly, not annual, because the adjustments are period-specific: Alphabet's
     equity-security gains run from $1.3bn to $99bn across six consecutive
     quarters, so a year of adjustments divided by a year of shares describes no
     period a reader can act on. Each card expands to its own bridge. */
  function quarterCard(q, index) {
    const adjusted = q.adjusted_eps;
    const gaap = q.gaap_eps;
    const gap = (adjusted !== null && adjusted !== undefined && gaap)
      ? adjusted - gaap : null;
    const tone = gap === null ? "" : gap > 0.005 ? " is-up" : gap < -0.005 ? " is-down" : "";
    const surprise = q.surprise_pct;
    const lines = (q.lines || []).map((line) => `<tr>
      <td>${line.value < 0 ? "−" : "+"} ${escapeHtml(line.label)}</td>
      <td class="right mono">${money(Math.abs(line.value))}</td>
    </tr>`).join("");
    const missing = (q.missing || []).length
      ? `<p class="hint">${escapeHtml(t("co.eps.notDisclosed", { items: q.missing.join(" · ") }))}</p>`
      : "";
    // A filer that does not tag its quarters has nothing to bridge. That is a
    // different statement from "nothing was adjusted", so it is said in words
    // rather than rendered as an empty table.
    const sourcedFromAnalyst = q.source === "analyst";
    const body = sourcedFromAnalyst
      ? `<p class="hint">${escapeHtml(t("co.eps.analystOnly"))}</p>`
      : `<div class="dimtable tablewrap tablewrap--flat">
          <table class="data">
            <tbody>
              <tr class="is-total"><td>${t("co.eps.netIncome")}</td>
                <td class="right mono">${q.net_income === null || q.net_income === undefined ? DASH : money(q.net_income)}</td></tr>
              ${lines || `<tr><td colspan="2" class="hint">${t("co.eps.noAdjust")}</td></tr>`}
              <tr class="is-total"><td>${t("co.eps.adjustedIncome")}</td>
                <td class="right mono">${q.adjusted_net_income === null || q.adjusted_net_income === undefined ? DASH : money(q.adjusted_net_income)}</td></tr>
              <tr><td>${t("co.bridge.shares")}</td>
                <td class="right mono">${q.diluted_shares ? FR.fmt(q.diluted_shares, 0) : DASH}</td></tr>
              <tr><td>${t("co.eps.taxRate")}</td>
                <td class="right mono">${q.effective_tax_rate === null || q.effective_tax_rate === undefined
                  ? DASH : `${(q.effective_tax_rate * 100).toFixed(1)}%`}</td></tr>
              <tr class="is-total is-accent"><td>${t("co.bridge.adjustedEps")}</td>
                <td class="right mono">${num(adjusted, 2)}</td></tr>
            </tbody>
          </table>
        </div>
        ${missing}`;

    return `<details class="epsq${tone}" ${index === 0 ? "open" : ""}>
      <summary>
        <span class="epsq-head">
          <b class="epsq-label">${escapeHtml(q.label)}</b>
          ${sourcedFromAnalyst ? `<span class="ptag tone-mid">${t("co.eps.reportedOnly")}</span>` : ""}
          ${q.surprise_pct === null || q.surprise_pct === undefined ? ""
            : `<span class="ptag ${q.surprise_pct >= 0 ? "tone-good" : "tone-low"}">${
                q.surprise_pct >= 0 ? t("co.eps.beat") : t("co.eps.miss")}
                ${(q.surprise_pct * 100).toFixed(1)}%</span>`}
        </span>
        <span class="epsq-figs">
          <span class="epsq-fig"><i>${t("co.eps.gaap")}</i><b class="mono">${num(gaap, 2)}</b></span>
          <span class="epsq-fig epsq-fig--adj"><i>${t("co.eps.nonGaap")}</i>
            <b class="mono">${num(adjusted, 2)}</b></span>
          ${q.consensus_eps === null || q.consensus_eps === undefined ? "" : `
            <span class="epsq-fig epsq-fig--cons"><i>${t("co.eps.consensusShort")}</i>
              <b class="mono">${num(q.consensus_eps, 2)}</b></span>`}
          ${gap === null ? "" : `<span class="epsq-gap">${gap >= 0 ? "+" : ""}${gap.toFixed(2)}</span>`}
        </span>
      </summary>
      <div class="epsq-body">
        <p class="hint">${escapeHtml(t("co.eps.period", { span: q.fiscal_label || q.end }))}</p>
        ${basisNote(q)}
        ${body}
      </div>
    </details>`;
  }

  /* The three figures above are on *different* bases, so stating them side by side
     invites the reading that GAAP was compared with the consensus. It was not.
     This says which two figures the beat/miss used, and — when the filer's own
     adjusted figure diverges from them — how the three relate. */
  function basisNote(q) {
    if (q.consensus_eps === null || q.consensus_eps === undefined
        || q.surprise_actual === null || q.surprise_actual === undefined) {
      return "";
    }
    const adjustedBasis = q.surprise_basis === "adjusted";
    const lines = [
      `<p class="hint">${escapeHtml(t(
        adjustedBasis ? "co.eps.basisAdjusted" : "co.eps.basisGaap", {
          actual: num(q.surprise_actual, 2), estimate: num(q.consensus_eps, 2),
        }))}</p>`,
    ];
    if (q.surprise_mixed_basis) {
      lines.push(`<p class="hint epsq-warn">${escapeHtml(t("co.eps.mixedBasis", {
        adjusted: num(q.adjusted_eps, 2), consensus: num(q.consensus_eps, 2),
      }))}</p>`);
    }
    return lines.join("");
  }

  function quarterBlock(payload) {
    const quarters = (payload && payload.quarters) || [];
    if (!payload || !payload.available) {
      return `<section class="glass dimblock">
        <header class="dimhead">
          <span class="dimico">${icon("clock")}</span>
          <div class="dimtitle">
            <h2>${t("co.eps.title")}<em>${t("co.eps.subtitle")}</em></h2>
            <p>${t("co.eps.lede")}</p>
          </div>
        </header>
        <p class="hint">${escapeHtml((payload && payload.note) || t("co.eps.none"))}</p>
      </section>`;
    }

    const series = quarters.slice().reverse();   // oldest first for reading order
    return `<section class="glass dimblock">
      <header class="dimhead">
        <span class="dimico">${icon("clock")}</span>
        <div class="dimtitle">
          <h2>${t("co.eps.title")}<em>${t("co.eps.subtitle")}</em></h2>
          <p>${t("co.eps.lede")}</p>
        </div>
        <div class="dimmeta">
          <span class="ptag tone-good">${t("co.bridge.quarterTag")}</span>
          <span class="badge">${escapeHtml(t("co.eps.count", { n: quarters.length }))}</span>
        </div>
      </header>
      <div class="epsgrid">${series.map((q, i) => quarterCard(q, i)).join("")}</div>
      <p class="hint" style="margin-top:12px">${escapeHtml(t("co.eps.foot"))}</p>
    </section>`;
  }

  let quarterToken = 0;

  async function mountQuarters(ticker) {
    const host = $("#quarterSection");
    if (!host) return;
    const token = ++quarterToken;
    try {
      const payload = await api.eps(ticker, 4);
      if (token !== quarterToken) return;
      host.innerHTML = quarterBlock(payload);
    } catch (err) {
      if (token !== quarterToken) return;
      host.innerHTML = "";
      console.warn("quarterly EPS fetch failed", err);
    }
  }

  /* ------------------------------------------------------- record mount */
  /* Fetched separately, like the analyst panel, so a slow SEC fetch never delays
     the scored comparison above it. A skeleton shows while it loads, and a
     failure removes the section rather than leaving an empty frame. */
  let recordToken = 0;

  async function mountRecord(ticker) {
    const host = $("#recordSection");
    if (!host) return;
    const token = ++recordToken;
    host.innerHTML = `<section class="glass dimblock">
      <div class="skeleton line" style="width:22%"></div>
      <div class="skeleton" style="height:120px;margin-top:16px"></div></section>`;
    try {
      const record = await api.record(ticker);
      if (token !== recordToken) return;      // a newer company is already loading
      host.innerHTML = recordBlock(record);
    } catch (err) {
      if (token !== recordToken) return;
      host.innerHTML = "";
      console.warn("record fetch failed", err);
    }
  }

  /* ------------------------------------------------------------------ render */
  function render(row, { rankedNeighbours = [] } = {}) {
    const rank = row.rank;
    const neighbours = rankedNeighbours.filter((r) => r.rank !== null && r.rank !== undefined);
    const index = neighbours.findIndex((r) => r.ticker === row.ticker);
    const prev = index > 0 ? neighbours[index - 1] : null;
    const next = index >= 0 && index < neighbours.length - 1 ? neighbours[index + 1] : null;

    // this company's review CSV + the whole-pool Excel, both keeping the snapshot
    const detailQS = new URLSearchParams(context);
    detailQS.set("ticker", row.ticker);
    const poolQS = new URLSearchParams(context);
    const exports = `
      <a class="btn btn--primary btn--sm" href="/api/export/detail?${detailQS.toString()}">
        ${icon("download")}<span>${t("co.detail.csv")}</span></a>
      <a class="btn btn--quiet btn--sm" href="/api/export/xlsx${poolQS.toString() ? "?" + poolQS.toString() : ""}">
        ${icon("download")}<span>${t("co.pool.xlsx")}</span></a>
      <button class="btn btn--quiet btn--sm" type="button" id="copyDetail">${icon("copy")}<span>${t("co.copy")}</span></button>`;

    const nav = `
      <div class="ranknav">
        <a class="btn btn--quiet btn--sm" ${prev ? `href="${companyHref(prev.ticker)}"` : 'aria-disabled="true" tabindex="-1"'}
          title="${prev ? t("co.prev", { rank: prev.rank, ticker: escapeHtml(prev.ticker) }) : t("co.first")}">
          ${icon("chevron")}<span>${prev ? `#${prev.rank} ${escapeHtml(prev.ticker)}` : t("co.first")}</span></a>
        <span class="badge badge--accent">#${rank ?? DASH} / ${neighbours.length}</span>
        <a class="btn btn--quiet btn--sm" ${next ? `href="${companyHref(next.ticker)}"` : 'aria-disabled="true" tabindex="-1"'}
          title="${next ? t("co.next", { rank: next.rank, ticker: escapeHtml(next.ticker) }) : t("co.last")}">
          <span>${next ? `#${next.rank} ${escapeHtml(next.ticker)}` : t("co.last")}</span>${icon("chevron")}</a>
      </div>`;

    return `
      <div class="crumb"><a href="${rankingHref}">${t("nav.ranking")}</a><span class="sep">/</span>
        <b>${escapeHtml(row.ticker)}</b></div>
      ${heroCard(row)}
      <div id="companyInsights" aria-live="polite"></div>
      <div class="detailbar">
        ${nav}
        <span class="spacer"></span>
        <div class="row" style="gap:8px">${exports}</div>
      </div>
      ${scoreRail(row)}
      ${DIMENSIONS.map((d) => dimensionBlock(row, d)).join("")}
      <div id="quarterSection" aria-live="polite"></div>
      <div id="recordSection" aria-live="polite"></div>
      ${optionsLink(row)}
      <div id="analystSection" aria-live="polite"></div>
      ${inputBlock(row)}
      ${provenanceBlock(row)}`;
  }

  /* -------------------------------------------------------------------- copy */
  function toTsv(row) {
    const lines = [];
    lines.push(`# ${row.ticker} ${row.company || ""}`);
    const meta = [
      [t("common.overallOfTen"), num(row.score_overall, 2)],
      [t("common.rank"), row.rank ?? ""],
      [t("common.profile"), FR.profileLabel(row.profile)],
      [t("common.coverage"), `${row.data_coverage}/${scoredCount()}`],
    ];
    meta.forEach(([k, v]) => lines.push(`${k}\t${v}`));
    lines.push("");
    lines.push([t("common.dimension"), t("common.score"), t("common.weight")].join("\t"));
    DIMENSIONS.forEach((d) => lines.push(`${t(d.i18n)}\t${num(dimensionScore(row, d), 2)}\t${(d.weight * 100).toFixed(0)}%`));
    lines.push(`${t("common.overall")}\t${num(row.score_overall, 2)}\t`);
    lines.push("");
    lines.push([t("common.metric"), t("common.stock"), t("common.peerMedian"),
                t("common.rank"), t("common.score"), t("common.direction")].join("\t"));
    DIMENSIONS.forEach((d) => metricsFor(d).forEach((m) => {
      lines.push([
        labelFor(m.attr) + (m.scored ? "" : ` (${t("co.refTag")})`),
        fmt(row[m.attr], m.kind),
        fmt(peerMedian(m.attr), m.kind),
        withinGroupRank(m.attr, row[m.attr], m.higher_is_better) ?? "",
        m.scored ? num(row["z_" + (Z_SUFFIX[m.attr] || m.attr)], 2) : "",
        m.higher_is_better ? t("common.higher") : t("common.lower"),
      ].join("\t"));
    }));
    return lines.join("\n");
  }

  async function copyDetail(row) {
    const text = toTsv(row);
    try {
      await navigator.clipboard.writeText(text);
      toast(t("co.toast.copied"));
    } catch {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
      toast(t("rk.toast.copiedShort"));
    }
  }

  const Z_SUFFIX = {
    revenue_growth_yoy: "growth_yoy", revenue_cagr_5y: "revenue_cagr",
    eps_growth_fy1: "eps_growth_fy1", eps_cagr_5y: "eps_cagr",
    gross_margin: "gross_margin", operating_margin: "operating_margin", net_margin: "net_margin",
    roe: "roe", roic: "roic", roa: "roa",
    fcf_margin: "fcf_margin", fcf_yield: "fcf_yield",
    ocf_to_net_income: "cash_conversion", capex_intensity: "capex_intensity",
    forward_pe: "forward_pe", price_to_sales: "price_sales",
    ev_to_ebitda: "ev_ebitda", price_to_book: "price_book", price_to_fcf: "price_fcf",
    return_1y: "return_1y", return_3m: "return_3m", return_6m: "return_6m",
    excess_return_1y: "excess_return_1y",
    excess_return_3m: "excess_return_3m", excess_return_6m: "excess_return_6m",
    volatility: "volatility", sharpe_ratio: "sharpe", sortino_ratio: "sortino",
    max_drawdown_1y: "max_drawdown", beta_1y: "beta_1y",
    debt_to_assets: "debt_assets", drawdown_52w: "drawdown", beta: "beta",
  };

  /** Fetch the backend's metric catalogue once; every block is built from it. */
  let catalogPromise = null;

  async function ensureCatalog() {
    if (CATALOG) return CATALOG;
    if (!catalogPromise) {
      catalogPromise = api.metrics()
        .then((payload) => {
          buildCatalog(payload);
          return CATALOG;
        })
        .catch((err) => {
          catalogPromise = null;   // allow a retry on the next mount
          toast(err.message || t("co.catalogFailed"), "err");
          throw err;
        });
    }
    return catalogPromise;
  }

  /* -------------------------------------------------------------- page entry */
  function params() {
    const qs = new URLSearchParams(location.search);
    return {
      ticker: (qs.get("ticker") || "").toUpperCase(),
      job_id: qs.get("job_id") || undefined,
      run_id: qs.get("run_id") || undefined,
      qs,
    };
  }

  async function mount({ force = false } = {}) {
    const host = $("#companyBody");
    const { ticker, job_id, run_id, qs } = params();
    // keep only the snapshot context so links never stack duplicate tickers
    context = new URLSearchParams();
    if (qs.get("job_id")) context.set("job_id", qs.get("job_id"));
    if (qs.get("run_id")) context.set("run_id", qs.get("run_id"));
    sourceQS = context.toString() ? `?${context.toString()}` : "";
    rankingHref = `ranking.html${sourceQS}`;

    if (!ticker) {
      host.innerHTML = `<div class="glass">${stateBlock({
        icon: "search", title: t("co.missingTicker.title"),
        body: t("co.missingTicker.body"),
        action: { href: "ranking.html", label: t("action.back.ranking") },
      })}</div>`;
      return;
    }

    host.innerHTML = skeleton();
    document.title = `${ticker} · ${t("co.title")} · FinanceRanker`;

    try {
      // The catalogue defines which metrics exist and which are scored, so it
      // is fetched before anything is rendered from it.
      await ensureCatalog();
      const rows = await ensurePool({ job_id, run_id }, { force });
      const row = rows.find((r) => r.ticker === ticker);
      if (!row) {
        host.innerHTML = `<div class="glass">${stateBlock({
          icon: "alert", title: t("co.notInPool.title", { ticker }),
          body: t("co.notInPool.body"),
          action: { href: rankingHref, label: t("action.back.ranking") },
        })}</div>`;
        return;
      }
      const neighbours = rows.slice().sort((a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9));
      lastRender = { row, neighbours };
      host.innerHTML = render(row, { rankedNeighbours: neighbours });
      // The analyst section is enrichment, fetched separately so a slow or
      // unavailable endpoint never delays the scored comparison above it.
      if (typeof FRAnalyst !== "undefined") FRAnalyst.mount(row.ticker);
      mountQuarters(row.ticker);
      mountRecord(row.ticker);
      const copy = $("#copyDetail");
      // keep the column rhythm correct when the breakpoint changes
      const onResize = FR.debounce(() => {
        $$("#companyBody colgroup.metric-cols").forEach((cg) => { cg.innerHTML = colgroupHTML(); });
      }, 180);
      window.addEventListener("resize", onResize, { once: false, passive: true });
      if (copy) copy.addEventListener("click", () => copyDetail(row));
      document.title = `${row.ticker} · ${row.company || t("co.crumb")} · FinanceRanker`;
    } catch (err) {
      host.innerHTML = `<div class="glass">${stateBlock({
        icon: "alert", title: t("co.loadFailed.title"),
        body: escapeHtml(err.status === 404 ? t("co.loadFailed.body.none") : (err.message || t("common.readFailed"))),
        action: { href: "refresh.html", label: t("action.go.refresh") },
      })}</div>`;
    }
  }

  /** Repaint in the new language without another round trip. */
  function relabel() {
    if (!lastRender) return;
    const host = $("#companyBody");
    if (!host) return;
    host.innerHTML = render(lastRender.row, { rankedNeighbours: lastRender.neighbours });
    // Repaint the analyst section from the payload already in memory.
    if (typeof FRAnalyst !== "undefined") FRAnalyst.relabel();
    const copy = $("#copyDetail");
    if (copy) copy.addEventListener("click", () => copyDetail(lastRender.row));
    document.title = `${lastRender.row.ticker} · ${lastRender.row.company || t("co.crumb")} · FinanceRanker`;
  }

  return {
    mount, render, relabel, ensureCatalog, DIMENSIONS, INPUT_GROUPS, Z_SUFFIX,
    peerMedian, withinGroupRank, fmt, toTsv,
    // Exposed so a test can drive the quarterly renderer with a real server
    // payload. A fixture cannot catch a payload shape the renderer mis-handles,
    // which is exactly how one ticker renders while another comes up blank.
    quarterBlock, quarterCard,
  };
})();
