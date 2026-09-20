/* ==========================================================================
   Analyst section for the company detail page.

   Renders four things the rankings cannot show, all from one Yahoo
   quoteSummary call:
     * the rating distribution, newest period versus three months ago;
     * every published rating / target change, newest first;
     * reported versus consensus EPS for the quarters Yahoo publishes;
     * forward consensus for the current and next quarter and year.

   Charts are hand-rolled SVG rather than a charting dependency: the whole UI is
   dependency-free, and three simple plots do not justify an exception.
   ========================================================================== */
"use strict";

const FRAnalyst = (() => {
  const { escapeHtml, num, money, tone, DASH } = FR;

  let detail = null;      // last fetched payload
  let loading = false;

  /* --------------------------------------------------------------- helpers */
  const RATING_ORDER = ["strong_buy", "buy", "hold", "sell", "strong_sell"];

  function ratingLabel(key) {
    const labels = {
      strong_buy: ["强烈买入", "Strong buy"],
      buy: ["买入", "Buy"],
      hold: ["持有", "Hold"],
      sell: ["卖出", "Sell"],
      strong_sell: ["强烈卖出", "Strong sell"],
    };
    const pair = labels[key] || [key, key];
    return FRI18n.current() === "en" ? pair[1] : pair[0];
  }

  function ratingTone(key) {
    return { strong_buy: "good", buy: "good", hold: "mid", sell: "low", strong_sell: "low" }[key] || "mid";
  }

  function fmtPct(value, digits = 1) {
    if (value === null || value === undefined || Number.isNaN(value)) return DASH;
    return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(digits)}%`;
  }

  function fmtNum(value, digits = 2) {
    if (value === null || value === undefined || Number.isNaN(value)) return DASH;
    return Number(value).toFixed(digits);
  }

  /** Ratings summed across every category, for the "as of now" headline. */
  function totals(entry) {
    if (!entry) return 0;
    return RATING_ORDER.reduce((sum, k) => sum + (entry[k] || 0), 0);
  }

  /* ----------------------------------------------------- rating distribution */
  function ratingBars(ratings) {
    if (!ratings || !ratings.length) return "";
    const current = ratings[0];
    const total = totals(current);
    if (!total) return "";
    const segments = RATING_ORDER
      .filter((k) => (current[k] || 0) > 0)
      .map((k) => {
        const value = current[k] || 0;
        const share = (value / total) * 100;
        return `<span class="rseg tone-${ratingTone(k)}" style="width:${share.toFixed(2)}%"
          title="${escapeHtml(ratingLabel(k))} ${value} · ${share.toFixed(1)}%"></span>`;
      }).join("");
    const legend = RATING_ORDER
      .filter((k) => (current[k] || 0) > 0)
      .map((k) => `<span class="rkey"><i class="tone-${ratingTone(k)}"></i>${
        escapeHtml(ratingLabel(k))} <b>${current[k]}</b></span>`).join("");
    // The oldest period in the payload is the comparison point; with four
    // monthly snapshots that is roughly a quarter ago.
    const prior = ratings[ratings.length - 1];
    const priorTotal = totals(prior);
    const drift = prior && priorTotal
      ? `<span class="rprior">${escapeHtml(t("an.threeMonthsAgo"))}：
          ${escapeHtml(ratingLabel("buy"))} ${prior.buy || 0} ·
          ${escapeHtml(ratingLabel("hold"))} ${prior.hold || 0} ·
          ${escapeHtml(ratingLabel("sell"))} ${(prior.sell || 0) + (prior.strong_sell || 0)}</span>`
      : "";
    return `
      <div class="rbar" role="img" aria-label="${escapeHtml(t("an.ratings"))}">${segments}</div>
      <div class="rlegend">${legend}
        <span class="rtotal">${t("an.analystsCovering", { n: total })}</span></div>
      ${drift}`;
  }

  /* ------------------------------------------------- target-price history */
  /* Plots each published target as a dot and the price line beneath it, over
     the last 24 months. The spread of dots says more than a single consensus
     number: it shows disagreement and how it moved. */
  function targetChart(detail) {
    const actions = (detail.actions || []).filter((a) => a.price_target);
    if (actions.length < 2) return "";
    const today = new Date();
    const cutoff = new Date(today.getTime() - 730 * 86400000);
    const points = actions
      .map((a) => ({ date: new Date(a.date), target: a.price_target, firm: a.firm, to: a.to_grade }))
      .filter((p) => p.date >= cutoff && p.target > 0)
      .sort((a, b) => a.date - b.date);
    if (points.length < 2) return "";

    const W = 720, H = 260, PAD = { l: 46, r: 12, t: 12, b: 26 };
    const targets = points.map((p) => p.target);
    const price = detail.current_price;
    const lows = [...targets, price].filter((v) => v);
    let lo = Math.min(...lows), hi = Math.max(...lows);
    if (hi - lo < 1e-9) { hi = lo + 1; lo -= 1; }
    const span = hi - lo;
    lo -= span * 0.08; hi += span * 0.08;

    const x = (d) => PAD.l + ((d - points[0].date) / Math.max(1, points[points.length - 1].date - points[0].date)) * (W - PAD.l - PAD.r);
    const y = (v) => H - PAD.b - ((v - lo) / (hi - lo)) * (H - PAD.t - PAD.b);

    const gridlines = [0, 0.25, 0.5, 0.75, 1].map((f) => {
      const value = lo + (hi - lo) * f;
      const gy = y(value);
      return `<line x1="${PAD.l}" y1="${gy.toFixed(1)}" x2="${W - PAD.r}" y2="${gy.toFixed(1)}"
        class="achart-grid"/>
        <text x="${PAD.l - 6}" y="${(gy + 3).toFixed(1)}" class="achart-axis" text-anchor="end">${
          value >= 1000 ? Math.round(value).toLocaleString() : value.toFixed(0)}</text>`;
    }).join("");

    const dots = points.map((p) => {
      const above = price ? p.target >= price : true;
      return `<circle cx="${x(p.date).toFixed(1)}" cy="${y(p.target).toFixed(1)}" r="3.6"
        class="adot tone-${above ? "good" : "low"}"><title>${
        escapeHtml(`${p.firm} · ${p.date.toISOString().slice(0, 10)} · ${p.target}${p.to ? " · " + p.to : ""}`)
        }</title></circle>`;
    }).join("");

    const priceLine = price
      ? `<line x1="${PAD.l}" y1="${y(price).toFixed(1)}" x2="${W - PAD.r}" y2="${y(price).toFixed(1)}"
          class="achart-price"/>
         <text x="${W - PAD.r}" y="${(y(price) - 6).toFixed(1)}" class="achart-axis" text-anchor="end">${
          escapeHtml(t("an.spot"))} ${price.toFixed(2)}</text>`
      : "";

    return `<svg viewBox="0 0 ${W} ${H}" class="achart" role="img"
        aria-label="${escapeHtml(t("an.targetChart"))}">
      ${gridlines}${priceLine}${dots}
      <text x="${PAD.l}" y="${H - 8}" class="achart-axis">${
        points[0].date.toISOString().slice(0, 7)}</text>
      <text x="${W - PAD.r}" y="${H - 8}" class="achart-axis" text-anchor="end">${
        points[points.length - 1].date.toISOString().slice(0, 7)}</text>
    </svg>`;
  }

  /* -------------------------------------------------- earnings surprise bars */
  function surpriseChart(history) {
    if (!history || !history.length) return "";
    const rows = history.filter((e) => e.eps_actual !== null || e.eps_estimate !== null);
    if (!rows.length) return "";
    const W = 720, H = 200, PAD = { l: 46, r: 12, t: 14, b: 34 };
    const values = rows.flatMap((e) => [e.eps_actual, e.eps_estimate]).filter((v) => v !== null);
    const zero = Math.min(0, ...values);
    const top = Math.max(...values, 0.0001);
    const lo = zero < 0 ? zero * 1.15 : 0;
    const hi = top * 1.15 || 1;
    const y = (v) => H - PAD.b - ((v - lo) / (hi - lo)) * (H - PAD.t - PAD.b);
    const slot = (W - PAD.l - PAD.r) / rows.length;
    const barW = Math.min(26, slot / 3);

    const bars = rows.map((e, i) => {
      const cx = PAD.l + slot * i + slot / 2;
      const parts = [];
      if (e.eps_estimate !== null) {
        const yTop = y(Math.max(e.eps_estimate, 0));
        parts.push(`<rect x="${(cx - barW - 2).toFixed(1)}" y="${yTop.toFixed(1)}"
          width="${barW}" height="${Math.max(1, H - PAD.b - yTop).toFixed(1)}" class="abar-est">
          <title>${escapeHtml(t("an.consensus"))} ${fmtNum(e.eps_estimate)}</title></rect>`);
      }
      if (e.eps_actual !== null) {
        const beat = e.surprise_pct !== null && e.surprise_pct >= 0;
        const yTop = y(Math.max(e.eps_actual, 0));
        parts.push(`<rect x="${(cx + 2).toFixed(1)}" y="${yTop.toFixed(1)}"
          width="${barW}" height="${Math.max(1, H - PAD.b - yTop).toFixed(1)}"
          class="abar-act tone-${beat ? "good" : "low"}">
          <title>${escapeHtml(t("an.actual"))} ${fmtNum(e.eps_actual)} · ${fmtPct(e.surprise_pct)}</title></rect>`);
      }
      const label = e.quarter_end ? e.quarter_end.slice(0, 7) : e.period;
      parts.push(`<text x="${cx.toFixed(1)}" y="${H - 18}" class="achart-axis" text-anchor="middle">${label}</text>`);
      if (e.surprise_pct !== null) {
        parts.push(`<text x="${cx.toFixed(1)}" y="${H - 5}" text-anchor="middle"
          class="achart-surprise tone-${e.surprise_pct >= 0 ? "good" : "low"}">${fmtPct(e.surprise_pct)}</text>`);
      }
      return parts.join("");
    }).join("");

    return `<svg viewBox="0 0 ${W} ${H}" class="achart" role="img"
        aria-label="${escapeHtml(t("an.surpriseChart"))}">
      <line x1="${PAD.l}" y1="${y(lo).toFixed(1)}" x2="${W - PAD.r}" y2="${y(lo).toFixed(1)}" class="achart-grid"/>
      ${bars}
    </svg>
    <div class="rlegend">
      <span class="rkey"><i class="abar-act tone-good"></i>${escapeHtml(t("an.actual"))}</span>
      <span class="rkey"><i class="abar-est"></i>${escapeHtml(t("an.consensus"))}</span>
    </div>`;
  }

  /* ------------------------------------------------------------ recent actions */
  function actionRows(actions, limit = 12) {
    const rows = (actions || []).slice(0, limit);
    if (!rows.length) return "";
    const body = rows.map((a) => {
      const changed = a.from_grade && a.to_grade && a.from_grade !== a.to_grade;
      const grade = changed
        ? `${escapeHtml(a.from_grade)} → <b>${escapeHtml(a.to_grade)}</b>`
        : escapeHtml(a.to_grade || a.from_grade || DASH);
      const target = a.price_target
        ? (a.prior_price_target
            ? `${money(a.price_target)} <em class="faint">← ${money(a.prior_price_target)}</em>`
            : money(a.price_target))
        : DASH;
      const toneKey = a.price_target_action === "Raises" ? "good"
        : a.price_target_action === "Lowers" ? "low" : "mid";
      return `<tr>
        <td class="mono">${escapeHtml(a.date)}</td>
        <td>${escapeHtml(a.firm)}</td>
        <td>${grade}</td>
        <td class="right mono">${target}</td>
        <td class="right"><span class="ptag tone-${toneKey}">${
          escapeHtml(a.price_target_action || a.action || DASH)}</span></td>
      </tr>`;
    }).join("");
    return `<div class="dimtable tablewrap tablewrap--flat">
      <table class="data">
        <thead><tr>
          <th>${t("an.date")}</th><th>${t("an.firm")}</th><th>${t("an.grade")}</th>
          <th class="right">${t("an.target")}</th><th class="right">${t("an.change")}</th>
        </tr></thead>
        <tbody>${body}</tbody>
      </table></div>`;
  }

  /* --------------------------------------------------------------- estimates */
  function estimateRows(estimates) {
    const rows = (estimates || []).filter((e) => e.eps_avg !== null);
    if (!rows.length) return "";
    const label = {
      "0q": ["本季", "Current quarter"],
      "+1q": ["下季", "Next quarter"],
      "0y": ["本财年", "Current year"],
      "+1y": ["下财年", "Next year"],
    };
    const body = rows.map((e) => {
      const pair = label[e.period] || [e.period, e.period];
      const text = FRI18n.current() === "en" ? pair[1] : pair[0];
      return `<tr>
        <td>${escapeHtml(text)}<em class="faint mono" style="margin-left:6px">${escapeHtml(e.end_date || "")}</em></td>
        <td class="right mono">${fmtNum(e.eps_avg)}</td>
        <td class="right mono faint">${
          e.eps_low !== null && e.eps_high !== null ? `${fmtNum(e.eps_low, 1)}–${fmtNum(e.eps_high, 1)}` : DASH}</td>
        <td class="right mono">${e.analyst_count ?? DASH}</td>
        <td class="right mono ${(e.growth || 0) >= 0 ? "up" : "down"}">${fmtPct(e.growth)}</td>
      </tr>`;
    }).join("");
    return `<div class="dimtable tablewrap tablewrap--flat">
      <table class="data">
        <thead><tr>
          <th>${t("an.period")}</th><th class="right">${t("an.epsAvg")}</th>
          <th class="right">${t("an.range")}</th><th class="right">${t("an.analysts")}</th>
          <th class="right">${t("an.growth")}</th>
        </tr></thead>
        <tbody>${body}</tbody>
      </table></div>`;
  }

  /* ------------------------------------------------------------------ render */
  function render() {
    if (loading) {
      return `<section class="glass dimblock"><div class="skeleton line" style="width:24%"></div>
        <div class="skeleton" style="height:110px;margin-top:16px"></div></section>`;
    }
    if (!detail) return "";
    if (detail.available === false) {
      return `<section class="glass dimblock" data-analyst="unavailable">
        <div class="section-head"><div>
          <h2 style="font-size:13.5px">${t("an.title")}</h2>
          <p class="hint">${escapeHtml(detail.reason || t("an.unavailable"))}</p>
        </div></div></section>`;
    }

    const upside = detail.target_mean && detail.current_price
      ? detail.target_mean / detail.current_price - 1 : null;
    const headline = `
      <div class="anhead">
        <div class="antarget">
          <span class="k">${t("an.meanTarget")}</span>
          <b class="mono">${detail.target_mean ? money(detail.target_mean) : DASH}</b>
          ${upside === null ? "" : `<span class="ptag tone-${upside >= 0 ? "good" : "low"}">${
            t("an.upside")} ${fmtPct(upside)}</span>`}
        </div>
        <div class="anrange">
          <span class="k">${t("an.range")}</span>
          <span class="mono">${detail.target_low ? money(detail.target_low) : DASH} — ${
            detail.target_high ? money(detail.target_high) : DASH}</span>
        </div>
        <div class="anrec">
          <span class="k">${t("an.consensus")}</span>
          <b>${escapeHtml(detail.recommendation || DASH)}</b>
          ${detail.recommendation_mean !== null && detail.recommendation_mean !== undefined
            ? `<span class="faint mono">${fmtNum(detail.recommendation_mean)} / 5</span>` : ""}
        </div>
        ${detail.next_earnings_date ? `<div class="annext">
          <span class="k">${t("an.nextEarnings")}</span>
          <span class="mono">${escapeHtml(detail.next_earnings_date)}</span></div>` : ""}
      </div>`;

    const blocks = [
      detail.ratings && detail.ratings.length
        ? `<h3 class="anh3">${t("an.ratings")}</h3>${ratingBars(detail.ratings)}` : "",
      detail.actions && detail.actions.length
        ? `<h3 class="anh3">${t("an.targetChart")}</h3>${targetChart(detail)}` : "",
      detail.earnings_history && detail.earnings_history.length
        ? `<h3 class="anh3">${t("an.surpriseChart")}</h3>${surpriseChart(detail.earnings_history)}` : "",
      detail.estimates && detail.estimates.length
        ? `<h3 class="anh3">${t("an.estimates")}</h3>${estimateRows(detail.estimates)}` : "",
      detail.actions && detail.actions.length
        ? `<h3 class="anh3">${t("an.recentActions")}</h3>${actionRows(detail.actions)}` : "",
    ].filter(Boolean).join("");

    const notes = (detail.notes || []).map((n) => `<li>${escapeHtml(n)}</li>`).join("");
    return `<section class="glass dimblock" data-analyst="ready">
      <header class="dimhead">
        <span class="dimico">${FR.icon("users")}</span>
        <div class="dimtitle">
          <h2>${t("an.title")}<em>${t("an.subtitle")}</em></h2>
          <p>${t("an.lede")}</p>
        </div>
        <div class="dimmeta">
          <span class="badge">${escapeHtml(detail.source || "")}</span>
          ${detail.as_of ? `<span class="badge">${escapeHtml(String(detail.as_of).slice(0, 10))}</span>` : ""}
        </div>
      </header>
      ${headline}
      ${blocks || `<p class="hint">${t("an.noData")}</p>`}
      ${notes ? `<ul class="annotes">${notes}</ul>` : ""}
    </section>`;
  }

  /* ------------------------------------------------------------------- loading */
  async function mount(ticker) {
    const host = document.getElementById("analystSection");
    if (!host || !ticker) return;
    loading = true;
    host.innerHTML = render();
    try {
      detail = await FR.api.analyst(ticker);
    } catch (err) {
      detail = { available: false, reason: err.message || t("an.unavailable") };
    }
    loading = false;
    host.innerHTML = render();
  }

  /** Re-render on language change; the payload itself does not change. */
  function relabel() {
    const host = document.getElementById("analystSection");
    if (host && detail) host.innerHTML = render();
  }

  return { mount, relabel, render, _internals: { targetChart, surpriseChart, ratingBars } };
})();
