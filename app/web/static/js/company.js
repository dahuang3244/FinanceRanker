/* Company detail page — thin wrapper around FRCompany. */
"use strict";

(() => {
  const { icon, $ } = FR;

  window.pageInit = async function pageInit() {
    const qs = new URLSearchParams(location.search);
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    $("#toRanking").innerHTML = `${icon("table")}<span data-i18n="action.back.ranking">${t("action.back.ranking")}</span>`;
    $("#pingBtn").innerHTML = `${icon("refresh")}<span data-i18n="action.reload">${t("action.reload")}</span>`;
    $("#pingBtn").addEventListener("click", async (e) => {
      e.currentTarget.disabled = true;
      await FRCompany.mount({ force: true });
      await FRInsights.load();
      e.currentTarget.disabled = false;
    });
    // keep the breadcrumb link carrying the snapshot context
    document.querySelectorAll('a[href="ranking.html"]').forEach((a) => { a.href = `ranking.html${suffix}`; });
    document.addEventListener("fr:lang", () => {
      $("#toRanking").innerHTML = `${icon("table")}<span>${t("action.back.ranking")}</span>`;
      $("#pingBtn").innerHTML = `${icon("refresh")}<span>${t("action.reload")}</span>`;
      FRCompany.relabel();
      FRInsights.render();
    });

    await FRCompany.mount({ force: true });
    await FRInsights.load();
  };
})();
