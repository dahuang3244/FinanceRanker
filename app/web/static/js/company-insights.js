/* Dated public-data review; narrative is a description of observed metrics. */
"use strict";
const FRInsights = (() => {
  let last = null;
  const lang = () => FRI18n.current();
  const tr = (zh, en) => lang() === "zh" ? zh : en;
  const e = FR.escapeHtml;
  const pct = v => v == null ? "—" : `${(v * 100).toFixed(1)}%`;
  const relative = (price, ma) => price && ma ? pct(price / ma - 1) : "—";
  const text = (v) => v == null ? "—" : e(String(v));
  function item(name, value) {
    return `<div class="insight-kv"><span>${name}</span><strong>${value}</strong></div>`;
  }
  function facet(name, value) {
    const score = value.score;
    const fill = score == null ? 0 : (score / 7 * 100).toFixed(1);
    return `<div class="insight-facet"><span>${name} <small>${value.count}/${value.total}</small></span>
      <div class="insight-facet-track"><i style="width:${fill}%"></i></div>
      <b>${score == null ? "—" : `${score.toFixed(1)}/7`}</b></div>`;
  }
  function narrative(data) {
    const r = data.report, k = data.risk, t = data.technical;
    const growth = r.revenue_growth == null ? tr("营收同比暂无可比数据", "comparable revenue growth is unavailable") :
      tr(`营收同比 ${pct(r.revenue_growth)}`, `revenue grew ${pct(r.revenue_growth)} year on year`);
    const cash = r.fcf_margin == null ? tr("FCF 利润率待补", "FCF margin is unavailable") :
      tr(`FCF 利润率 ${pct(r.fcf_margin)}`, `FCF margin is ${pct(r.fcf_margin)}`);
    const movement = t.ma200 == null ? tr("200 日趋势数据不足", "200-day trend history is insufficient") :
      (t.close >= t.ma200 ? tr("价格高于 200 日均线", "price is above its 200-day average") :
                             tr("价格低于 200 日均线", "price is below its 200-day average"));
    const risk = k.drawdown_52w == null ? tr("52 周回撤尚未核实", "52-week drawdown is not verified") :
      tr(`距 52 周收盘高点 ${pct(k.drawdown_52w)}`, `${pct(k.drawdown_52w)} from the 52-week closing high`);
    return `${growth} · ${cash} · ${movement} · ${risk}。`;
  }
  function render() {
    const host = document.querySelector("#companyInsights");
    if (!host || !last) return;
    const { technical: t, report: r, risk: k, news: n, facets: f } = last;
    const price = t.close;
    const trends = [20, 50, 200].map(days =>
      item(`${days}${tr("日均线", "-day average")}`, relative(price, t[`ma${days}`]))).join("");
    const titles = (n.articles || []).slice(0, 5).map(a => {
      let safe;
      try { const u = new URL(a.url); if (!["https:", "http:"].includes(u.protocol)) return ""; safe = u.href; }
      catch { return ""; }
      return `<li><a href="${e(safe)}" target="_blank" rel="noopener noreferrer">${e(a.title)}</a><time>${e(a.published)}</time></li>`;
    }).join("");
    const mood = n.temperature == null ? tr("样本不足，未计算", "Insufficient directional headlines") :
      n.temperature > 0.2 ? tr("偏暖", "Warmer") : n.temperature < -0.2 ? tr("偏冷", "Cooler") : tr("平稳", "Balanced");
    const insight = (name, value) => `<span class="insight-pill">${name} <b>${value}</b></span>`;
    host.innerHTML = `<section class="glass insight-card">
      <div class="section-head"><div><h2>${tr("公司概览", "Company overview")}</h2>
        <p class="hint">${tr("基于本次快照与有日期的收盘价；用于描述，不预测回报。", "Snapshot and dated closes; descriptive, not a return forecast.")}</p></div></div>
      <p class="insight-summary">${e(narrative(last))}</p>
      <div class="insight-pills">
        ${insight(tr("成长", "Growth"), pct(r.revenue_growth))}
        ${insight(tr("盈利", "Profitability"), pct(r.roe))}
        ${insight(tr("现金", "Cash"), pct(r.fcf_margin))}
        ${insight(tr("回撤", "Drawdown"), pct(k.drawdown_52w))}
      </div>
      <div class="insight-facets">${facet(tr("盈利", "Profit"), f.profit)}${facet(tr("成长", "Growth"), f.growth)}
        ${facet(tr("估值", "Valuation"), f.valuation)}${facet(tr("近期", "Recent"), f.recent)}
        ${facet(tr("风险", "Risk"), f.risk)}</div>
      <p class="insight-note">${tr("面板 1–7 分由池内指标分位映射；风险分越高代表相对风险越大。n/N 表示有数据的指标数。", "Facets map available peer percentiles to 1–7; higher risk means more relative risk. n/N shows covered metrics.")}</p>
      <div class="insight-note">${tr("下次财报", "Next earnings")}: ${r.next_earnings_date ? text(r.next_earnings_date) : tr("尚无经核实的公告日期", "No verified announced date")}
        ${r.next_earnings_source ? `(${text(r.next_earnings_source)})` : ""}
        · ${tr("最近财年截止", "Latest fiscal year end")}: ${text(r.fiscal_end)}
        · ${tr("财报来源", "Filing source")}: ${text(r.source)}</div>
    </section>
    <div class="insight-grid">
      <section class="glass insight-card"><h2>${tr("趋势结构", "Trend structure")}</h2>
        <p class="hint">${tr("价格相对均线 · 收盘价口径", "Close versus moving averages")}</p>
        <div class="insight-facts">${trends}${item("RSI(14)", t.rsi14 == null ? "—" : t.rsi14.toFixed(1))}
          ${item(tr("3 个月回报", "3-month return"), pct(last.return_3m))}
          ${item(tr("距 52 周高点", "From 52-week high"), pct(t.from_high))}</div>
        <p class="insight-note">${tr("数据截至", "As of")} ${text(t.as_of)} · ${text(t.source)} · ${t.adjusted ? tr("复权", "adjusted") : tr("未经复权；回报不含分红", "unadjusted; returns exclude dividends")}</p>
      </section>
      <section class="glass insight-card"><h2>${tr("回报面 / 风险面", "Return / risk")}</h2>
        <p class="hint">${tr("保留原指标及缺失状态，避免用主观评分填空。", "Original metrics and coverage, without fabricated scores.")}</p>
        <div class="insight-facts">${item(tr("营收同比", "Revenue YoY"), pct(r.revenue_growth))}
          ${item(tr("ROE", "ROE"), pct(r.roe))}${item(tr("FCF 利润率", "FCF margin"), pct(r.fcf_margin))}
          ${item(tr("资产负债率", "Debt / assets"), pct(k.debt_to_assets))}
          ${item(tr("52 周回撤", "52-week drawdown"), pct(k.drawdown_52w))}
          ${item("Beta", k.beta == null ? "—" : k.beta.toFixed(2) + "×")}</div>
        <p class="insight-note">${tr("财报", "Filing")}: ${text(r.source)} · ${tr("行情", "Prices")}: ${text(k.market_source)}</p>
      </section>
    </div>
    <section class="glass insight-card"><h2>${tr("新闻情绪温度", "News sentiment temperature")}</h2>
      <p class="hint">${tr("近 90 天公开标题的英文关键词代理量度；不是全文分析，也不进入排名。", "English headline keyword proxy over 90 days; not full-text sentiment or a ranking input.")}</p>
      <div class="insight-mood"><strong>${mood}</strong><span>${n.temperature == null ? "—" : `${n.temperature >= 0 ? "+" : ""}${n.temperature.toFixed(2)}`}</span>
        <small>${(n.articles || []).length} ${tr("篇标题", "headlines")} · ${text(n.source)}</small></div>
      ${titles ? `<ul class="insight-articles">${titles}</ul>` : `<p class="insight-note">${tr("当前未获取到符合条件的新闻；不显示虚构情绪值。", "No eligible news retrieved; sentiment is unavailable.")}</p>`}
    </section>`;
  }
  async function load() {
    const host = document.querySelector("#companyInsights");
    if (!host) return;
    const qs = new URLSearchParams(location.search);
    const ticker = qs.get("ticker") || "";
    if (!/^[A-Za-z0-9.\-^]{1,12}$/.test(ticker)) return;
    const context = new URLSearchParams();
    if (qs.get("job_id")) context.set("job_id", qs.get("job_id"));
    if (qs.get("run_id")) context.set("run_id", qs.get("run_id"));
    host.innerHTML = `<p class="insight-note">${tr("正在读取趋势和新闻…", "Loading trend and news…")}</p>`;
    try {
      const resp = await fetch(`/api/insights/${encodeURIComponent(ticker)}?${context}`, { cache: "no-store" });
      if (!resp.ok) throw new Error(String(resp.status));
      last = await resp.json();
      render();
    } catch (err) {
      host.innerHTML = `<p class="insight-note">${tr("补充资料暂时不可用：", "Additional data unavailable: ")}${e(err.message)}</p>`;
    }
  }
  return { load, render };
})();
