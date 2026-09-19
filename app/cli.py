"""CLI entry point.

Examples
--------
    python -m app.cli refresh MSFT AAPL NVDA --csv out.csv --xlsx out.xlsx
    python -m app.cli refresh --default --xlsx report.xlsx
    python -m app.cli show                 # latest snapshot as a table
    python -m app.cli runs                 # list snapshots
    python -m app.cli serve                # start the web service
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from app import pipeline, store
from app.config import settings
from app.exporters.csv_export import write_csv
from app.exporters.excel_export import write_xlsx

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cli")


def _fmt(value, kind: str = "") -> str:
    if value is None:
        return "—"
    if kind == "pct":
        return f"{value * 100:.1f}%"
    if kind == "mult":
        return f"{value:.2f}x"
    if kind == "money":
        for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
            if abs(value) >= div:
                return f"${value / div:.2f}{unit}"
    return f"{value:.2f}"


def cmd_refresh(args: argparse.Namespace) -> int:
    tickers = settings.ticker_list if args.default else args.tickers
    if not tickers:
        print("no tickers given (pass symbols or --default)", file=sys.stderr)
        return 2

    started = datetime.now()

    def progress(ticker: str, status: str, done: int, total: int) -> None:
        print(f"  [{done:>3}/{total}] {ticker:<8} {status}")

    print(f"Refreshing {len(tickers)} tickers…")
    rows, errors = pipeline.build_rows(tickers, progress=progress)
    finished = datetime.now()

    print()
    header = f"{'#':>3} {'Ticker':<8} {'Company':<26} {'Score':>6} {'Growth':>7} {'Profit':>7} {'Cash':>7} {'Value':>7} {'Mkt':>7}"
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row.rank if row.rank else '-':>3} {row.ticker:<8} "
            f"{(row.company or '')[:26]:<26} "
            f"{_fmt(row.score_overall):>6} {_fmt(row.score_growth):>7} "
            f"{_fmt(row.score_profitability):>7} {_fmt(row.score_cash):>7} "
            f"{_fmt(row.score_valuation):>7} {_fmt(row.score_market):>7}"
        )

    if errors:
        print("\nFailures:")
        for ticker, message in errors.items():
            print(f"  {ticker}: {message}")

    if args.csv:
        path = write_csv(rows, Path(args.csv))
        print(f"\nCSV  -> {path}")
    if args.xlsx:
        path = write_xlsx(rows, Path(args.xlsx))
        print(f"XLSX -> {path}")

    if not args.no_save:
        run_id = store.save_run(
            rows, errors, started_at=started, finished_at=finished, trigger="cli"
        )
        print(f"snapshot -> {run_id}")

    return 0 if rows else 1


def cmd_show(args: argparse.Namespace) -> int:
    run_id = args.run_id or store.latest_run_id()
    if not run_id:
        print("no snapshots yet", file=sys.stderr)
        return 1
    rows = store.load_run(run_id)
    print(f"run {run_id} — {len(rows)} peers\n")
    for row in rows:
        print(
            f"{row.rank if row.rank else '-':>3} {row.ticker:<8} "
            f"{(row.company or '')[:28]:<28} score={_fmt(row.score_overall)} "
            f"P/S={_fmt(row.price_to_sales, 'mult')} ROE={_fmt(row.roe, 'pct')}"
        )
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    runs = store.list_runs(args.limit)
    if not runs:
        print("no snapshots yet")
        return 0
    print(f"{'run_id':<26} {'started':<20} {'rows':>5} {'errors':>7} {'trigger':<10}")
    for run in runs:
        print(
            f"{run['run_id']:<26} {run['started_at']:<20} {run['row_count']:>5} "
            f"{run['error_count']:>7} {run['trigger']:<10}"
        )
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=args.host or settings.host,
        port=args.port or settings.port,
        reload=args.reload,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="finance-ranker", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_refresh = sub.add_parser("refresh", help="fetch and rank a peer set")
    p_refresh.add_argument("tickers", nargs="*", help="ticker symbols")
    p_refresh.add_argument("--default", action="store_true", help="use FR_DEFAULT_TICKERS")
    p_refresh.add_argument("--csv", help="write a CSV to this path")
    p_refresh.add_argument("--xlsx", help="write an XLSX to this path")
    p_refresh.add_argument("--no-save", action="store_true", help="skip the DB snapshot")
    p_refresh.set_defaults(func=cmd_refresh)

    p_show = sub.add_parser("show", help="print a stored snapshot")
    p_show.add_argument("run_id", nargs="?")
    p_show.set_defaults(func=cmd_show)

    p_runs = sub.add_parser("runs", help="list stored snapshots")
    p_runs.add_argument("--limit", type=int, default=20)
    p_runs.set_defaults(func=cmd_runs)

    p_serve = sub.add_parser("serve", help="run the API + web UI")
    p_serve.add_argument("--host")
    p_serve.add_argument("--port", type=int)
    p_serve.add_argument("--reload", action="store_true")
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
