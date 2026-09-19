/* Sources — provider chains, cache state, metric dictionary and API reference. */
"use strict";

(() => {
  const { api, icon, $, escapeHtml, toast, when, ago, num, DASH } = FR;

  const PROVIDERS = [
    { key: "fundamentals", icon: "shield", labelKey: "ov.kind.fundamentals" },
    { key: "prices", icon: "trend", labelKey: "ov.kind.prices" },
    { key: "quotes", icon: "zap", labelKey: "ov.kind.quotes" },
  ];

  const DICT = [
    {
      group: "dim.growth", weight: "25%",
      items: [
        ["revenue_growth_yoy"],
        ["revenue_cagr_5y"],
        ["eps_growth_fy1"],
        ["eps_cagr_5y"],
      ],
    },
    {
      group: "dim.profitability", weight: "25%",
      items: [
        ["gross_margin"],
        ["operating_margin"],
        ["net_margin"],
        ["roe"],
        ["roic"],
      ],
    },
    {
      group: "dim.cash", weight: "20%",
      items: [
        ["fcf_margin"],
        ["fcf_yield"],
        ["ocf_to_net_income"],
      ],
    },
    {
      group: "dim.valuation", weight: "20%",
      items: [
        ["forward_pe"],
        ["price_to_sales"],
        ["ev_to_ebitda"],
        ["price_to_book"],
        ["price_to_fcf"],
      ],
    },
    {
      group: "dim.market", weight: "10%",
      items: [
        ["return_1y"],
        ["drawdown_52w"],
        ["beta"],
        ["debt_to_assets"],
      ],
    },
  ];

  const ENDPOINTS = [
    ["GET", "/api/health", "sc.api.hint.health", "get"],
    ["GET", "/api/config", "sc.api.hint.config", "get"],
    ["POST", "/api/refresh", "sc.api.hint.refresh", "post"],
    ["GET", "/api/jobs", "sc.api.hint.jobs", "get"],
    ["GET", "/api/jobs/{id}", "sc.api.hint.job", "get"],
    ["GET", "/api/jobs/{id}/stream", "sc.api.hint.stream", "get"],
    ["GET", "/api/ranking", "sc.api.hint.ranking", "get"],
    ["GET", "/api/ticker/{ticker}", "sc.api.hint.ticker", "get"],
    ["GET", "/api/runs", "sc.api.hint.runs", "get"],
    ["GET", "/api/runs/{id}", "sc.api.hint.run", "get"],
    ["DELETE", "/api/runs/{id}", "sc.api.hint.runDelete", "del"],
    ["GET", "/api/history/{ticker}", "sc.api.hint.history", "get"],
    ["GET", "/api/export/csv", "sc.api.hint.exportCsv", "get"],
    ["GET", "/api/export/xlsx", "sc.api.hint.exportXlsx", "get"],
    ["POST", "/api/export/both", "sc.api.hint.exportBoth", "post"],
    ["POST", "/api/cache/clear", "sc.api.hint.cacheClear", "post"],
  ];

  const CAVEATS = ["noForecast", "modelBridge", "crossCurrency", "dailyClose"];

  const state = { health: null };

  /* -------------------------------------------------------------- rendering */
  function renderProviders(health) {
    const host = $("#srcgrid");
    const chains = health ? {
      fundamentals: String(health.providers.fundamentals || "").split("->").map((s) => s.trim()),
      prices: String(health.providers.prices || "").split("->").map((s) => s.trim()),
      quotes: String(health.providers.quotes || "").split("->").map((s) => s.trim()),
    } : null;
    const yahoo = health ? Boolean(health.providers.yahoo_enabled) : false;

    host.innerHTML = PROVIDERS.map((p) => {
      const hops = (chains && chains[p.key] && chains[p.key].length) ? chains[p.key] : p.hops;
      const live = hops.filter((h) => !/yahoo/i.test(h) || yahoo);
      return `<div class="glass srccard">
        <div class="top">
          <span class="ico">${icon(p.icon)}</span>
          <b>${t(p.labelKey)}</b>
          <span class="st badge ${health ? "badge--good" : ""}"><span class="dot"></span>${health ? t("sc.ready") : t("sc.down")}</span>
        </div>
        <div class="chain">
          ${hops.map((h, i) => `<div class="hop">
            <span class="idx">${i + 1}</span>
            <span>${escapeHtml(h)}</span>
            ${/yahoo/i.test(h) ? `<em>${yahoo ? "" : t("sc.hop.off")}</em>` : ""}
          </div>`).join("")}
        </div>
        <div class="foot">${t(`sc.prov.role.${p.key}`)}<br />${t(`sc.prov.note.${p.key}`)}</div>
      </div>`;
    }).join("");
  }

  function renderCache(health) {
    const host = $("#cachebar");
    if (!health) {
      host.innerHTML = `<div class="state" style="padding:14px"><p>${t("sc.cache.offline")}</p></div>`;
      return;
    }
    const entries = Object.entries(health.cache || {});
    if (!entries.length) {
      host.innerHTML = `<div class="state" style="padding:14px"><p>${t("sc.cache.empty")}</p></div>`;
      return;
    }
    const max = Math.max(...entries.map(([, n]) => n), 1);
    const total = entries.reduce((a, [, n]) => a + n, 0);
    host.innerHTML = entries
      .sort((a, b) => b[1] - a[1])
      .map(([ns, n]) => `<div class="ns">
        <span class="nm">${escapeHtml(ns)}</span>
        <span class="bar"><i style="transform:scaleX(${(n / max).toFixed(3)})"></i></span>
        <span class="n">${t("sc.cache.entries", { n })}</span>
      </div>`).join("") + `<hr class="rule" />
      <div class="row" style="font-size:11.5px;color:var(--muted)">
        <span>${t("sc.cache.total", { n: total })}</span><span class="spacer"></span>
        <span>${t("sc.cache.namespaces", { n: entries.length })}</span></div>`;
  }

  function renderStore(health) {
    const host = $("#storeInfo");
    if (!health) {
      host.innerHTML = `<dt>${t("rf.jobs.status")}</dt><dd>${t("health.offline")}</dd>`;
      return;
    }
    const s = health.store || {};
    host.innerHTML = `
      <dt>${t("sc.store.runs")}</dt><dd>${s.runs ?? DASH}</dd>
      <dt>${t("sc.store.rows")}</dt><dd>${s.snapshots ?? DASH}</dd>
      <dt>${t("sc.store.last")}</dt><dd>${s.last_capture ? escapeHtml(when(s.last_capture)) : DASH}</dd>
      <dt>${t("sc.store.last")}</dt><dd>${s.last_capture ? escapeHtml(ago(s.last_capture)) : DASH}</dd>
      <dt>${t("sc.store.checked")}</dt><dd>${escapeHtml(when(health.time))}</dd>`;
  }

  function renderDict() {
    $("#dict").innerHTML = DICT.map((g) => `
      <div class="grp">
        <h4>${t(g.group)}<span class="w">${t("common.weight")} ${escapeHtml(g.weight)}</span></h4>
        <ul>
          ${g.items.map(([field]) => `
            <li>
              <code>${escapeHtml(field)}</code>
              <b>${t(`m.${field}`)}</b>
              <em>${t(`m.${field}.hint`)}</em>
            </li>`).join("")}
        </ul>
      </div>`).join("");
  }

  function renderApi() {
    $("#apiBody").innerHTML = ENDPOINTS.map(([method, path, descKey, kind]) => `
      <tr>
        <td><span class="method ${kind}">${method}</span></td>
        <td><code class="mono" style="font-size:11.5px">${escapeHtml(path)}</code></td>
        <td class="muted" style="white-space:normal">${escapeHtml(t(descKey))}</td>
      </tr>`).join("");
  }

  function renderCaveats() {
    $("#caveats").innerHTML = CAVEATS.map((key) => `
      <div class="glass glass--solid" style="padding:18px 20px;display:flex;flex-direction:column;gap:8px">
        <div class="row" style="gap:9px">
          <span style="width:26px;height:26px;border-radius:9px;display:grid;place-items:center;background:var(--accent-soft);border:1px solid var(--accent-line);color:var(--accent-deep)">
            ${icon("info")}
          </span>
          <b style="font-size:13px">${t(`sc.caveat.${key}.title`)}</b>
        </div>
        <p class="muted" style="margin:0;font-size:12.5px;line-height:1.7">${t(`sc.caveat.${key}.body`)}</p>
      </div>`).join("");
  }

  /* ------------------------------------------------------------------- wire */
  window.pageInit = async function pageInit() {
    $("#pingBtn").innerHTML = `${icon("refresh")}<span data-i18n="sc.recheck">${t("sc.recheck")}</span>`;
    renderDict();
    renderApi();
    renderCaveats();

    const apply = (health) => {
      state.health = health;
      $("#healthBadge").className = "badge" + (health ? " badge--good" : " badge--low");
      $("#healthBadge").innerHTML = `<span class="dot"></span>${health ? t("health.ready") : t("health.offline")}`;
      renderProviders(health);
      renderCache(health);
      renderStore(health);
    };

    document.addEventListener("fr:health", (event) => {
      const detail = event.detail || {};
      apply(detail.ok ? detail.health : null);
    });

    const load = async () => {
      try {
        apply(await api.health());
      } catch {
        apply(null);
      }
    };

    $("#pingBtn").addEventListener("click", async (e) => {
      e.currentTarget.disabled = true;
      await load();
      e.currentTarget.disabled = false;
      toast(t("sc.toast.rechecked"));
    });

    $("#clearCache").innerHTML = `${icon("broom")}<span data-i18n="sc.cache.clear">${t("sc.cache.clear")}</span>`;
    $("#clearCache").addEventListener("click", async (e) => {
      if (!confirm(t("sc.cache.confirm"))) return;
      e.currentTarget.disabled = true;
      try {
        const data = await api.clearCache();
        toast(t("sc.cache.cleared", { n: data.removed ?? 0 }));
        await load();
      } catch (err) {
        toast(t("sc.cache.clearFail", { msg: err.message }), "err");
      } finally {
        e.currentTarget.disabled = false;
      }
    });

    await load();
    if (!state.health) {
      toast(t("sc.toast.offline"), "warn", 5000);
    }

    document.addEventListener("fr:lang", () => {
      renderDict();
      renderApi();
      renderCaveats();
      apply(state.health);
    });
  };
})();
