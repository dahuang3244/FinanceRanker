/* Refresh console — pool editor, SSE progress, job list and artifacts. */
"use strict";

(() => {
  const {
    api, icon, solid, $, el, escapeHtml, toast,
    num, pct, when, ago, DASH,
  } = FR;

  const PRESETS = [
    ["rf.preset.default", "MSFT,AAPL,GOOGL,AMZN,META,NVDA,TSM,AMD,QCOM,AVGO,AMAT,MU,ORCL"],
    ["rf.preset.semis", "NVDA,AMD,AVGO,QCOM,MU,AMAT,TSM,INTC,ARM,ASML"],
    ["rf.preset.mega", "AAPL,MSFT,GOOGL,AMZN,META,NVDA,TSLA,AVGO"],
    ["rf.preset.software", "MSFT,ORCL,CRM,ADBE,NOW,SNOW,PANW,INTU"],
    ["rf.preset.internet", "GOOGL,META,AMZN,NFLX,UBER,ABNB,SPOT,PINS"],
  ];

  const STEPS = ["rf.step.pool", "rf.step.fetch", "rf.step.metrics", "rf.step.score"];

  const state = {
    tickers: [],
    jobId: null,
    source: null,
    poller: null,
    busy: false,
    startedAt: null,
    total: 0,
    done: 0,
    finished: false,
  };

  /* ------------------------------------------------------------ pool editor */
  function parse(text) {
    return String(text || "")
      .split(/[\s,;、，]+/)
      .map((t) => t.trim().toUpperCase().replace(/[^A-Z0-9.\-]/g, ""))
      .filter(Boolean);
  }

  function unique(list) {
    const seen = new Set();
    const out = [];
    for (const t of list) {
      if (seen.has(t)) continue;
      seen.add(t);
      out.push(t);
    }
    return out;
  }

  function syncInput() {
    $("#poolInput").value = state.tickers.join(", ");
    const rows = Math.min(8, Math.max(3, Math.ceil(state.tickers.join(", ").length / 58)));
    $("#poolInput").rows = state.tickers.length ? rows : 5;
  }

  function renderBag() {
    const host = $("#poolBag");
    const duplicates = new Set();
    const seen = new Set();
    parse($("#poolInput").value).forEach((t) => {
      if (seen.has(t)) duplicates.add(t);
      seen.add(t);
    });

    if (!state.tickers.length) {
      host.innerHTML = `<span class="faint" style="font-size:12px">${t("rf.pool.empty")}</span>`;
    } else {
      // note: the loop variable is `code`, not `t` — `t` is the translator
      host.innerHTML = state.tickers.map((code, i) => `
        <span class="tag${duplicates.has(code) ? " dup" : ""}" style="animation-delay:${Math.min(i * 18, 240)}ms">
          ${escapeHtml(code)}
          <button type="button" data-drop="${escapeHtml(code)}"
            aria-label="${escapeHtml(t("common.remove", { name: code }))}">×</button>
        </span>`).join("");
    }

    $("#poolCount").textContent = String(state.tickers.length);
    const over = state.tickers.length > 100;
    $("#poolHelp").innerHTML = over
      ? `<span style="color:var(--accent-deep)">${t("rf.pool.tooMany")}</span>`
      : t("rf.pool.parsed", {
        n: state.tickers.length,
        dup: duplicates.size ? t("rf.pool.dup", { n: duplicates.size }) : "",
      });
    $("#runBtn").disabled = state.busy || !state.tickers.length || over;

    host.querySelectorAll("[data-drop]").forEach((b) =>
      b.addEventListener("click", () => {
        state.tickers = state.tickers.filter((t) => t !== b.dataset.drop);
        syncInput();
        renderBag();
      }));
  }

  function setPool(list, { silent = false } = {}) {
    state.tickers = unique(list);
    syncInput();
    renderBag();
    if (!silent) toast(t("rf.toast.pool", { n: state.tickers.length }));
  }
  // Sector mode reuses the pool editor to stage its top 10 as a custom pool.
  window.frSetPool = (list) => setPool(list);

  /* ------------------------------------------------------------ run + stream */
  function setStreamBadge(kind, label, i18nKey) {
    const node = $("#streamBadge");
    node.className = "badge" + (kind === "live" ? " badge--accent" : kind === "warn" ? " badge--mid" : "");
    const text = i18nKey ? `<span data-i18n="${i18nKey}">${escapeHtml(label)}</span>` : escapeHtml(label);
    node.innerHTML = `<span class="dot"></span>${text}`;
  }

  function renderSteps(activeIndex, markAllDone = false) {
    $("#steps").innerHTML = STEPS.map((labelKey, i) => {
      const cls = markAllDone || i < activeIndex ? "done" : i === activeIndex ? "active" : "";
      const mark = markAllDone || i < activeIndex ? icon("check") : "";
      return `<div class="step ${cls}"><span class="mark">${mark}</span><span>${t(labelKey)}</span>
        ${i === activeIndex && state.total ? `<span class="at">${state.done}/${state.total}</span>` : ""}</div>`;
    }).join("");
  }

  function setProgress(ratio, stage) {
    const clamped = Math.max(0, Math.min(1, ratio || 0));
    $("#progress").classList.toggle("is-live", state.busy);
    $("#progressFill").style.transform = `transform: scaleX(${clamped.toFixed(4)})`;
    $("#pctText").textContent = `${Math.round(clamped * 100)}%`;
    if (stage) $("#stageText").textContent = stage;

    // ETA from elapsed time and completed units
    if (state.busy && state.startedAt && state.done > 0 && state.done < state.total) {
      const elapsed = (Date.now() - state.startedAt) / 1000;
      const remain = (elapsed / state.done) * (state.total - state.done);
      $("#etaText").textContent = remain < 60
        ? t("rf.eta.seconds", { n: Math.round(remain) })
        : t("rf.eta.minutes", { n: num(remain / 60, 1) });
    } else if (state.busy) {
      $("#etaText").textContent = t("rf.eta.estimating");
    } else {
      $("#etaText").textContent = DASH;
    }
  }

  function logLine(text, kind = "") {
    const host = $("#log");
    const now = new Date();
    const p = (x) => String(x).padStart(2, "0");
    const time = `${p(now.getHours())}:${p(now.getMinutes())}:${p(now.getSeconds())}`;
    host.insertAdjacentHTML("beforeend",
      `<div class="ln ${kind}"><time>${time}</time><span>${escapeHtml(text)}</span></div>`);
    while (host.children.length > 220) host.firstElementChild.remove();
    host.scrollTop = host.scrollHeight;
  }

  function clearLog() {
    $("#log").innerHTML = `<div class="ln"><time>--:--:--</time><span class="faint" data-i18n="rf.log.cleared">${t("rf.log.cleared")}</span></div>`;
  }

  function showRunAlert(message, kind = "error") {
    const node = $("#runAlert");
    if (!message) { node.hidden = true; return; }
    node.className = `alert${kind === "warn" ? " alert--warn" : ""}`;
    node.innerHTML = `${icon(kind === "warn" ? "info" : "alert")}<span>${escapeHtml(message)}</span>`;
    node.hidden = false;
  }

  async function start() {
    if (!state.tickers.length) return;
    state.busy = true;
    state.finished = false;
    state.done = 0;
    state.total = state.tickers.length;
    state.startedAt = Date.now();
    showRunAlert("");
    $("#runBtn").disabled = true;
    $("#errorList").innerHTML = "";
    ["#exportCsv", "#exportXlsx", "#viewResult"].forEach((s) => { $(s).disabled = true; });
    $("#artRows").textContent = $("#artEligible").textContent = $("#artErrors").textContent = DASH;
    $("#artifactHint").textContent = t("rf.artifacts.running");
    renderSteps(0);
    setProgress(0.02, t("rf.stage.submit", { n: state.tickers.length }));
    setStreamBadge("live", t("rf.badge.running"));
    logLine(t("rf.log.submitted", { list: state.tickers.join(", ") }));

    try {
      const data = await api.post("/api/refresh", { tickers: state.tickers });
      state.jobId = data.job_id;
      logLine(t("rf.log.accepted", { id: data.job_id }), "ok");
      renderSteps(1);
      stream(data.job_id);
      loadJobs();
    } catch (err) {
      state.busy = false;
      setStreamBadge("warn", t("rf.badge.failed"));
      setProgress(0, err.message || t("rf.toast.submitFailed"));
      showRunAlert(err.message || t("rf.toast.submitFailed"));
      logLine(err.message || t("rf.toast.submitFailed"), "err");
      renderBag();
    }
  }

  function closeStream() {
    if (state.source) { state.source.close(); state.source = null; }
    if (state.poller) { clearInterval(state.poller); state.poller = null; }
  }

  function stream(jobId) {
    closeStream();
    const source = new EventSource(`/api/jobs/${jobId}/stream`);
    state.source = source;

    source.onmessage = (event) => {
      let msg = null;
      try { msg = JSON.parse(event.data); } catch { return; }
      handle(msg);
      if (msg.kind === "close" || msg.kind === "done" || msg.kind === "error") {
        source.close();
        state.source = null;
        finish(msg);
      }
    };

    source.onerror = () => {
      source.close();
      state.source = null;
      if (state.finished) return;
      setStreamBadge("warn", t("rf.badge.polling"));
      logLine(t("rf.log.sseLost"), "warn");
      poll(jobId);
    };
  }

  function poll(jobId) {
    if (state.poller) clearInterval(state.poller);
    state.poller = setInterval(async () => {
      try {
        const snap = await api.job(jobId);
        state.done = Math.round((snap.progress || 0) * (snap.total || state.total));
        state.total = snap.total || state.total;
        setProgress(snap.progress || 0, snap.message || snap.status);
        if (snap.status === "done" || snap.status === "failed") {
          clearInterval(state.poller);
          state.poller = null;
          finish({ kind: snap.status === "done" ? "close" : "error", ...snap });
        }
      } catch (err) {
        clearInterval(state.poller);
        state.poller = null;
        state.busy = false;
        renderBag();
        logLine(t("rf.log.pollFailed", { msg: err.message }), "err");
      }
    }, 1600);
  }

  function handle(msg) {
    if (msg.kind === "state" || msg.kind === "close") {
      state.total = msg.total || state.total;
      state.done = Math.round((msg.progress || 0) * state.total);
      if (state.done) setProgress(msg.progress || 0, msg.message || msg.status);
      return;
    }
    if (msg.kind === "start") {
      state.total = msg.total || state.total;
      logLine(t("rf.log.start", { n: state.total }), "ok");
      setProgress(0.03, t("rf.stage.fetching", { n: state.total }));
      renderSteps(1);
      return;
    }
    if (msg.kind === "progress") {
      state.done = msg.done || state.done;
      state.total = msg.total || state.total;
      setProgress(state.total ? state.done / state.total : 0, `${msg.ticker} · ${msg.status}`);
      const kind = /fail|error|skip|不足/i.test(msg.status || "") ? "warn" : "ok";
      logLine(t("rf.log.step", { ticker: msg.ticker, status: msg.status }), kind);
      renderSteps(1);
      return;
    }
    if (msg.kind === "warning") {
      showRunAlert(msg.message, "warn");
      logLine(msg.message, "warn");
      return;
    }
  }

  async function finish(msg) {
    state.busy = false;
    state.finished = true;
    closeStream();
    renderBag();

    if (msg.kind === "error") {
      setStreamBadge("warn", t("rf.badge.failed"));
      setProgress(1, msg.message || t("rf.toast.submitFailed"));
      showRunAlert(msg.message || t("rf.toast.submitFailed"));
      logLine(msg.message || t("rf.toast.submitFailed"), "err");
      renderSteps(0);
      return;
    }

    setProgress(1, t("rf.stage.reading"));
    renderSteps(STEPS.length, true);
    logLine(t("rf.log.finished"), "ok");
    setStreamBadge("live", t("rf.badge.done"));

    try {
      const rank = await api.ranking({ job_id: state.jobId });
      const rows = rank.rows || [];
      const eligible = rows.filter((r) => r.rank_eligible);
      const errors = Object.entries(rank.errors || {});
      $("#artRows").textContent = rows.length;
      $("#artEligible").textContent = eligible.length;
      $("#artErrors").textContent = errors.length;
      const top = rows.slice().sort((a, b) => (b.score_overall ?? -1) - (a.score_overall ?? -1))[0];
      $("#artifactHint").innerHTML = top
        ? t("rf.art.top", { ticker: escapeHtml(top.ticker), score: num(top.score_overall, 2) })
        : t("rf.art.none");

      if (errors.length) {
        $("#errorList").innerHTML = `<div class="alert"><span></span><span>${errors
          .map(([t, e]) => `<b>${escapeHtml(t)}</b> ${escapeHtml(String(e))}`).join("<br />")}</span></div>`;
        errors.forEach(([ticker, e]) => logLine(t("rf.log.failedOne", { ticker, msg: e }), "err"));
      } else {
        $("#errorList").innerHTML = "";
      }

      ["#exportCsv", "#exportXlsx", "#viewResult"].forEach((s) => { $(s).disabled = !rows.length; });
      logLine(t("rf.log.ingested", { n: rows.length, m: eligible.length }), "ok");
      toast(t("rf.toast.done", { n: rows.length }));
      loadJobs();

      if ($("#openAfter").checked && rows.length) {
        setTimeout(() => { location.href = `ranking.html?job_id=${encodeURIComponent(state.jobId)}`; }, 900);
      }
    } catch (err) {
      $("#artifactHint").textContent = t("rf.log.readFailed", { msg: err.message });
      logLine(t("rf.log.readFailed", { msg: err.message }), "err");
    }
  }

  /* --------------------------------------------------------------- job list */
  const STATUS_TONE = { done: "badge--good", running: "badge--accent", queued: "", failed: "badge--low" };

  async function loadJobs() {
    const body = $("#jobsBody");
    try {
      const data = await api.jobs(20);
      const jobs = data.jobs || [];
      if (!jobs.length) {
        body.innerHTML = `<tr><td colspan="5" class="muted" style="padding:18px">${t("rf.jobs.none")}</td></tr>`;
        return;
      }
      body.innerHTML = jobs.map((job) => {
        const pctDone = Math.round((job.progress || 0) * 100);
        return `<tr>
          <td class="mono faint">${escapeHtml(String(job.job_id).slice(0, 18))}</td>
          <td><span class="badge ${STATUS_TONE[job.status] || ""}">${escapeHtml(job.status)}</span></td>
          <td class="right mono">${pctDone}%</td>
          <td class="company" title="${escapeHtml(job.message || "")}">${escapeHtml(job.message || DASH)}</td>
          <td class="right">${job.status === "running" || job.status === "queued"
            ? `<button class="btn btn--quiet btn--sm" type="button" data-watch="${escapeHtml(job.job_id)}">${t("rf.jobs.watch")}</button>`
            : `<span class="faint mono" style="font-size:11px">${ago(job.finished_at || job.started_at)}</span>`}</td>
        </tr>`;
      }).join("");
      body.querySelectorAll("[data-watch]").forEach((b) =>
        b.addEventListener("click", () => {
          state.jobId = b.dataset.watch;
          state.busy = true;
          state.finished = false;
          state.startedAt = Date.now();
          setStreamBadge("live", t("rf.badge.following"));
          logLine(t("rf.log.resume", { id: b.dataset.watch }));
          stream(b.dataset.watch);
          renderBag();
        }));
    } catch (err) {
      body.innerHTML = `<tr><td colspan="5" class="muted" style="padding:18px">${t("rf.jobs.loadFailed", { msg: escapeHtml(err.message || "") })}</td></tr>`;
    }
  }

  /* ------------------------------------------------------------------- wire */
  window.pageInit = async function pageInit() {
    $("#toRanking").innerHTML = `<span data-i18n="action.see.ranking">${t("action.see.ranking")}</span>${icon("chevron")}`;
    $("#poolDefault").innerHTML = `${icon("refresh")}<span data-i18n="rf.pool.default">${t("rf.pool.default")}</span>`;
    $("#poolClear").innerHTML = `${icon("x")}<span data-i18n="rf.pool.clear">${t("rf.pool.clear")}</span>`;
    $("#runBtn").innerHTML = `${solid("play")}<span data-i18n="action.start">${t("action.start")}</span>`;
    $("#jobsReload").innerHTML = `${icon("refresh")}<span data-i18n="action.refresh">${t("action.refresh")}</span>`;
    $("#logClear").innerHTML = `${icon("broom")}<span data-i18n="rf.log.clear">${t("rf.log.clear")}</span>`;
    $("#seeRanking").innerHTML = `<span data-i18n="rf.art.seeRanking">${t("rf.art.seeRanking")}</span>${icon("chevron")}`;
    $("#exportCsv").innerHTML = `${icon("download")}<span data-i18n="action.export.csv">${t("action.export.csv")}</span>`;
    $("#exportXlsx").innerHTML = `${icon("download")}<span data-i18n="action.export.xlsx">${t("action.export.xlsx")}</span>`;
    $("#viewResult").innerHTML = `${icon("table")}<span data-i18n="rf.art.viewResult">${t("rf.art.viewResult")}</span>`;

    clearLog();
    renderSteps(-1);
    setProgress(0, t("rf.stage.idle"));

    // presets
    $("#poolPresets").innerHTML = PRESETS
      .map(([labelKey, list]) => `<button class="chip" type="button" data-preset="${escapeHtml(list)}">${t(labelKey)}</button>`)
      .join("");
    $("#poolPresets").querySelectorAll("[data-preset]").forEach((b) =>
      b.addEventListener("click", () => setPool(parse(b.dataset.preset))));

    // pool input
    const onInput = FR.debounce(() => {
      state.tickers = unique(parse($("#poolInput").value));
      renderBag();
    }, 260);
    $("#poolInput").addEventListener("input", onInput);
    $("#poolInput").addEventListener("blur", () => {
      state.tickers = unique(parse($("#poolInput").value));
      syncInput();
      renderBag();
    });
    $("#poolClear").addEventListener("click", () => setPool([], { silent: true }));
    $("#jobsReload").addEventListener("click", loadJobs);
    $("#logClear").addEventListener("click", clearLog);
    $("#runBtn").addEventListener("click", start);
    $("#exportCsv").addEventListener("click", () => {
      location.href = `/api/export/csv?job_id=${encodeURIComponent(state.jobId)}`;
    });
    $("#exportXlsx").addEventListener("click", () => {
      location.href = `/api/export/xlsx?job_id=${encodeURIComponent(state.jobId)}`;
    });
    $("#viewResult").addEventListener("click", () => {
      location.href = `ranking.html?job_id=${encodeURIComponent(state.jobId)}`;
    });
    $("#seeRanking").addEventListener("click", () => {
      location.href = `ranking.html?job_id=${encodeURIComponent(state.jobId)}`;
    });

    // config + job history
    try {
      const config = await api.config();
      setPool(config.default_tickers || [], { silent: true });
      state.concurrency = config.max_concurrency;
      $("#concurrencyBadge").innerHTML = t("ov.cell.concurrency", { n: `<span class="mono">${config.max_concurrency}</span>` });
    } catch {
      setPool(parse(PRESETS[0][1]), { silent: true });
      $("#concurrencyBadge").textContent = t("health.offline");
    }

    // resume an in-flight job, else show the newest one
    document.addEventListener("fr:lang", () => {
      renderBag();
      renderSteps(state.busy ? 1 : state.finished ? STEPS.length : -1, state.finished);
      if (!state.busy && !state.finished) setProgress(0, t("rf.stage.idle"));
      if (state.concurrency) {
        $("#concurrencyBadge").innerHTML = t("ov.cell.concurrency", { n: `<span class="mono">${state.concurrency}</span>` });
      }
      $("#poolPresets").querySelectorAll("[data-preset]").forEach((b, i) => {
        b.textContent = t(PRESETS[i][0]);
      });
      loadJobs();
      if (state.jobId) $("#seeRanking").hidden = false;
    });

    try {
      const data = await api.jobs(20);
      const jobs = data.jobs || [];
      await loadJobs();
      const active = jobs.find((j) => j.status === "running" || j.status === "queued");
      const latest = active || jobs[0];
      if (latest && (active || latest.status === "done")) {
        state.jobId = latest.job_id;
        if (active) {
          state.busy = true;
          state.startedAt = new Date(latest.started_at || Date.now()).getTime();
          state.total = latest.total || 0;
          state.done = Math.round((latest.progress || 0) * state.total);
          setStreamBadge("live", t("rf.badge.following"));
          logLine(t("rf.log.resume", { id: latest.job_id }));
          stream(latest.job_id);
          renderBag();
        } else {
          setStreamBadge("", t("rf.badge.idle"), "rf.badge.idle");
          logLine(t("rf.log.lastJob", { id: latest.job_id, status: latest.status }), latest.status === "done" ? "ok" : "warn");
          if (latest.status === "done") {
            $("#artifactHint").textContent = t("rf.artifacts.recent");
            const rank = await api.ranking({ job_id: latest.job_id });
            const rows = rank.rows || [];
            const errors = Object.entries(rank.errors || {});
            $("#artRows").textContent = rows.length;
            $("#artEligible").textContent = rows.filter((r) => r.rank_eligible).length;
            $("#artErrors").textContent = errors.length;
            ["#exportCsv", "#exportXlsx", "#viewResult"].forEach((s) => { $(s).disabled = !rows.length; });
            $("#seeRanking").hidden = !rows.length;
            setProgress(1, t("rf.artifacts.loaded"));
            renderSteps(STEPS.length, true);
          }
        }
      } else {
        setStreamBadge("", t("rf.badge.idle"), "rf.badge.idle");
      }
    } catch (err) {
      logLine(t("rf.log.jobsFailed", { msg: err.message }), "err");
    }

    // Sector mode lives in js/sector.js so this runner stays untouched; it
    // mounts its own panel and wires the mode switch.
    if (typeof window.pageInitSector === "function") {
      try { await window.pageInitSector(); }
      catch (err) { logLine(`sector mode init failed: ${err.message}`, "warn"); }
    }
  };
})();
