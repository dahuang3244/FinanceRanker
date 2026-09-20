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
  // `kind` drives formatting: ratio -> %, multiple -> x, money -> $, num -> plain
  const DIMENSIONS = [
    {
      key: "growth", i18n: "dim.growth", score: "score_growth", weight: 0.25, icon: "trend",
      metrics: [
        { attr: "revenue_growth_yoy", kind: "ratio", higher: true },
        { attr: "revenue_cagr_5y", kind: "ratio", higher: true },
        { attr: "eps_growth_fy1", kind: "ratio", higher: true },
        { attr: "eps_cagr_5y", kind: "ratio", higher: true },
      ],
    },
    {
      key: "profitability", i18n: "dim.profitability", score: "score_profitability", weight: 0.25, icon: "coins",
      metrics: [
        { attr: "gross_margin", kind: "ratio", higher: true },
        { attr: "operating_margin", kind: "ratio", higher: true },
        { attr: "net_margin", kind: "ratio", higher: true },
        { attr: "roe", kind: "ratio", higher: true },
        { attr: "roic", kind: "ratio", higher: true },
      ],
    },
    {
      key: "cash", i18n: "dim.cash", score: "score_cash", weight: 0.20, icon: "layers",
      metrics: [
        { attr: "fcf_margin", kind: "ratio", higher: true },
        { attr: "fcf_yield", kind: "ratio", higher: true },
        { attr: "ocf_to_net_income", kind: "multiple", higher: true },
      ],
    },
    {
      key: "valuation", i18n: "dim.valuation", score: "score_valuation", weight: 0.20, icon: "shield",
      metrics: [
        { attr: "forward_pe", kind: "multiple", higher: false },
        { attr: "price_to_sales", kind: "multiple", higher: false },
        { attr: "ev_to_ebitda", kind: "multiple", higher: false },
        { attr: "price_to_book", kind: "multiple", higher: false },
        { attr: "price_to_fcf", kind: "multiple", higher: false },
      ],
    },
    {
      key: "market", i18n: "dim.market", score: "score_market", weight: 0.10, icon: "zap",
      metrics: [
        { attr: "return_1y", kind: "ratio", higher: true },
        { attr: "debt_to_assets", kind: "ratio", higher: false },
        { attr: "drawdown_52w", kind: "ratio", higher: true },
        { attr: "beta", kind: "num", higher: false },
      ],
    },
  ];

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

  /** Currency the filings are in (what every raw-input figure is quoted in). */
  function filingCurrency(row) {
    return row.filing_currency || row.currency || "USD";
  }

  /**
   * Whether the filings and the traded price are in different currencies.
   *
   * Keyed on filing vs trading, not on "is it USD": a foreign private issuer's
   * ADR trades in USD while its 20-F is filed in the home currency, so the old
   * `row.currency !== "USD"` test never fired for exactly the issuers it was
   * written for (TSM), and the TWD figures below looked like US dollars.
   */
  function crossCurrency(row) {
    return !!row.currency && filingCurrency(row) !== row.currency;
  }

  /** Whether a filing currency is a real one; "unconfirmed" is not. */
  function isCurrency(ccy) {
    return /^[A-Z]{3}$/.test(ccy || "");
  }

  /** Hero badge for a filing currency that is not the traded one. */
  function currencyBadge(ccy) {
    return isCurrency(ccy) ? t("co.currencyNonUsd", { ccy }) : t("co.currencyUnknown");
  }

  /** Why those figures are withheld — the badge's tooltip. */
  function currencyHint(ccy) {
    return isCurrency(ccy) ? t("co.currencyNonUsd.hint", { ccy }) : t("co.currencyUnknown.hint");
  }

  /** Unit label for the raw filing inputs block. */
  function inputsCurrency(ccy) {
    return isCurrency(ccy)
      ? t("co.calcInputs.currency", { ccy })
      : t("co.calcInputs.currencyUnknown");
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
              <span class="badge">${t("common.coverage")} ${row.data_coverage}/21</span>
              ${crossCurrency(row)
                ? `<span class="badge badge--mid" title="${escapeHtml(currencyHint(filingCurrency(row)))}">${escapeHtml(currencyBadge(filingCurrency(row)))}</span>` : ""}
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
    const rank = withinGroupRank(metric.attr, value, metric.higher);
    const za = "z_" + (FRCompany.Z_SUFFIX[metric.attr] || metric.attr);
    const score = row[za];
    const hasValue = value !== null && value !== undefined;
    const delta = hasValue && median !== null ? value - median : null;
    const deltaBetter = delta === null ? null : (metric.higher ? delta >= 0 : delta <= 0);

    const present = pool.filter((r) => r[metric.attr] !== null && r[metric.attr] !== undefined).length;
    const cells = {
      // `DIMENSIONS` entries carry only `attr`, so the name comes from the
      // shared i18n dictionary. Reading `metric.label` / `metric.en` here left
      // every row's 指标 cell blank for every ticker.
      metric: `<td class="mlabel">
        <b>${escapeHtml(labelFor(metric.attr))}</b>
        <em>${escapeHtml(subLabelFor(metric.attr))}</em>
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
      bar: `<td class="mbar"><span class="mbarline"><i class="tone-${tone(score)}" style="transform:scaleX(${score === null || score === undefined ? 0 : Math.max(0, Math.min(1, score / 10)).toFixed(3)})"></i></span></td>`,
      score: `<td class="right mscore tone-${tone(score)}"><span class="mono">${num(score, 2)}</span></td>`,
    };
    return `<tr class="${hasValue ? "" : "is-missing"}" data-attr="${metric.attr}">
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
    const present = dim.metrics.filter((m) => row[m.attr] !== null && row[m.attr] !== undefined).length;
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
            <span class="badge">${present}/${dim.metrics.length} ${t("common.itemsWithValue")}</span>
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
            <tbody>${dim.metrics.map((m) => metricRow(row, m)).join("")}</tbody>
          </table>
        </div>
      </section>`;
  }

  function inputBlock(row) {
    return `<section class="glass dimblock inputs">
      <header class="dimhead">
        <span class="dimico">${icon("database")}</span>
        <div class="dimtitle">
          <h2>${t("co.calcInputs")}<em>${t("co.calcInputs.en")}</em></h2>
          <p>${t("co.calcInputs.hint")}</p>
        </div>
        ${crossCurrency(row)
          ? `<div class="dimmeta"><span class="badge badge--mid">${escapeHtml(inputsCurrency(filingCurrency(row)))}</span></div>`
          : ""}
      </header>
      <div class="inputgrid">
        ${INPUT_GROUPS.map((g) => `
          <div class="inputgroup">
            <h3>${icon(g.icon)}<span>${t(g.i18n)}</span></h3>
            <dl class="dl">
              ${g.rows.map(([attr, kind]) => `
                <dt>${t(`in.${attr}`)}</dt>
                <dd class="${row[attr] === null || row[attr] === undefined ? "na" : ""}">${fmt(row[attr], kind)}</dd>`).join("")}
            </dl>
          </div>`).join("")}
      </div>
    </section>`;
  }

  function provenanceBlock(row) {
    const items = [
      [t("co.field.secSource"), row.sec_source || DASH],
      [t("co.field.marketSource"), row.market_source || DASH],
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
      <div class="detailbar">
        ${nav}
        <span class="spacer"></span>
        <div class="row" style="gap:8px">${exports}</div>
      </div>
      ${scoreRail(row)}
      ${DIMENSIONS.map((d) => dimensionBlock(row, d)).join("")}
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
      [t("common.coverage"), `${row.data_coverage}/21`],
    ];
    meta.forEach(([k, v]) => lines.push(`${k}\t${v}`));
    lines.push("");
    lines.push([t("common.dimension"), t("common.score"), t("common.weight")].join("\t"));
    DIMENSIONS.forEach((d) => lines.push(`${t(d.i18n)}\t${num(dimensionScore(row, d), 2)}\t${(d.weight * 100).toFixed(0)}%`));
    lines.push(`${t("common.overall")}\t${num(row.score_overall, 2)}\t`);
    lines.push("");
    lines.push([t("common.metric"), t("common.stock"), t("common.peerMedian"),
                t("common.rank"), t("common.score"), t("common.direction")].join("\t"));
    DIMENSIONS.forEach((d) => d.metrics.forEach((m) => {
      lines.push([
        labelFor(m.attr),
        fmt(row[m.attr], m.kind),
        fmt(peerMedian(m.attr), m.kind),
        withinGroupRank(m.attr, row[m.attr], m.higher) ?? "",
        num(row["z_" + (Z_SUFFIX[m.attr] || m.attr)], 2),
        m.higher ? t("common.higher") : t("common.lower"),
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
    roe: "roe", roic: "roic", fcf_margin: "fcf_margin", fcf_yield: "fcf_yield",
    ocf_to_net_income: "cash_conversion", forward_pe: "forward_pe", price_to_sales: "price_sales",
    ev_to_ebitda: "ev_ebitda", price_to_book: "price_book", price_to_fcf: "price_fcf",
    return_1y: "return_1y", debt_to_assets: "debt_assets", drawdown_52w: "drawdown", beta: "beta",
  };

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
    const copy = $("#copyDetail");
    if (copy) copy.addEventListener("click", () => copyDetail(lastRender.row));
    document.title = `${lastRender.row.ticker} · ${lastRender.row.company || t("co.crumb")} · FinanceRanker`;
  }

  return {
    mount, render, relabel, DIMENSIONS, INPUT_GROUPS, Z_SUFFIX,
    peerMedian, withinGroupRank, fmt, toTsv,
  };
})();
