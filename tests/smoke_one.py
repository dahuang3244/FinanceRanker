"""Quick smoke test: fetch one ticker through the full pipeline."""

from __future__ import annotations

import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from app import pipeline  # noqa: E402

ticker = sys.argv[1] if len(sys.argv) > 1 else "MSFT"

benchmark = pipeline.get_benchmark()
print("benchmark:", "ok" if benchmark else "unavailable")

row, error = pipeline.fetch_one(ticker, benchmark)
if error:
    print("ERROR:", error)
    raise SystemExit(1)

show = [
    "ticker", "company", "status", "price", "market_cap", "beta", "return_1y",
    "revenue_fy0", "revenue_growth_yoy", "revenue_cagr_5y", "gross_margin",
    "operating_margin", "net_margin", "roe", "roic", "fcf_fy0", "fcf_margin",
    "fcf_yield", "debt_to_assets", "gaap_eps", "model_adjusted_eps",
    "selected_adjusted_eps", "eps_fy1_estimate", "forward_pe", "price_to_sales",
    "ev_to_ebitda", "price_to_book", "price_to_fcf", "drawdown_52w",
    "fiscal_end", "source_quality", "data_coverage",
]
for field in show:
    print(f"  {field:26s} = {getattr(row, field, None)!r}")
print("\nnotes:", row.notes)
