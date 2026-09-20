"""Standalone check that every added stock carries the full metric set.

Run:  PYTHONPATH=. python tests/verify_end_to_end.py

It fetches the current default peer set, asserts that every market, return, risk
and consensus metric the peer screen compares is populated, exercises all three
export formats, and exits non-zero on the first gap it finds. Use it after
changing a provider or a metric so a silent blank cannot ship.
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.engine import scoring
from app.pipeline import build_rows, get_benchmark, get_price_basis

# Every market/risk figure a row must carry for the peer comparison to work.
REQUIRED = [
    "price", "market_cap", "high_52w", "low_52w",
    "return_1m", "return_3m", "return_6m", "return_1y", "return_ytd",
    "excess_return_3m", "excess_return_6m", "excess_return_1y",
    "benchmark_return_3m", "benchmark_return_6m", "benchmark_return_1y",
    "volatility", "downside_deviation", "sharpe_ratio", "sortino_ratio",
    "beta", "beta_1y", "max_drawdown_1y", "drawdown_52w",
    "analyst_target", "analyst_upside", "forward_pe",
    "revenue_fy0", "revenue_growth_yoy", "revenue_cagr_5y",
    "gross_margin", "operating_margin", "net_margin", "roe",
    "ocf_to_net_income", "debt_to_assets", "gaap_eps",
    # Indicators added for depth: these must populate across the pool.
    "revenue_cagr_3y", "eps_growth_yoy", "net_income_growth_yoy",
    "roa", "asset_turnover", "capex_intensity", "cash_to_assets",
    "price_to_ocf", "ev_to_sales", "ebitda_fy0", "net_debt_fy0",
]
# Structurally unavailable for some filers or balance sheets (negative invested
# capital, net cash, negative or near-zero growth, no SBC tag), so they are
# reported separately rather than treated as failures.
CONDITIONAL = [
    "roic", "ev_to_ebitda", "fcf_yield", "fcf_margin",
    "gross_profit_growth_yoy", "fcf_growth_yoy", "operating_leverage",
    "sbc_pct_revenue", "peg_ratio", "net_debt_to_ebitda",
]

failures: list[str] = []


def main() -> int:
    tickers = settings.ticker_list
    print(f"peer set ({len(tickers)}): {', '.join(tickers)}")
    basis = get_price_basis()
    benchmark = get_benchmark(basis)
    print(f"price basis: {basis}")
    if benchmark is None:
        failures.append("benchmark SPY unavailable")
        print("  !! SPY unavailable")
    else:
        print(f"benchmark:   {benchmark.source}, {len(benchmark.points)} bars, "
              f"adjusted={benchmark.used_adjusted}, last={benchmark.points[-1].d}")

    rows, errors = build_rows(tickers, benchmark=benchmark, basis=basis)
    for ticker, message in errors.items():
        failures.append(f"{ticker}: {message}")
        print(f"  !! {ticker}: {message}")

    header = (f"{'ticker':7}{'cov':>6}{'rank':>6}{'3M':>9}{'6M':>9}{'1Y':>9}"
              f"{'vsSPY6M':>10}{'vol':>8}{'sharpe':>8}{'beta1y':>8}{'maxDD':>9}{'target':>9}")
    print("\n" + header)
    print("-" * len(header))
    for row in sorted(rows, key=lambda r: (r.rank is None, r.rank or 999)):
        def f(value, scale=1.0, digits=2):
            return "—" if value is None else f"{value * scale:+.{digits}f}"
        print(f"{row.ticker:7}{row.data_coverage:>3}/{len(scoring.METRICS):<2}"
              f"{str(row.rank if row.rank is not None else '—'):>6}"
              f"{f(row.return_3m, 100, 1):>9}{f(row.return_6m, 100, 1):>9}{f(row.return_1y, 100, 1):>9}"
              f"{f(row.excess_return_6m, 100, 1):>10}{f(row.volatility, 100, 1):>8}"
              f"{f(row.sharpe_ratio, 1, 2):>8}{f(row.beta_1y, 1, 2):>8}"
              f"{f(row.max_drawdown_1y, 100, 1):>9}{f(row.analyst_target, 1, 1):>9}")

    print("\nper-metric blanks across the peer set")
    for attr in REQUIRED:
        missing = [r.ticker for r in rows if getattr(r, attr, None) is None]
        if missing:
            failures.append(f"{attr} blank for {', '.join(missing)}")
            print(f"  BLANK  {attr:24} {', '.join(missing)}")
    for attr in CONDITIONAL:
        missing = [r.ticker for r in rows if getattr(r, attr, None) is None]
        note = "  (structurally n/a)" if missing else ""
        print(f"  {'blank' if missing else 'ok   '}  {attr:24} {', '.join(missing) or '—'}{note}")

    # Every metric the API says is scored must actually have a score, so the
    # score column can never render blank for a scored metric.
    print("\nscored metrics without a score")
    score_gaps = 0
    for attr, _, _ in scoring.METRICS:
        z = scoring.Z_FIELDS[attr]
        blank = [r.ticker for r in rows
                 if getattr(r, attr) is not None and getattr(r, z) is None]
        if blank:
            score_gaps += 1
            failures.append(f"{attr} present but unscored for {', '.join(blank)}")
            print(f"  GAP    {attr:24} -> {z:22} {', '.join(blank)}")
    if not score_gaps:
        print(f"  none — all {len(scoring.METRICS)} scored metrics have a score wherever the value exists")

    # Unscored metrics must be catalogued (shipped to the UI) or they cannot be
    # labelled as reference-only and would render as an unexplained blank.
    catalogued = {m["attr"] for m in scoring.metric_catalog()}
    uncatalogued = sorted(scoring.reference_attrs() - catalogued)
    if uncatalogued:
        failures.append(f"reference metrics missing from the catalogue: {uncatalogued}")
        print(f"  !! reference metrics not catalogued: {uncatalogued}")

    if rows and all(r.rank_eligible for r in rows):
        print("\nall rows rank-eligible: yes")
    else:
        not_eligible = [r.ticker for r in rows if not r.rank_eligible]
        failures.append(f"not rank-eligible: {', '.join(not_eligible)}")

    # ---- exports -----------------------------------------------------------
    print("\nexports")
    outdir = Path(tempfile.mkdtemp(prefix="fr-verify-"))
    try:
        from app.exporters.csv_export import write_csv
        from app.exporters.excel_export import write_xlsx
        from app.exporters.detail_export import detail_rows_to_csv

        csv_path = write_csv(rows, outdir / "peers.csv")
        xlsx_path = write_xlsx(rows, outdir / "peers.xlsx")
        detail = detail_rows_to_csv(rows, rows[0])
        (outdir / "detail.csv").write_text(detail, encoding="utf-8-sig")
        print(f"  csv    {csv_path.stat().st_size:>9,} bytes  {csv_path.name}")
        print(f"  xlsx   {xlsx_path.stat().st_size:>9,} bytes  {xlsx_path.name}")
        print(f"  detail {len(detail):>9,} chars")

        import csv as _csv

        header_line = next(
            line for line in csv_path.read_text(encoding="utf-8-sig").splitlines() if "Ticker" in line
        )
        for expected in ("Sharpe ratio", "6M excess vs SPY", "Volatility (ann.)", "Beta (1Y)"):
            if expected not in header_line:
                failures.append(f"csv header missing {expected}")
        if "3M excess vs SPY" not in detail:
            failures.append("detail export missing 3M excess row")
    except Exception:
        failures.append("export failed")
        traceback.print_exc()

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for item in failures:
            print(f"  - {item}")
        return 1
    print(f"OK: {len(rows)} rows, every required metric populated, all exports written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
