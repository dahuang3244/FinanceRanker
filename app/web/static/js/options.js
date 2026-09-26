/* ==========================================================================
   Options environment — read the option chain for any US ticker.

   Tabs mirror how a desk reads a chain, in the order the questions are asked:
     总览        what the book looks like right now
     成交与仓位   the numbers behind it, by expiry
     定位/异动    where positions cluster and what traded unusually
     阈值        what would change the reading
     个人分析     a plain-language pass with the limits attached

   Nothing here reports implied volatility. The free chain returns placeholder
   IV with zero bid/ask, so an IV/RV or skew figure would be invented; the
   thresholds that would normally use it are translated into the volume and
   open-interest terms this data actually supports.
   ========================================================================== */
"use strict";

(() => {
  const { api, icon, $, $$, escapeHtml, toast, stateBlock, num, money, pct, tone, DASH } = FR;

  const state = { data: null, ticker: "", tab: "overview", loading: false };
  const TABS = ["overview", "flow", "position", "threshold", "analysis"];

  /* --------------------------------------------------------------- helpers */
  const fmtPcr = (v) => (v === null || v === undefined ? DASH : Number(v).toFixed(3));
  const fmtInt = (v) => (v === null || v === undefined ? DASH : Number(v).toLocaleString());
  const fmtPct1 = (v) => (v === null || v === undefined ? DASH : `${(v * 100).toFixed(1)}%`);

  /** Verdict implied by a put/call ratio, with its plain reading. */
  function pcrReading(value) {
    if (value === null || value === undefined) return { key: "flat", label: t("op.pcr.na") };
    if (value >= 1.0) return { key: "defensive", label: t("op.pcr.defensive") };
    if (value <= 0.7) return { key: "bullish", label: t("op.pcr.bullish") };
    return { key: "flat", label: t("op.pcr.balanced") };
  }

  const BUCKET_ORDER = [
    "directional_call", "directional_put", "upside_call", "downside_put",
    "deep_call", "deep_put",
  ];
  const bucketLabel = (key) => t(`op.bucket.${key}`);

  /** A metric tile, matching the app's existing board styling. */
  const tile = (label, value, sub, extra = "") => `
    <div class="op-tile${extra ? " " + extra : ""}">
      <div class="k">${escapeHtml(label)}</div>
      <div class="v mono">${value}</div>
      ${sub ? `<div class="d">${escapeHtml(sub)}</div>` : ""}
    </div>`;

  /** Horizontal bar pair used for the Call/Put structure rows. */
  function splitBar(callValue, putValue) {
    const total = (callValue || 0) + (putValue || 0);
    if (!total) return "";
    const callShare = ((callValue || 0) / total) * 100;
    return `<span class="op-split">
      <i class="call" style="width:${callShare.toFixed(1)}%"></i>
      <i class="put" style="width:${(100 - callShare).toFixed(1)}%"></i>
    </span>`;
  }

  /* --------------------------------------------------------------- sections */
  /** Dealer gamma exposure, in currency per 1% move, or a dash when the source
      reports no Greeks — a figure would then have to be assumed. */
  const gexValue = (v) => {
    if (v === null || v === undefined) return DASH;
    const abs = Math.abs(v);
    const sign = v < 0 ? "−" : "+";
    if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
    if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`;
    if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
    return `${sign}$${abs.toFixed(0)}`;
  };

  function headline() {
    const d = state.data;
    const totals = d.totals || {};
    const volume = pcrReading(totals.volume_pcr);
    const oi = pcrReading(totals.oi_pcr);
    const spot = d.spot;
    const painGap = d.max_pain && spot ? d.max_pain / spot - 1 : null;
    const flipGap = d.gamma_flip && spot ? d.gamma_flip / spot - 1 : null;

    const tiles = [
      tile(t("op.spot"), spot ? money(spot) : DASH, d.currency || ""),
      tile(t("op.volPcr"), fmtPcr(totals.volume_pcr), volume.label,
        volume.key === "defensive" ? "is-warn" : volume.key === "bullish" ? "is-good" : ""),
      tile(t("op.oiPcr"), fmtPcr(totals.oi_pcr), oi.label),
      tile(t("op.maxPain"), d.max_pain ? money(d.max_pain) : DASH,
        painGap === null ? "" : `${t("op.vsSpot")} ${(painGap * 100).toFixed(1)}%`),
      tile(t("op.gex"), gexValue(d.gex),
        d.gamma_flip
          ? `${t("op.gammaFlip")} ${money(d.gamma_flip)} (${(flipGap * 100).toFixed(1)}%)`
          : (d.gex === null || d.gex === undefined ? t("op.gex.none") : ""),
        (d.gex ?? 0) < 0 ? "is-warn" : (d.gex ? "is-good" : "")),
      tile(t("op.straddle"), fmtPct1(d.straddle_move),
        d.max_pain_expiry ? t("op.toExpiry", { d: d.max_pain_expiry }) : ""),
    ].join("");

    const structure = d.summaries.map((s) => `
      <tr>
        <td class="mono">${escapeHtml(s.expiration)}<em class="faint"> · ${s.days}${t("op.days")}</em></td>
        <td>${splitBar(s.call_oi, s.put_oi)}</td>
        <td class="right mono">${(s.oi_pcr ?? 0).toFixed(3)}</td>
        <td class="right mono">${(s.volume_pcr ?? 0).toFixed(3)}</td>
        <td class="right mono">${s.max_pain ? money(s.max_pain) : DASH}</td>
      </tr>`).join("");

    return `
      <section class="glass op-block">
        <div class="op-board">${tiles}</div>
      </section>
      <section class="glass op-block">
        <h2 class="op-h2">${t("op.pcrStructure")}</h2>
        <p class="hint">${t("op.pcrStructure.hint")}</p>
        <div class="dimtable tablewrap tablewrap--flat">
          <table class="data">
            <thead><tr>
              <th>${t("op.expiry")}</th><th>${t("op.callPutSplit")}</th>
              <th class="right">${t("op.oiPcr")}</th>
              <th class="right">${t("op.volPcr")}</th>
              <th class="right">${t("op.maxPain")}</th>
            </tr></thead>
            <tbody>${structure}</tbody>
          </table>
        </div>
      </section>
      <section class="glass op-block">
        <h2 class="op-h2">${t("op.concentration")}</h2>
        <p class="hint">${t("op.concentration.hint")}</p>
        ${concentrationTable()}
      </section>
      ${gexSection()}`;
  }

  /** Gamma exposure by strike, and the sign convention it rests on. */
  function gexSection() {
    const d = state.data;
    const rows = d.gex_strikes || [];
    if (!rows.length) {
      return `<section class="glass op-block">
        <h2 class="op-h2">${t("op.gex.title")}</h2>
        <p class="hint">${t("op.gex.noGreeks")}</p>
      </section>`;
    }
    const max = Math.max(...rows.map((r) => Math.abs(r.net_gex || 0))) || 1;
    return `<section class="glass op-block">
      <h2 class="op-h2">${t("op.gex.title")}</h2>
      <p class="hint">${t("op.gex.hint")}</p>
      <div class="dimtable tablewrap tablewrap--flat">
        <table class="data">
          <thead><tr>
            <th>${t("op.strike")}</th><th>${t("op.gex.net")}</th>
            <th class="right">${t("op.gex.call")}</th><th class="right">${t("op.gex.put")}</th>
            <th class="right">${t("op.vsSpot")}</th>
          </tr></thead>
          <tbody>${rows.map((r) => `<tr>
            <td class="mono">${money(r.strike)}</td>
            <td><span class="op-gexbar${(r.net_gex || 0) < 0 ? " is-neg" : ""}">
              <i style="width:${(Math.abs(r.net_gex || 0) / max * 100).toFixed(1)}%"></i></span></td>
            <td class="right mono">${gexValue(r.call_gex)}</td>
            <td class="right mono">${gexValue(r.put_gex)}</td>
            <td class="right mono ${(r.distance || 0) >= 0 ? "up" : "down"}">${
              ((r.distance || 0) * 100).toFixed(1)}%</td>
          </tr>`).join("")}</tbody>
        </table>
      </div>
      <p class="hint as-note-inline">${escapeHtml(d.gex_basis || "")}</p>
    </section>`;
  }

  function concentrationTable() {
    const rows = state.data.concentration || [];
    if (!rows.length) return `<p class="hint">${t("op.none")}</p>`;
    const max = Math.max(...rows.map((r) => r.total_oi || 0)) || 1;
    return `<div class="dimtable tablewrap tablewrap--flat">
      <table class="data">
        <thead><tr>
          <th>${t("op.strike")}</th><th>${t("op.callPutSplit")}</th>
          <th class="right">${t("op.callOi")}</th><th class="right">${t("op.putOi")}</th>
          <th class="right">${t("op.totalOi")}</th><th class="right">${t("op.vsSpot")}</th>
        </tr></thead>
        <tbody>${rows.map((r) => `<tr>
          <td class="mono">${money(r.strike)}</td>
          <td><span class="op-weight"><i style="width:${((r.total_oi || 0) / max * 100).toFixed(1)}%"></i></span></td>
          <td class="right mono">${fmtInt(r.call_oi)}</td>
          <td class="right mono">${fmtInt(r.put_oi)}</td>
          <td class="right mono">${fmtInt(r.total_oi)}</td>
          <td class="right mono ${(r.distance || 0) >= 0 ? "up" : "down"}">${
            r.distance === null || r.distance === undefined ? DASH : `${(r.distance * 100).toFixed(1)}%`}</td>
        </tr>`).join("")}</tbody>
      </table>
    </div>`;
  }

  function flowSection() {
    const d = state.data;
    const rows = d.summaries.map((s) => {
      const buckets = BUCKET_ORDER
        .filter((k) => (s.buckets || {})[k])
        .map((k) => `<span class="op-chip op-chip--${k}">${
          escapeHtml(bucketLabel(k))} <b>${fmtInt(s.buckets[k])}</b></span>`).join("");
      return `<div class="op-expirycard">
        <div class="op-expiryhead">
          <b class="mono">${escapeHtml(s.expiration)}</b>
          <span class="faint">${s.days}${t("op.days")} · ${t("op.expiriesAvailable", {
            n: state.data.expiries_available })}</span>
        </div>
        <div class="op-board op-board--tight">
          ${tile(t("op.callVolume"), fmtInt(s.call_volume))}
          ${tile(t("op.putVolume"), fmtInt(s.put_volume))}
          ${tile(t("op.callOi"), fmtInt(s.call_oi))}
          ${tile(t("op.putOi"), fmtInt(s.put_oi))}
          ${tile(t("op.volPcr"), fmtPcr(s.volume_pcr), pcrReading(s.volume_pcr).label)}
          ${tile(t("op.oiPcr"), fmtPcr(s.oi_pcr), pcrReading(s.oi_pcr).label)}
        </div>
        <div class="op-chips">${buckets}</div>
      </div>`;
    }).join("");
    return `<section class="glass op-block">
      <h2 class="op-h2">${t("op.byExpiry")}</h2>
      <p class="hint">${t("op.byExpiry.hint")}</p>
      ${rows}
    </section>`;
  }

  function positionSection() {
    const unusual = state.data.unusual || [];
    const rows = unusual.length
      ? unusual.map((u) => `<tr>
          <td><span class="op-chip op-chip--${u.direction}">${escapeHtml(bucketLabel(u.direction))}</span></td>
          <td class="mono right">${money(u.strike)}</td>
          <td class="mono">${escapeHtml(u.expiration)}<em class="faint"> · ${u.days}${t("op.days")}</em></td>
          <td class="right mono"><b>${u.ratio.toFixed(1)}×</b></td>
          <td class="right mono">${fmtInt(u.volume)}</td>
          <td class="right mono">${fmtInt(u.open_interest)}</td>
          <td class="right mono">${u.last_price ? money(u.last_price) : DASH}</td>
        </tr>`).join("")
      : `<tr><td colspan="7" class="hint">${t("op.noUnusual")}</td></tr>`;
    return `
      <section class="glass op-block">
        <h2 class="op-h2">${t("op.unusual")}</h2>
        <p class="hint">${t("op.unusual.hint")}</p>
        <div class="dimtable tablewrap tablewrap--flat">
          <table class="data">
            <thead><tr>
              <th>${t("op.kind")}</th><th class="right">${t("op.strike")}</th>
              <th>${t("op.expiry")}</th><th class="right">${t("op.volOi")}</th>
              <th class="right">${t("op.volume")}</th><th class="right">${t("op.openInterest")}</th>
              <th class="right">${t("op.last")}</th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </section>
      <section class="glass op-block">
        <h2 class="op-h2">${t("op.concentration")}</h2>
        <p class="hint">${t("op.concentration.hint")}</p>
        ${concentrationTable()}
      </section>`;
  }

  /* ----------------------------------------------------------------- thresholds */
  /* The three conditions, with the trigger levels the reader expects:
       IV/RV    1.05+   seller premium is rich
       Skew     2.5+    defensive demand is rising
       PCR      0.90+   puts are taking over
     IV/RV and skew are computed from this chain (see app/volatility.py); when
     either cannot be measured the card says so rather than showing a number. */
  const THRESHOLDS = [
    { key: "ivrv", scale: 2.0, format: "ratio2" },
    { key: "skew", scale: 8.0, format: "volpts" },
    { key: "pcr", scale: 2.0, format: "ratio3" },
  ];

  function thresholdValue(key, d) {
    const vol = d.volatility || {};
    const totals = d.totals || {};
    if (key === "ivrv") return vol.iv_rv;
    if (key === "skew") return vol.skew_points;
    return totals.volume_pcr;
  }

  const TRIGGER = { ivrv: 1.05, skew: 2.5, pcr: 0.90 };

  function formatThreshold(key, value) {
    if (value === null || value === undefined) return DASH;
    if (key === "skew") return `${value >= 0 ? "+" : ""}${value.toFixed(1)} vol pts`;
    if (key === "ivrv") return value.toFixed(3);
    return value.toFixed(3);
  }

  function thresholds() {
    const d = state.data;
    const v = d.verdict || {};
    const concText = v.concentration_ratio === null || v.concentration_ratio === undefined
      ? DASH
      : (v.concentration_ratio >= 0.5 ? t("op.conc.high") : t("op.conc.low"));

    const defs = THRESHOLDS.map(({ key, scale }) => {
      const value = thresholdValue(key, d);
      const trigger = TRIGGER[key];
      const measurable = value !== null && value !== undefined;
      return {
        key, scale, value, trigger, measurable,
        breached: measurable && value >= trigger,
      };
    });

    const cards = defs.map((item) => {
      const ratio = item.measurable
        ? Math.max(0, Math.min(1, Math.abs(item.value) / item.scale)) : 0;
      return `<div class="op-thresh${item.breached ? " is-breached" : ""}${
        item.measurable ? "" : " is-unmeasured"}">
        <div class="op-threshhead">
          <b>${escapeHtml(t(`op.th.${item.key}`))}</b>
          <span class="ptag tone-${item.breached ? "low" : "good"}">${
            item.measurable
              ? escapeHtml(formatThreshold(item.key, item.value))
              : escapeHtml(t("op.th.unavailable"))}</span>
        </div>
        <p class="hint">${escapeHtml(t(`op.th.${item.key}.hint`))}</p>
        <span class="op-threshbar${item.breached ? " is-on" : ""}">
          <i style="width:${(ratio * 100).toFixed(1)}%"></i>
          <em style="left:${Math.min(100, (item.trigger / item.scale) * 100).toFixed(1)}%"></em>
        </span>
        <div class="op-threshfoot">
          <span>${t("op.th.current")} <b class="mono">${escapeHtml(
            formatThreshold(item.key, item.value))}</b></span>
          <span>${t("op.th.trigger")} <b class="mono">${escapeHtml(
            formatThreshold(item.key, item.trigger))}</b></span>
        </div>
      </div>`;
    }).join("");

    const rows = defs.map((item) => `<tr class="${item.breached ? "is-total" : ""}">
      <td>${escapeHtml(t(`op.th.${item.key}`))}</td>
      <td class="right mono">${escapeHtml(formatThreshold(item.key, item.value))}</td>
      <td class="right mono">${escapeHtml(formatThreshold(item.key, item.trigger))}</td>
      <td>${escapeHtml(t(`op.th.${item.key}.when`))}</td>
    </tr>`).join("");

    // How the volatility figures were derived, so a reader can weigh them.
    const vol = d.volatility || {};
    const derivation = vol.basis
      ? `<p class="hint op-derivation">${escapeHtml(vol.basis)}${
          vol.skew && vol.skew.put_strike
            ? " · " + escapeHtml(t("op.th.skewDetail", {
                put: money(vol.skew.put_strike), putIv: pct(vol.skew.put_iv * 100),
                call: money(vol.skew.call_strike), callIv: pct(vol.skew.call_iv * 100),
              }))
            : ""}</p>`
      : `<p class="hint op-derivation">${escapeHtml(t("op.th.noVol"))}</p>`;

    return `
      <section class="glass op-block">
        <div class="op-threshgrid">${cards}</div>
        ${derivation}
      </section>
      <section class="glass op-block">
        <h2 class="op-h2">${t("op.th.table")}</h2>
        <p class="hint">${t("op.th.table.hint")}</p>
        <div class="dimtable tablewrap tablewrap--flat">
          <table class="data">
            <thead><tr>
              <th>${t("op.th.condition")}</th><th class="right">${t("op.th.currentShort")}</th>
              <th class="right">${t("op.th.triggerShort")}</th><th>${t("op.th.reading")}</th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </section>`;
  }

  /* ------------------------------------------------------- personal analysis */
  /* Translates the numbers into a reading, and — just as important — states what
     would change it and what cannot be read from this data at all. Every claim
     names the quantity it came from, because a positioning screen that asserts
     without attribution is indistinguishable from a guess. */
  /** The next dated event, and whether it lands inside the option's life.
      A catalyst is not visible in volume or open interest at all, so it is
      stated rather than left for the reader to remember. */
  function catalystCard(v) {
    if (!v.next_earnings) return "";
    const days = v.days_to_earnings;
    const soon = v.earnings_in_window;
    return `<div class="op-catalyst${soon ? " is-soon" : ""}">
      <span class="op-catalyst-k">${t("op.an.catalyst")}</span>
      <b class="mono">${escapeHtml(String(v.next_earnings))}</b>
      ${days === null || days === undefined ? ""
        : `<span class="ptag tone-mid">${escapeHtml(t("op.an.catalyst.days", { n: days }))}</span>`}
      ${soon ? `<span class="ptag tone-low">${t("op.an.catalyst.inWindow")}</span>` : ""}
      <span class="d">${escapeHtml(soon ? t("op.an.catalyst.warn") : t("op.an.catalyst.note"))}</span>
    </div>`;
  }

  function analysisSection() {
    const d = state.data;
    const notes = d.notes || [];
    const totals = d.totals || {};
    const v = d.verdict || {};
    const spot = d.spot;
    const top = (d.concentration || [])[0];

    // Five-dot score, filled from the left.
    const score = (n) => {
      if (n === null || n === undefined) return `<span class="op-stars">${DASH}</span>`;
      const dots = [1, 2, 3, 4, 5]
        .map((i) => `<i class="${i <= n ? "on" : ""}"></i>`).join("");
      return `<span class="op-stars" title="${n}/5">${dots}<b>${n}/5</b></span>`;
    };

    // The backend supplies the verdict as keys plus numbers; the wording is
    // named here so it follows the selected language rather than arriving as a
    // single baked-in string.
    const label = (group, key, fallback) => {
      if (!key) return fallback || DASH;
      const name = `op.${group}.${key}`;
      const text = t(name);
      return text === name ? (fallback || DASH) : text;
    };
    const stanceText = label("stance", v.stance, v.stance_label);
    const leanText = label("lean", v.lean_key, v.lean);
    const noveltyText = label("novelty", v.novelty, v.novelty_label);
    const concText = v.concentration_ratio === null || v.concentration_ratio === undefined
      ? DASH
      : (v.concentration_ratio >= 0.5 ? t("op.conc.high") : t("op.conc.low"));

    const stanceTone = v.stance === "defensive" ? "low"
      : v.stance === "bullish" ? "good" : "mid";

    // Premium flow, so the size of the tilt is visible and not just its ratio.
    const callPrem = totals.call_premium;
    const putPrem = totals.put_premium;
    const premMax = Math.max(callPrem || 0, putPrem || 0) || 1;

    const claim = (title, toneKey, body) => `
      <div class="op-claim">
        <div class="op-claimhead">
          <b>${escapeHtml(title)}</b>
          <span class="ptag tone-${toneKey}">${escapeHtml(t(`op.an.tag.${toneKey}`))}</span>
        </div>
        <p>${escapeHtml(body)}</p>
      </div>`;

    return `
      <section class="glass op-block">
        <header class="op-anhead">
          <div>
            <h2 class="op-h2">${t("op.an.title")}</h2>
            <p class="hint">${t("op.an.subtitle")}</p>
          </div>
          <span class="ptag tone-${stanceTone} op-stance">${escapeHtml(stanceText)}</span>
        </header>

        <div class="op-judgement">
          <p>${escapeHtml(t("op.an.summary", {
            ticker: d.ticker,
            stance: stanceText,
            lean: leanText,
            pcr: fmtPcr(totals.volume_pcr),
            oiPcr: fmtPcr(totals.oi_pcr),
          }))}</p>
        </div>

        <div class="op-questions">
          <div class="op-q">
            <span class="k">${t("op.an.q.chase")}</span>
            ${score(v.chase_safety)}
            <span class="d">${escapeHtml(t("op.an.q.chase.hint"))}</span>
          </div>
          <div class="op-q">
            <span class="k">${t("op.an.q.put")}</span>
            ${score(v.put_value)}
            <span class="d">${escapeHtml(t("op.an.q.put.hint"))}</span>
          </div>
          <div class="op-q">
            <span class="k">${t("op.an.q.wait")}</span>
            <b class="op-q-answer">${escapeHtml(t(`op.an.wait.${v.wait || "unknown"}`))}</b>
            <span class="d">${escapeHtml(t("op.an.q.wait.hint"))}</span>
          </div>
        </div>
        ${catalystCard(v)}
      </section>

      <div class="op-two">
        ${claim(t("op.an.why.title"), "defensive", t("op.an.why.body", {
          pcr: fmtPcr(totals.volume_pcr),
          gap: v.flow_gap === null || v.flow_gap === undefined
            ? DASH : `${v.flow_gap >= 0 ? "+" : ""}${v.flow_gap.toFixed(3)}`,
        }))}
        ${claim(t("op.an.call.title"), "bullish", t("op.an.call.body", {
          call: fmtPcr(totals.volume_pcr ? 1 / totals.volume_pcr : null),
          callShare: fmtPct1(totals.volume_calls && (totals.volume_calls + totals.volume_puts)
            ? totals.volume_calls / (totals.volume_calls + totals.volume_puts) : null),
          oiShare: fmtPct1(totals.oi_calls && (totals.oi_calls + totals.oi_puts)
            ? totals.oi_calls / (totals.oi_calls + totals.oi_puts) : null),
        }))}
      </div>

      <div class="op-two">
        <section class="glass op-block">
          <div class="op-claimhead">
            <b>${t("op.an.holding.title")}</b>
            <span class="ptag tone-mid">${escapeHtml(concText)}</span>
          </div>
          <ul class="annotes">
            <li>${escapeHtml(t("op.an.holding.env", {
              ratio: v.concentration_ratio === null || v.concentration_ratio === undefined
                ? DASH : fmtPct1(v.concentration_ratio),
            }))}</li>
            <li>${escapeHtml(t("op.an.holding.zone", {
              pain: d.max_pain ? money(d.max_pain) : DASH,
              dist: v.max_pain_distance === null || v.max_pain_distance === undefined
                ? DASH : `${(v.max_pain_distance * 100).toFixed(1)}%`,
              expiry: d.max_pain_expiry || DASH,
              spot: spot ? money(spot) : DASH,
            }))}</li>
            <li>${escapeHtml(t("op.an.holding.flow", {
              call: callPrem === undefined ? DASH : money(callPrem),
              put: putPrem === undefined ? DASH : money(putPrem),
            }))}</li>
          </ul>
          <div class="op-premium">
            <span class="op-premiumrow"><em>${t("op.callVolume")}</em>
              <i class="call" style="width:${((callPrem || 0) / premMax * 100).toFixed(1)}%"></i>
              <b class="mono">${callPrem === undefined ? DASH : money(callPrem)}</b></span>
            <span class="op-premiumrow"><em>${t("op.putVolume")}</em>
              <i class="put" style="width:${((putPrem || 0) / premMax * 100).toFixed(1)}%"></i>
              <b class="mono">${putPrem === undefined ? DASH : money(putPrem)}</b></span>
          </div>
        </section>

        <section class="glass op-block">
          <div class="op-claimhead">
            <b>${t("op.an.watch.title")}</b>
            <span class="ptag tone-mid">${t("op.an.tag.watch")}</span>
          </div>
          <ul class="annotes">
            ${(d.concentration || []).slice(0, 3).map((row) => `<li>${escapeHtml(t("op.an.watch.item", {
              strike: money(row.strike),
              oi: fmtInt(row.total_oi),
              dist: row.distance === null || row.distance === undefined
                ? DASH : `${(row.distance * 100).toFixed(1)}%`,
              calls: fmtInt(row.call_oi), puts: fmtInt(row.put_oi),
            }))}</li>`).join("")}
          </ul>
          <p class="hint">${escapeHtml(t("op.an.watch.hint"))}</p>
        </section>
      </div>

      <section class="glass op-block">
        <h2 class="op-h2">${t("op.an.howto")}</h2>
        <div class="op-readers">
          <div class="op-reader">
            <b>${t("op.an.holder.title")}</b>
            <p>${escapeHtml(t("op.an.holder.body", {
              pain: d.max_pain ? money(d.max_pain) : DASH }))}</p>
          </div>
          <div class="op-reader">
            <b>${t("op.an.contrarian.title")}</b>
            <p>${escapeHtml(t("op.an.contrarian.body", { pcr: fmtPcr(totals.volume_pcr) }))}</p>
          </div>
          <div class="op-reader">
            <b>${t("op.an.risk.title")}</b>
            <p>${escapeHtml(t("op.an.risk.body", {
              strike: top ? money(top.strike) : DASH,
              oi: top ? fmtInt(top.total_oi) : DASH }))}</p>
          </div>
        </div>
      </section>

      <section class="glass op-block">
        <h2 class="op-h2">${t("op.an.change.title")}</h2>
        <p class="hint">${t("op.an.change.hint")}</p>
        <div class="dimtable tablewrap tablewrap--flat">
          <table class="data">
            <thead><tr>
              <th>${t("op.th.condition")}</th><th class="right">${t("op.th.currentShort")}</th>
              <th class="right">${t("op.th.triggerShort")}</th><th>${t("op.an.change.effect")}</th>
            </tr></thead>
            <tbody>${THRESHOLDS.map(({ key }) => {
              const value = thresholdValue(key, d);
              return `<tr class="${value !== null && value !== undefined && value >= TRIGGER[key]
                ? "is-total" : ""}">
                <td>${escapeHtml(t(`op.th.${key}`))}</td>
                <td class="right mono">${escapeHtml(formatThreshold(key, value))}</td>
                <td class="right mono">${escapeHtml(formatThreshold(key, TRIGGER[key]))}</td>
                <td>${escapeHtml(t(`op.an.change.${key}`))}</td>
              </tr>`;
            }).join("")}</tbody>
          </table>
        </div>
      </section>

      <section class="glass op-block op-block--note">
        <h2 class="op-h2">${t("op.an.limits")}</h2>
        <ul class="annotes">
          <li>${escapeHtml(t("op.an.limit.noIv"))}</li>
          <li>${escapeHtml(t("op.an.limit.oi"))}</li>
          <li>${escapeHtml(t("op.an.limit.gamma"))}</li>
          <li>${escapeHtml(t("op.an.limit.snapshot"))}</li>
          <li>${escapeHtml(t("op.an.limit.free"))}</li>
          ${(notes || []).map((n) => `<li>${escapeHtml(n)}</li>`).join("")}
        </ul>
      </section>`;
  }

  /* ------------------------------------------------------------------ render */
  const SECTIONS = {
    overview: headline, flow: flowSection, position: positionSection,
    threshold: thresholds, analysis: analysisSection,
  };

  function renderTabs() {
    return `<div class="seg op-tabs" role="tablist">${TABS.map((key) => `
      <button type="button" role="tab" data-tab="${key}"
        aria-selected="${state.tab === key}">${escapeHtml(t(`op.tab.${key}`))}</button>`).join("")}</div>`;
  }

  function render() {
    const host = $("#opBody");
    if (state.loading) {
      host.innerHTML = `<section class="glass op-block">
        <div class="skeleton line" style="width:24%"></div>
        <div class="skeleton" style="height:120px;margin-top:16px"></div></section>`;
      return;
    }
    if (!state.data) {
      host.innerHTML = `<div class="glass">${stateBlock({
        icon: "search", title: t("op.empty.title"), body: t("op.empty.body"),
      })}</div>`;
      return;
    }
    if (state.data.available === false) {
      host.innerHTML = `<div class="glass">${stateBlock({
        icon: "alert", title: t("op.unavailable.title", { ticker: state.data.ticker || "" }),
        body: escapeHtml(state.data.reason || t("op.unavailable.body")),
        action: { href: "ranking.html", label: t("action.back.ranking") },
      })}</div>`;
      return;
    }
    const d = state.data;
    host.innerHTML = `
      <div class="op-head">
        <div>
          <h2 class="op-ticker">${escapeHtml(d.ticker)}
            <span class="mono op-spot">${d.spot ? money(d.spot) : DASH}</span></h2>
          <p class="hint">${escapeHtml(t("op.meta", {
            used: d.expiries_used, available: d.expiries_available,
            source: d.source || "" }))}</p>
        </div>
        <div class="op-headright">
          <span class="badge">${escapeHtml(d.max_pain_expiry || "")}</span>
        </div>
      </div>
      ${renderTabs()}
      <div class="op-section" data-section="${state.tab}">${SECTIONS[state.tab]()}</div>`;

    $$("#opBody .op-tabs button").forEach((b) =>
      b.addEventListener("click", () => { state.tab = b.dataset.tab; render(); }));
  }

  /* -------------------------------------------------------------------- load */
  async function load(ticker) {
    const symbol = (ticker || $("#opQuery").value || "").trim().toUpperCase();
    if (!symbol) { toast(t("op.needTicker"), "warn"); return; }
    $("#opQuery").value = symbol;
    state.ticker = symbol;
    state.loading = true;
    render();
    try {
      state.data = await api.options(symbol, {
        expiries: Number($("#opExpiries").value) || 4,
        refresh: $("#opRefresh").checked,
      });
      if (state.data.available) {
        const qs = new URLSearchParams(location.search);
        qs.set("ticker", symbol);
        history.replaceState(null, "", `${location.pathname}?${qs}`);
      }
      $("#opSource").innerHTML = `<span class="dot"></span>${escapeHtml(
        state.data.source || t("op.noData"))}`;
    } catch (err) {
      state.data = { ticker: symbol, available: false, reason: err.message || t("op.loadFailed") };
    }
    state.loading = false;
    render();
  }

  window.pageInit = async function pageInit() {
    $("#opSearchIcon").innerHTML = icon("search");
    $("#opLoad").innerHTML = `${icon("search")}<span data-i18n="op.load">${t("op.load")}</span>`;

    // Offer the screened pool as one-click starting points.
    try {
      const data = await api.ranking();
      const picks = (data.rows || []).slice(0, 14);
      $("#opPicks").innerHTML = picks.map((r) =>
        `<button class="chip" type="button" data-t="${escapeHtml(r.ticker)}">${
          escapeHtml(r.ticker)}</button>`).join("");
      $$("#opPicks [data-t]").forEach((b) =>
        b.addEventListener("click", () => load(b.dataset.t)));
    } catch { /* the picker is a convenience, not a dependency */ }

    $("#opLoad").addEventListener("click", () => load());
    $("#opQuery").addEventListener("keydown", (e) => { if (e.key === "Enter") load(); });

    const preset = new URLSearchParams(location.search).get("ticker");
    if (preset) { $("#opQuery").value = preset.toUpperCase(); await load(preset); } else { render(); }
  };

  // Test hook: the page's own fetch-and-render path, without the DOM wiring.
  window.__opLoad = load;
  window.__opRender = render;
  window.__opState = state;

  document.addEventListener("fr:lang", () => render());
})();
