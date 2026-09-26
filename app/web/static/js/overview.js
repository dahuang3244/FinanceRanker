/* Overview — scoreboard, leaders, coverage matrix and run history strip. */
"use strict";

(() => {
  const {
    api, icon, $, el, escapeHtml, toast, stateBlock,
    num, pct, signedPct, mult, money, compact, when, ago, tone, profilePill, DASH,
  } = FR;

  const FACETS = [
    ["dim.growth", "score_growth"],
    ["dim.profitability", "score_profitability"],
    ["dim.cash", "score_cash"],
    ["dim.valuation", "score_valuation"],
    ["dim.market", "score_market"],
  ];

  const MODEL = {
    fundamentals: ["ov.kind.fundamentals", "SEC XBRL companyfacts", "akshare"],
    prices: ["ov.kind.prices", "Sina US daily", "akshare → Yahoo"],
    quotes: ["ov.kind.quotes", "Tencent qt.gtimg.cn", "Eastmoney → Yahoo"],
  };

  const state = { rows: [], runs: [], config: null, health: null, matrixEligible: false };

  /* ------------------------------------------------------------- scoreboard */
  function renderBoard(errors) {
    const rows = state.rows;
    const eligible = rows.filter((r) => r.rank_eligible);
    const scored = rows.filter((r) => r.score_overall !== null && r.score_overall !== undefined);
    const values = scored.map((r) => r.score_overall).sort((a, b) => b - a);
    const median = values.length
      ? (values.length % 2 ? values[(values.length - 1) / 2]
        : (values[values.length / 2 - 1] + values[values.length / 2]) / 2)
      : null;
    const top = rows.find((r) => r.rank === 1) || scored.sort((a, b) => b.score_overall - a.score_overall)[0];
    const failing = Object.keys(errors || {}).length;

    $("#boardHeadline").innerHTML = rows.length
      ? t("ov.headline.body", { n: rows.length, m: eligible.length })
      : t("ov.headline.empty");
    $("#boardSub").innerHTML = rows.length
      ? t("ov.sub.body", {
        ago: ago((state.runs[0] || {}).started_at),
        failed: failing ? t("ov.sub.failed", { n: failing }) : "",
      })
      : t("ov.sub.empty");

    const set = (id, value, note, noteClass) => {
      const cell = $(id);
      cell.querySelector(".v").innerHTML = value;
      const d = cell.querySelector(".d");
      d.textContent = note || DASH;
      d.className = "d" + (noteClass ? " " + noteClass : "");
    };

    set("#cellCoverage", `${rows.length}<small>${t("common.tickers")}</small>`,
      state.config ? t("ov.cell.concurrency", { n: state.config.max_concurrency }) : DASH);
    set("#cellTop", top ? `${num(top.score_overall, 2)}<small>/10</small>` : DASH,
      top ? `${top.ticker} · ${top.company ? top.company.slice(0, 16) : ""}` : DASH);
    set("#cellMedian", median === null ? DASH : `${num(median, 2)}<small>/10</small>`,
      values.length ? t("ov.cell.range", { a: num(values[values.length - 1], 1), b: num(values[0], 1) }) : DASH);
    const run = state.runs[0];
    set("#cellSnap", run ? `${run.row_count}<small>${t("common.rows")}</small>` : DASH,
      run ? `${run.trigger} · ${ago(run.started_at)}` : DASH);
  }

  /* ---------------------------------------------------------------- matrix */
  /* 0–10 swatch strip, generated from the shared scale so the legend can never
     disagree with the cells it explains. */
  function renderLegend() {
    const host = $("#matrixLegend");
    if (!host || typeof FRScale === "undefined") return;
    host.innerHTML = FRScale.legendSteps().map((i) => {
      const { background, color } = FRScale.swatch(i);
      return `<span class="scale-legend-cell" style="background:${background};color:${color}"
        title="${i} / 10">${i}</span>`;
    }).join("");
  }

  function renderMatrix() {
    const body = $("#matrixBody");
    const rows = state.rows
      .filter((r) => !state.matrixEligible || r.rank_eligible)
      .slice()
      .sort((a, b) => (b.score_overall ?? -1) - (a.score_overall ?? -1));

    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="9">${stateBlock({
        icon: "filter",
        title: t("ov.matrix.empty.title"),
        body: state.matrixEligible ? t("ov.matrix.empty.body") : t("ov.leaders.empty.body"),
      })}</td></tr>`;
      return;
    }

    body.innerHTML = rows.map((row) => `
      <tr>
        <td><a class="tick-link" href="company.html?ticker=${encodeURIComponent(row.ticker)}">${escapeHtml(row.ticker)}</a></td>
        ${FACETS.map(([, key]) => `<td>${swatch(row[key])}</td>`).join("")}
        <td>${swatch(row.score_overall)}</td>
        <td class="mono">${row.data_coverage}/21</td>
        <td>${profilePill(row.profile)}</td>
      </tr>`).join("");

  }

  function swatch(v) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) {
      return `<span class="swatch na">${DASH}</span>`;
    }
    // Diverging red↔green scale, shared with the ranking/history meter bars:
    // 5.5 is neutral, red is weak, green is strong. Full opacity per cell so a
    // column can be compared by hue alone.
    const { background, color } = FRScale.swatch(v);
    return `<span class="swatch" style="background:${background};color:${color}"
      title="${num(v, 2)} / 10">${num(v, 1)}</span>`;
  }

  /* ------------------------------------------------------------ run strip */
  function renderStrip() {
    const host = $("#runstrip");
    const runs = state.runs.slice(0, 18).reverse();
    if (!runs.length) {
      host.innerHTML = `<div class="state" style="padding:18px"><p>${t("ov.recent.none")}</p></div>`;
      $("#stripMeta").textContent = "";
      return;
    }
    const max = Math.max(...runs.map((r) => r.row_count), 1);
    host.innerHTML = runs.map((run) => {
      const h = Math.max(8, Math.round((run.row_count / max) * 100));
      return `<div class="col${run.row_count ? "" : " zero"}" style="height:${h}%">
        <span class="tip">${when(run.started_at)} · ${run.row_count} ${t("common.rows")} · ${run.trigger}</span>
      </div>`;
    }).join("");

    const total = state.runs.reduce((a, r) => a + r.row_count, 0);
    const failed = state.runs.reduce((a, r) => a + r.error_count, 0);
    $("#stripMeta").innerHTML = `
      <span>${t("ov.recent.meta", { runs: state.runs.length, rows: total })}</span>
      <span class="spacer"></span>
      <span>${t("ov.recent.failed", { n: failed })}</span>
      <span>${t("ov.recent.latest", { when: when(state.runs[0].started_at) })}</span>`;
  }

  /* -------------------------------------------------------------- providers */
  function renderProviders() {
    const host = $("#provlist");
    const h = state.health;
    if (!h) {
      host.innerHTML = `<div class="state" style="padding:14px"><p>${t("ov.providers.offline")}</p></div>`;
      return;
    }
    const chain = {
      fundamentals: String(h.providers.fundamentals || "").split("->").map((s) => s.trim()),
      prices: String(h.providers.prices || "").split("->").map((s) => s.trim()),
      quotes: String(h.providers.quotes || "").split("->").map((s) => s.trim()),
    };
    host.innerHTML = Object.entries(MODEL).map(([key, [labelKey, primary, backup]]) => {
      const steps = chain[key] && chain[key].length ? chain[key] : [primary, backup];
      return `<div class="prov">
        <span class="ico">${icon(key === "fundamentals" ? "shield" : key === "prices" ? "trend" : "zap")}</span>
        <span class="nm">${t(labelKey)}<br /><span class="faint" style="font-size:11px">${escapeHtml(steps[0])}</span></span>
        <span class="src">${steps.length > 1
          ? t("ov.providers.backup", { list: escapeHtml(steps.slice(1).join(" / ")) })
          : t("ov.providers.nobackup")}<br />${
          h.providers.yahoo_in_use ? t("ov.providers.yahooOn") : t("ov.providers.yahooOff")}</span>
      </div>`;
    }).join("");
  }

  /* --------------------------------------------------------- risk-free rate */
  /* The reference every return is judged against. Fetched separately from the
     ranking, because a Treasury outage must not delay the peer comparison. */
  const TENOR_ORDER = ["1m", "3m", "6m", "1y", "2y", "5y", "7y", "10y", "20y", "30y"];

  function renderRates() {
    const host = $("#ratesBody");
    if (!host) return;
    const curve = state.rates;
    if (!curve || !Object.keys(curve).length) {
      host.innerHTML = `<p class="hint">${t("ov.rates.offline")}</p>`;
      return;
    }
    const spread = curve.spread_10y_2y;
    const inverted = spread !== undefined && spread !== null && spread < 0;
    host.innerHTML = `
      <div class="ratesrow">${
        TENOR_ORDER.filter((k) => curve[k] !== undefined).map((k) => `
          <div class="ratetile${k === "10y" ? " is-key" : ""}">
            <span class="k">${escapeHtml(k.toUpperCase())}</span>
            <span class="v mono">${Number(curve[k]).toFixed(2)}%</span>
          </div>`).join("")}
      </div>
      <div class="ratesmeta">
        ${spread === undefined || spread === null ? "" : `
          <span class="ptag ${inverted ? "tone-low" : "tone-good"}">${
            t("ov.rates.spread", { v: spread.toFixed(2) })}</span>`}
        ${inverted ? `<span class="ptag tone-low">${t("ov.rates.inverted")}</span>` : ""}
        <span class="spacer"></span>
        <span class="faint">${t("ov.rates.asof", { d: escapeHtml(String(curve.date || "")) })}</span>
      </div>`;
  }

  /* -------------------------------------------------------------- assemble */
  async function load({ fresh = false } = {}) {
    const rank = await api.ranking({});
    state.rows = rank.rows || [];
    state.errors = rank.errors || {};
    renderBoard(state.errors);
    renderLegend();
    renderMatrix();
    if (fresh) FR.toast(t("rf.toast.done", { n: state.rows.length }));
  }

  window.pageInit = async function pageInit() {
    // header actions
    $("#ovRefresh").innerHTML = `${icon("refresh")}<span data-i18n="action.refresh">${t("action.refresh")}</span>`;
    $("#ovRun").innerHTML = `${FR.solid("play")}<span data-i18n="action.start">${t("action.start")}</span>`;
    $("#provMore").innerHTML = `<span data-i18n="ov.more">${t("ov.more")}</span>${icon("chevron")}`;
    $("#stripMore").innerHTML = `<span data-i18n="ov.history">${t("ov.history")}</span>${icon("chevron")}`;

    const applyHealth = (detail) => {
      state.health = detail && detail.ok ? detail.health : null;
      state.runs = (detail && detail.runs) || state.runs;
      state.config = (detail && detail.config) || state.config;
      renderProviders();
      renderStrip();
    };

    document.addEventListener("fr:health", (event) => applyHealth(event.detail || {}));

    $("#ovRefresh").addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      try {
        applyHealth(await FR.ping());
        await load({ fresh: true });
      } catch (err) {
        toast(err.message || t("rk.err.load"), "err");
      } finally {
        btn.disabled = false;
      }
    });

    $("#matrixEligible").addEventListener("change", (e) => {
      state.matrixEligible = e.target.checked;
      renderMatrix();
    });

    try {
      const [health, runs, config] = await Promise.all([api.health(), api.runs(60), api.config()]);
      state.health = health;
      state.runs = runs.runs || [];
      state.config = config;
      renderProviders();
      renderStrip();
    } catch (err) {
      state.health = null;
      renderProviders();
      $("#runstrip").innerHTML = `<div class="state" style="padding:18px">
        <p>${t("ov.recent.offline", { msg: escapeHtml(err.message || "") })}</p></div>`;
      $("#stripMeta").textContent = "";
    }

    try {
      await load();
    } catch (err) {
      $("#boardHeadline").textContent = t("ov.headline.empty");
      $("#boardSub").innerHTML = err.status === 404
        ? t("ov.sub.empty")
        : `${t("common.readFailed")}: ${err.message}`;
      state.rows = [];
      renderMatrix();
    }

    // The risk-free curve is enrichment: fetched after the ranking so a Treasury
    // outage never delays or blanks the peer comparison.
    try {
      const rates = await api.rates();
      state.rates = rates.curve || null;
    } catch (err) {
      state.rates = null;
    }
    renderRates();

    // re-render every dynamic block on a language flip
    document.addEventListener("fr:lang", () => {
      if (!state.rows.length) {
        $("#boardHeadline").textContent = t("ov.headline.empty");
        $("#boardSub").innerHTML = t("ov.sub.empty");
      }
      renderBoard(state.errors || {});
      renderLegend();
      renderMatrix();
      renderStrip();
      renderProviders();
    });
  };
})();
