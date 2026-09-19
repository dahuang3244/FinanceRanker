/* ==========================================================================
   Sector mode for the refresh page.

   Adds a second way to build a universe next to the hand-picked pool:

     pool   — the original behaviour, unchanged: type tickers, rank those.
     sector — pick a module (科技/金融/医药食品/媒体/汽车能源/制造零售) or type
              one ticker; the module is resolved from that ticker's SEC SIC code
              and its peers are ranked against each other for the top 10.

   Kept in its own file on purpose: refresh.js owns the job runner and stays
   untouched, so the original path cannot regress when this one changes.
   ========================================================================== */
"use strict";

(() => {
  // NOTE: `t` is the global shorthand defined by js/i18n.js (window.t), not an
  // FR export — destructuring it from FR would yield undefined.
  const { api, $, escapeHtml, toast, DASH } = FR;

  const state = {
    mode: "pool",
    sectors: [],
    sector: null,        // {key,label,label_en,seeded,indexed}
    focus: "",           // the ticker the user typed, if any
    resolved: null,      // resolve result for the focus ticker
    ranking: null,
    busy: false,
    presetTickers: "",   // filled when the user picks a ranking row's universe
  };

  /* ------------------------------------------------------------ helpers */
  const pct = (v, d = 1) =>
    v === null || v === undefined ? DASH : (Number(v) * 100).toFixed(d) + "%";
  const mult = (v) => (v === null || v === undefined ? DASH : Number(v).toFixed(2) + "x");
  const money = (v) => {
    if (v === null || v === undefined) return DASH;
    const a = Math.abs(v);
    if (a >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
    if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
    return `$${v.toFixed(2)}`;
  };
  const scoreTag = (v) => {
    if (v === null || v === undefined) return `<span class="badge">${DASH}</span>`;
    const dir = v >= 6.5 ? "good" : v >= 5 ? "mid" : "low";
    return `<span class="badge badge--${dir}">${Number(v).toFixed(2)}</span>`;
  };

  /** Sector names arrive as [zh, en]; pick the active language. */
  function sectorLabelOf(pair) {
    if (!pair) return "";
    if (Array.isArray(pair)) return FRI18n.current() === "en" ? (pair[1] || pair[0]) : pair[0];
    return pair;
  }

  function sectorName(s) {
    return FRI18n.current() === "en" ? (s.label_en || s.label) : s.label;
  }

  /* ------------------------------------------------------- markup */
  function panelHtml() {
    const options = state.sectors
      .map(
        (s) =>
          `<option value="${s.key}"${state.sector && state.sector.key === s.key ? " selected" : ""}>` +
          `${escapeHtml(sectorName(s))} (${s.seeded})</option>`
      )
      .join("");

    return `
      <section class="section">
        <div class="section-head">
          <div>
            <h2 data-i18n="rf.sector">板块排名</h2>
            <p class="hint" data-i18n="rf.sector.hint">
              选一个板块，或直接输入一只股票——会按其 SEC 行业分类自动归入板块，再与同板块公司一起打分，取前十名。
            </p>
          </div>
        </div>

        <div class="glass console">
          <div class="field">
            <label data-i18n="rf.sector.pick">板块</label>
            <select class="input" id="sectorSelect">${options}</select>
            <div class="help" id="sectorMeta"></div>
          </div>

          <div class="field">
            <label data-i18n="rf.sector.ticker">按个股定位（可选）</label>
            <div class="row" style="gap:10px">
              <input class="input" id="sectorTicker" placeholder="例如 NVDA / 台积电 / JPM"
                     autocomplete="off" spellcheck="false" style="flex:1" />
              <button class="btn btn--quiet" type="button" id="sectorResolve"
                      data-i18n="rf.sector.locate">定位板块</button>
            </div>
            <div class="help" id="sectorResolved"></div>
          </div>

          <hr class="rule" />

          <div class="row">
            <button class="btn btn--primary" type="button" id="sectorRun"
                    data-i18n="rf.sector.run">按板块排名（前十）</button>
            <span class="spacer"></span>
            <label class="check">
              <input type="checkbox" id="sectorUsePool" />
              <span data-i18n="rf.sector.asPool">把结果前 10 名填入自定义股票池</span>
            </label>
          </div>

          <div class="alert hidden" id="sectorAlert" role="alert"></div>
        </div>
      </section>

      <section class="section" id="sectorResultSection" hidden>
        <div class="section-head">
          <div>
            <h2 data-i18n="rf.sector.result">板块内排名</h2>
            <p class="hint" id="sectorResultHint"></p>
          </div>
          <div class="tools">
            <span class="badge" id="sectorIndexBadge"></span>
          </div>
        </div>
        <div class="glass tablewrap tablewrap--flat" style="border:0;background:transparent;box-shadow:none">
          <table class="data">
            <thead>
              <tr>
                <th>#</th><th data-i18n="col.ticker">代码</th><th data-i18n="col.company">公司</th>
                <th class="right" data-i18n="col.score">总分</th>
                <th class="right" data-i18n="dim.growth">成长</th>
                <th class="right" data-i18n="dim.profitability">盈利</th>
                <th class="right" data-i18n="dim.cash">现金</th>
                <th class="right" data-i18n="dim.valuation">估值</th>
                <th class="right" data-i18n="dim.market">市场</th>
                <th class="right" data-i18n="col.ps">P/S</th>
              </tr>
            </thead>
            <tbody id="sectorBody"></tbody>
          </table>
        </div>
      </section>`;
  }

  /* ------------------------------------------------------- rendering */
  function renderShell() {
    $("#sectorMode").innerHTML = panelHtml();
    wirePanel();
    renderSectorMeta();
    renderResolved();
  }

  function renderSectorMeta() {
    const el = $("#sectorMeta");
    if (!el || !state.sector) return;
    el.textContent = t("rf.sector.meta")
      .replace("{n}", String(state.sector.seeded))
      .replace("{m}", String(state.sector.indexed || 0));
  }

  function renderResolved() {
    const el = $("#sectorResolved");
    if (!el) return;
    const r = state.resolved;
    if (!r) { el.textContent = ""; return; }
    el.innerHTML =
      `${escapeHtml(r.company || r.ticker)} · SIC <span class="mono">${escapeHtml(String(r.sic ?? "—"))}</span>` +
      ` ${escapeHtml(r.sic_description || "")} → <b>${escapeHtml(sectorLabelOf(r.sector_label) || t("rf.sector.unclassified"))}</b>`;
  }

  function renderAlert(message, kind = "error") {
    const el = $("#sectorAlert");
    if (!el) return;
    if (!message) { el.classList.add("hidden"); return; }
    el.className = "alert" + (kind === "warn" ? " alert--warn" : "");
    el.textContent = message;
  }

  function renderRanking() {
    const data = state.ranking;
    const section = $("#sectorResultSection");
    if (!data || !data.rows || !data.rows.length) {
      if (section) section.hidden = true;
      return;
    }
    section.hidden = false;
    $("#sectorResultHint").textContent = t("rf.sector.resultHint")
      .replace("{label}", data.sector_label ? sectorLabelOf(data.sector_label) : "")
      .replace("{pool}", String(data.pool_size))
      .replace("{n}", String(data.rows.length));
    $("#sectorIndexBadge").textContent = t("rf.sector.indexed", { n: data.index_size });

    $("#sectorBody").innerHTML = data.rows
      .map((r, i) => {
        const isFocus = state.focus && r.ticker === state.focus;
        return `<tr${isFocus ? ' style="outline:2px solid var(--accent-line);outline-offset:-2px"' : ""}>
          <td class="mono">${i + 1}</td>
          <td><a class="tick-link" href="company.html?ticker=${encodeURIComponent(r.ticker)}">${escapeHtml(r.ticker)}</a></td>
          <td>${escapeHtml(r.company || DASH)}</td>
          <td class="right">${scoreTag(r.score_overall)}</td>
          <td class="right">${scoreTag(r.score_growth)}</td>
          <td class="right">${scoreTag(r.score_profitability)}</td>
          <td class="right">${scoreTag(r.score_cash)}</td>
          <td class="right">${scoreTag(r.score_valuation)}</td>
          <td class="right">${scoreTag(r.score_market)}</td>
          <td class="right">${mult(r.price_to_sales)}</td>
        </tr>`;
      })
      .join("");
  }

  /* ------------------------------------------------------------ actions */
  async function loadSectors() {
    try {
      const d = await api.sectors();
      state.sectors = d.sectors || [];
      if (state.sectors.length) {
        state.sector = state.sectors[0];
      }
    } catch {
      state.sectors = [];
    }
  }

  /** Repaint the sector controls after a language flip. */
  function relabelSectors() {
    const sel = $("#sectorSelect");
    if (sel && state.sectors.length) {
      const current = sel.value;
      sel.innerHTML = state.sectors
        .map((sc) => `<option value="${sc.key}">${escapeHtml(sectorName(sc))} (${sc.seeded})</option>`)
        .join("");
      sel.value = current;
    }
    renderSectorMeta();
    renderResolved();
    if (state.ranking) renderRanking();
  }

  async function resolveFocus() {
    const input = $("#sectorTicker");
    const symbol = (input.value || "").trim().split(/\s+/)[0].toUpperCase();
    if (!symbol) { toast(t("rf.sector.needTicker"), "warn"); return; }
    renderAlert(null);
    try {
      const r = await api.resolveSector(symbol);
      state.resolved = r;
      state.focus = r.ticker;
      if (r.sector) {
        const match = state.sectors.find((s) => s.key === r.sector);
        if (match) {
          state.sector = match;
          $("#sectorSelect").value = match.key;
        }
      }
      // The index may have grown from this lookup; refresh the counts.
      await loadSectors();
      renderSectorMeta();
      renderResolved();
    } catch (err) {
      state.resolved = null;
      renderResolved();
      renderAlert(err.message || String(err), "warn");
    }
  }

  async function runSector() {
    if (state.busy || !state.sector) return;
    state.busy = true;
    $("#sectorRun").disabled = true;
    renderAlert(null);
    $("#sectorAlert").className = "alert";
    $("#sectorAlert").textContent = t("rf.sector.running");
    $("#sectorAlert").classList.remove("hidden");

    try {
      const data = await api.sectorRanking(state.sector.key, {
        pool_size: 40,
        top: 10,
        focus: state.focus || undefined,
      });
      state.ranking = data;
      renderRanking();
      renderAlert(null);
      if (data.errors && Object.keys(data.errors).length) {
        renderAlert(
          t("rf.sector.partial").replace("{n}", String(Object.keys(data.errors).length)),
          "warn"
        );
      }
      if ($("#sectorUsePool") && $("#sectorUsePool").checked) {
        const tickers = data.rows.map((r) => r.ticker);
        if (typeof window.frSetPool === "function") {
          window.frSetPool(tickers);
          toast(t("rf.sector.filled").replace("{n}", String(tickers.length)));
        }
      }
    } catch (err) {
      renderAlert(err.message || String(err));
    } finally {
      state.busy = false;
      $("#sectorRun").disabled = false;
    }
  }

  /* ------------------------------------------------------------- wiring */
  function wirePanel() {
    $("#sectorSelect").addEventListener("change", (e) => {
      state.sector = state.sectors.find((s) => s.key === e.target.value) || null;
      renderSectorMeta();
    });
    $("#sectorResolve").addEventListener("click", resolveFocus);
    $("#sectorTicker").addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); resolveFocus(); }
    });
    $("#sectorRun").addEventListener("click", runSector);
  }

  function setMode(mode) {
    state.mode = mode;
    const pool = $("#poolMode");
    const sector = $("#sectorMode");
    if (pool) pool.hidden = mode !== "pool";
    if (sector) sector.hidden = mode !== "sector";
    document.querySelectorAll("#modeSeg [data-mode]").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.mode === mode))
    );
    if (mode === "sector" && !state.sectors.length) {
      loadSectors().then(renderShell);
    } else {
      renderSectorMeta();
    }
  }

  async function init() {
    const host = $("#sectorMode");
    if (!host) return;

    await loadSectors();
    renderShell();

    document.querySelectorAll("#modeSeg [data-mode]").forEach((b) =>
      b.addEventListener("click", () => setMode(b.dataset.mode))
    );

    // A ?sector=key or ?ticker=SYM deep link opens sector mode directly.
    const qs = new URLSearchParams(location.search);
    const wantSector = qs.get("sector");
    const wantTicker = qs.get("ticker");
    if (wantSector || wantTicker) {
      setMode("sector");
      if (wantSector) {
        const match = state.sectors.find((s) => s.key === wantSector);
        if (match) { state.sector = match; $("#sectorSelect").value = match.key; renderSectorMeta(); }
      }
      if (wantTicker) {
        $("#sectorTicker").value = wantTicker.toUpperCase();
        await resolveFocus();
      }
      if (wantSector || state.resolved) await runSector();
    }
  }

  document.addEventListener("fr:lang", relabelSectors);

  window.pageInitSector = init;
})();
