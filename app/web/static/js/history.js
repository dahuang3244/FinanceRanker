/* History — run timeline, snapshot detail, score trend and run-to-run compare. */
"use strict";

(() => {
  const {
    api, icon, solid, $, $$, escapeHtml, toast, stateBlock,
    num, pct, signedPct, mult, money, when, ago, DASH,
  } = FR;

  const FACETS = [
    ["dim.growth", "score_growth"],
    ["dim.profitability", "score_profitability"],
    ["dim.cash", "score_cash"],
    ["dim.valuation", "score_valuation"],
    ["dim.market", "score_market"],
  ];

  const state = {
    runs: [],
    current: null,     // run_id
    rows: [],
    compareId: "",
    compareRows: null,
    trendTicker: "",
    trend: [],
  };

  /* ------------------------------------------------------------------- svg */
  function sparkline(series, compare, width = 720, height = 82) {
    const padL = 26;
    const padB = 18;
    const plotH = height - padB - 6;
    const all = [...series.map((s) => s.score_overall), ...(compare || []).map((s) => s.score_overall)]
      .filter((v) => v !== null && v !== undefined);
    if (!all.length) return `<div class="state" style="padding:18px"><p>${t("hs.trend.empty")}</p></div>`;

    // A full 0–10 axis flattens real differences (pool scores cluster tightly),
    // so the window tracks the data — at least two points wide.
    const lo = Math.min(...all);
    const hi = Math.max(...all);
    const band = Math.max(2, hi - lo + 0.8);
    const mid = (lo + hi) / 2;
    const min = Math.max(0, Math.round((mid - band / 2) * 100) / 100);
    const max = Math.min(10, Math.round((mid + band / 2) * 100) / 100);
    const span = Math.max(0.5, max - min);
    const n = series.length;
    const x = (i) => padL + (n <= 1 ? (width - padL) / 2 : (i * (width - padL - 8)) / (n - 1));
    const y = (v) => 6 + plotH - ((v - min) / span) * plotH;

    const path = (list) => list.map((s, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(s.score_overall).toFixed(1)}`).join(" ");
    const area = n > 1
      ? `${path(series)} L${x(n - 1).toFixed(1)},${(6 + plotH).toFixed(1)} L${x(0).toFixed(1)},${(6 + plotH).toFixed(1)} Z`
      : "";

    const gridValues = [min, min + span / 2, max];
    const grid = gridValues.map((v) => `
      <line class="grid-line" x1="${padL}" x2="${width - 8}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" vector-effect="non-scaling-stroke" />
      <text class="axis" x="2" y="${(y(v) + 3).toFixed(1)}">${v.toFixed(1)}</text>`).join("");

    const dots = series.map((s, i) => `
      <circle class="pt" cx="${x(i).toFixed(1)}" cy="${y(s.score_overall).toFixed(1)}" r="2.4" vector-effect="non-scaling-stroke">
        <title>${escapeHtml(when(s.captured_at))} · ${num(s.score_overall, 2)}</title>
      </circle>`).join("");

    const comparePath = compare && compare.length > 1
      ? `<path d="${compare.map((s, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(s.score_overall).toFixed(1)}`).join(" ")}"
           fill="none" stroke="var(--faint)" stroke-width="1.4" stroke-dasharray="4 3" vector-effect="non-scaling-stroke" />`
      : "";

    const first = series[0];
    const last = series[n - 1];
    // Axis dates ride in HTML rather than the stretched SVG so they keep a
    // consistent optical size at every width.
    const date = (iso) => String(iso || "").slice(5, 10);
    return `
      <svg class="spark" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img"
        aria-label="${escapeHtml(state.trendTicker)} 总分走势 ${num(first.score_overall, 2)} 至 ${num(last.score_overall, 2)}">
        <defs>
          <linearGradient id="sparkFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="rgba(189,92,60,0.28)" />
            <stop offset="100%" stop-color="rgba(189,92,60,0.02)" />
          </linearGradient>
        </defs>
        ${grid}
        ${area ? `<path class="area" d="${area}" />` : ""}
        <path class="line" d="${path(series)}" vector-effect="non-scaling-stroke" />
        ${comparePath}
        ${dots}
      </svg>
      <div class="spark-axis">
        <span>${escapeHtml(date(first.captured_at))}</span>
        <span class="mid">${t("hs.trend.snapshots", { n })}</span>
        <span>${escapeHtml(date(last.captured_at))}</span>
      </div>`;
  }

  function animateSpark() {
    const line = $(".spark .line");
    if (!line || typeof line.getTotalLength !== "function") return;
    let length = 0;
    try { length = line.getTotalLength(); } catch { return; }
    if (!length) return;
    line.style.strokeDasharray = `${length}`;
    line.style.strokeDashoffset = `${length}`;
    requestAnimationFrame(() => {
      line.style.transition = "stroke-dashoffset 1.1s var(--ease)";
      line.style.strokeDashoffset = "0";
    });
  }

  /* --------------------------------------------------------------- timeline */
  function renderTimeline() {
    const host = $("#timeline");
    if (!state.runs.length) {
      host.innerHTML = stateBlock({
        icon: "clock",
        title: t("hs.timeline.empty.title"),
        body: t("hs.timeline.empty.body"),
        action: { href: "refresh.html", label: t("action.go.refresh") },
      });
      return;
    }

    host.innerHTML = state.runs.map((run) => `
      <button class="tl-item" type="button" data-run="${escapeHtml(run.run_id)}"
        aria-current="${run.run_id === state.current ? "true" : "false"}">
        <span class="rail-line"><span class="node"></span></span>
        <span>
          <span class="when">${escapeHtml(when(run.started_at))}</span>
          <span class="sub">${run.row_count} ${t("common.rows")} · ${run.error_count
            ? `<span class="bad">${run.error_count} ${t("rf.art.errors")}</span>`
            : `0 ${t("rf.art.errors")}`} · ${escapeHtml(run.trigger)}</span>
        </span>
      </button>`).join("");

    host.querySelectorAll("[data-run]").forEach((b) =>
      b.addEventListener("click", () => selectRun(b.dataset.run)));
    $("#timelineHint").textContent = t("hs.timeline.count", { n: state.runs.length, ago: ago(state.runs[0].started_at) });
  }

  /* ------------------------------------------------------------ run detail */
  function statsFor(rows) {
    const scored = rows.filter((r) => r.score_overall !== null && r.score_overall !== undefined);
    const values = scored.map((r) => r.score_overall).sort((a, b) => b - a);
    const avg = values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
    return {
      rows: rows.length,
      eligible: rows.filter((r) => r.rank_eligible).length,
      avg,
      top: rows.find((r) => r.rank === 1) || scored.sort((a, b) => b.score_overall - a.score_overall)[0],
      missing: rows.filter((r) => r.data_coverage < 21).length,
      spread: values.length > 1 ? values[0] - values[values.length - 1] : null,
    };
  }

  function deltaCell(a, b, key, digits = 2) {
    if (!a || !b) return `<td class="right faint">${DASH}</td>`;
    const va = a[key];
    const vb = b[key];
    if (va === null || va === undefined || vb === null || vb === undefined) return `<td class="right faint">${DASH}</td>`;
    const d = va - vb;
    const tone = Math.abs(d) < 0.005 ? "var(--faint)" : d > 0 ? "var(--good)" : "var(--low)";
    return `<td class="right"><span class="mono" style="color:${tone}">${d > 0 ? "+" : ""}${d.toFixed(digits)}</span></td>`;
  }

  function renderPanel() {
    const host = $("#panelBody");
    const run = state.runs.find((r) => r.run_id === state.current);
    if (!run) {
      host.innerHTML = stateBlock({ icon: "info", title: t("hs.pick"), body: t("hs.pick.body") });
      return;
    }

    const s = statsFor(state.rows);
    const compareRun = state.runs.find((r) => r.run_id === state.compareId);
    const byTicker = new Map((state.compareRows || []).map((r) => [r.ticker, r]));

    const trendOptions = state.rows
      .slice()
      .sort((a, b) => (b.score_overall ?? -1) - (a.score_overall ?? -1))
      .map((r) => `<option value="${escapeHtml(r.ticker)}"${r.ticker === state.trendTicker ? " selected" : ""}>${escapeHtml(r.ticker)}</option>`)
      .join("");

    host.innerHTML = `
      <div class="head">
        <div>
          <h3>${escapeHtml(run.run_id)}</h3>
          <div class="when">${escapeHtml(when(run.started_at))} → ${escapeHtml(when(run.finished_at))} · ${t("common.trigger")} ${escapeHtml(run.trigger)} · ${ago(run.started_at)}</div>
        </div>
        <div class="row" style="gap:8px">
          <a class="btn btn--quiet btn--sm" href="ranking.html?run_id=${encodeURIComponent(run.run_id)}" id="openRanking">${icon("table")}<span>${t("hs.openRanking")}</span></a>
          <a class="btn btn--quiet btn--sm" href="/api/export/csv?run_id=${encodeURIComponent(run.run_id)}">${icon("download")}<span>CSV</span></a>
          <a class="btn btn--quiet btn--sm" href="/api/export/xlsx?run_id=${encodeURIComponent(run.run_id)}">${icon("download")}<span>Excel</span></a>
          <button class="btn btn--ghost btn--sm btn--danger" type="button" id="dropRun">${icon("x")}<span>${t("common.delete")}</span></button>
        </div>
      </div>

      <div class="grid grid--stats">
        <div class="statcard glass--quiet"><div class="label">${t("hs.stat.rows")}</div><div class="value">${s.rows}</div>
          <div class="note">${t("hs.stat.rows.note", { submitted: escapeHtml(run.tickers || "").split(",").length, failed: run.error_count })}</div></div>
        <div class="statcard glass--quiet"><div class="label">${t("hs.stat.eligible")}</div><div class="value">${s.eligible}</div>
          <div class="note">${t("hs.stat.eligible.note", { n: s.rows - s.eligible })}</div></div>
        <div class="statcard glass--quiet"><div class="label">${t("hs.stat.avg")}</div><div class="value">${num(s.avg, 2)}</div>
          <div class="note">${t("hs.stat.avg.note", { spread: s.spread === null ? DASH : num(s.spread, 2) })}</div></div>
        <div class="statcard glass--quiet"><div class="label">${t("hs.stat.top")}</div><div class="value">${s.top ? escapeHtml(s.top.ticker) : DASH}</div>
          <div class="note">${s.top ? `${num(s.top.score_overall, 2)} / 10 · #${s.top.rank ?? DASH}` : DASH}</div></div>
      </div>

      <div class="section" style="gap:10px">
        <div class="section-head">
          <div>
            <h2 style="font-size:14px">${t("hs.trend")}</h2>
            <p class="hint">${t("hs.trend.hint")}</p>
          </div>
          <div class="tools">
            <select class="input" id="trendTicker" style="min-width:110px">${trendOptions}</select>
            <select class="input" id="compareSelect" style="min-width:180px">
              <option value="">${t("hs.compare.none")}</option>
              ${state.runs.filter((r) => r.run_id !== run.run_id).slice(0, 24).map((r) =>
                `<option value="${escapeHtml(r.run_id)}"${r.run_id === state.compareId ? " selected" : ""}>${t("hs.compare.option", { when: escapeHtml(when(r.started_at)) })}</option>`).join("")}
            </select>
          </div>
        </div>
        <div class="glass glass--quiet" style="padding:14px 16px 8px">${sparkline(state.trend, state.compareRows && state.compareRows.length ? state.compareRows : null)}</div>
        ${compareRun ? `<div class="row" style="font-size:11.5px;color:var(--muted)">
          <span class="badge badge--accent">${t("hs.badge.current", { when: escapeHtml(when(run.started_at)) })}</span>
          <span class="badge">${t("hs.badge.compare", { when: escapeHtml(when(compareRun.started_at)) })}</span></div>` : ""}
      </div>

      <div class="section" style="gap:10px">
        <div class="section-head">
          <div>
            <h2 style="font-size:14px">${t("hs.table")}</h2>
            <p class="hint">${s.missing ? t("hs.table.hint.some", { n: s.missing }) : t("hs.table.hint.all")}${
              compareRun ? t("hs.table.hint.compare") : ""}</p>
          </div>
        </div>
        <div class="tablewrap" style="max-height:min(56dvh,560px)">
          <table class="data">
            <thead>
              <tr>
                <th>#</th><th>${t("common.ticker")}</th><th>${t("common.company")}</th>
                ${FACETS.map(([key]) => `<th class="right">${t(key)}</th>`).join("")}
                <th class="right">${t("common.overall")}</th><th class="right">${t("common.coverage")}</th>
                ${compareRun ? `<th class="right">Δ ${t("common.overall")}</th>` : ""}
              </tr>
            </thead>
            <tbody>
              ${state.rows.length ? state.rows.map((r) => `
                <tr class="rowlink" data-row="${escapeHtml(r.ticker)}">
                  <td class="rank-cell num${r.rank && r.rank <= 3 ? " top" : ""}">${r.rank ?? DASH}</td>
                  <td class="ticker"><a class="tick-link" href="company.html?ticker=${encodeURIComponent(r.ticker)}&run_id=${encodeURIComponent(run.run_id)}">${escapeHtml(r.ticker)}</a></td>
                  <td class="company" title="${escapeHtml(r.company || "")}">${escapeHtml(r.company || DASH)}</td>
                  ${FACETS.map(([, k]) => `<td class="right dimcell">${FR.meter(r[k])}</td>`).join("")}
                  <td class="right dimcell">${FR.meter(r.score_overall)}</td>
                  <td class="right"><span class="mono">${r.data_coverage}/21</span></td>
                  ${compareRun ? deltaCell(r, byTicker.get(r.ticker), "score_overall") : ""}
                </tr>`).join("")
                : `<tr><td colspan="${8 + (compareRun ? 1 : 0)}"><div class="state"><p>${t("hs.table.empty")}</p></div></td></tr>`}
            </tbody>
          </table>
        </div>
      </div>`;

    $$("#panelBody tr[data-row]").forEach((tr) =>
      tr.addEventListener("click", (event) => {
        if (event.target.closest("a, button")) return;
        location.href = `company.html?ticker=${encodeURIComponent(tr.dataset.row)}&run_id=${encodeURIComponent(run.run_id)}`;
      }));

    $("#trendTicker").addEventListener("change", async (e) => {
      state.trendTicker = e.target.value;
      await loadTrend();
      renderPanel();
    });
    $("#compareSelect").addEventListener("change", async (e) => {
      state.compareId = e.target.value;
      if (!state.compareId) {
        state.compareRows = null;
        renderPanel();
        return;
      }
      try {
        const data = await api.run(state.compareId);
        state.compareRows = data.rows || [];
        // align trend points to the compared snapshot length
        renderPanel();
      } catch (err) {
        toast(t("hs.compare.fail", { msg: err.message }), "err");
        state.compareRows = null;
        renderPanel();
      }
    });
    $("#dropRun").addEventListener("click", async () => {
      if (!confirm(t("hs.delete.confirm", { id: run.run_id }))) return;
      try {
        await api.dropRun(run.run_id);
        toast(t("hs.delete.ok"));
        state.current = null;
        state.rows = [];
        await loadRuns();
      } catch (err) {
        toast(t("hs.delete.fail", { msg: err.message }), "err");
      }
    });

    animateSpark();
  }

  /* ------------------------------------------------------------------- load */
  async function loadTrend() {
    if (!state.trendTicker) { state.trend = []; return; }
    try {
      const data = await api.history(state.trendTicker, 60);
      const points = (data.history || [])
        .filter((p) => p.score_overall !== null && p.score_overall !== undefined)
        .sort((a, b) => String(a.captured_at).localeCompare(String(b.captured_at)));
      state.trend = points;
    } catch {
      state.trend = [];
    }
  }

  async function selectRun(runId) {
    state.current = runId;
    renderTimeline();
    try {
      const data = await api.run(runId);
      state.rows = data.rows || [];
    } catch (err) {
      state.rows = [];
      toast(t("hs.load.fail", { msg: err.message }), "err");
    }
    if (!state.rows.some((r) => r.ticker === state.trendTicker)) {
      const first = state.rows.slice().sort((a, b) => (b.score_overall ?? -1) - (a.score_overall ?? -1))[0];
      state.trendTicker = first ? first.ticker : "";
      await loadTrend();
    }
    renderPanel();
  }

  async function loadRuns() {
    try {
      const data = await api.runs(60);
      state.runs = data.runs || [];
    } catch (err) {
      state.runs = [];
      toast(t("hs.list.fail", { msg: err.message }), "err");
    }
    renderTimeline();
    if (!state.runs.length) {
      $("#panelBody").innerHTML = stateBlock({
        icon: "clock", title: t("hs.none.title"), body: t("hs.none.body"),
        action: { href: "refresh.html", label: t("action.go.refresh") },
      });
      return;
    }
    const target = state.runs.find((r) => r.run_id === state.current) || state.runs[0];
    await selectRun(target.run_id);
  }

  window.pageInit = async function pageInit() {
    $("#reloadRuns").innerHTML = `${icon("refresh")}<span data-i18n="hs.reload">${t("hs.reload")}</span>`;
    $("#newRun").innerHTML = `${solid("play")}<span data-i18n="hs.new">${t("hs.new")}</span>`;
    $("#reloadRuns").addEventListener("click", loadRuns);

    document.addEventListener("fr:lang", () => {
      $("#reloadRuns").innerHTML = `${icon("refresh")}<span>${t("hs.reload")}</span>`;
      $("#newRun").innerHTML = `${solid("play")}<span>${t("hs.new")}</span>`;
      renderTimeline();
      renderPanel();
    });

    await loadRuns();
  };
})();
