"""FastAPI application: refresh, progress, ranking and one-click export."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import cache, store
from app.config import settings
from app.exporters.csv_export import rows_to_csv, write_csv
from app.exporters.detail_export import detail_rows_to_csv, find_row
from app.exporters.excel_export import write_xlsx
from app.jobs import manager
from app.models import MetricRow

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# Resolved against the bundle root when frozen so PyInstaller's --add-data
# payload is found; `__file__` would point inside the temporary unpack dir.
from app.config import BUNDLE_DIR

STATIC_DIR = Path(__file__).parent / "web" / "static"
if not STATIC_DIR.is_dir():
    STATIC_DIR = BUNDLE_DIR / "app" / "web" / "static"

app = FastAPI(
    title="FinanceRanker",
    description="Free public-data technology peer ranker (SEC XBRL + Yahoo annual + akshare)",
    version="0.1.1",
)


# --------------------------------------------------------------------------- #
# lifecycle
# --------------------------------------------------------------------------- #
@app.on_event("startup")
async def _startup() -> None:
    store.init_db()
    # Probe providers in the background: it must not delay serving, but the
    # first refresh should already know what this network can reach.
    async def _probe() -> None:
        try:
            from app.health import results as probe_results

            await asyncio.to_thread(probe_results)
        except Exception as exc:  # pragma: no cover - never fatal
            log.info("provider probe skipped: %s", exc)

    asyncio.create_task(_probe())
    try:
        from app.scheduler import start_scheduler

        start_scheduler()
    except Exception as exc:  # scheduler is optional
        log.warning("scheduler not started: %s", exc)
    log.info("FinanceRanker ready on http://%s:%s", settings.host, settings.port)


@app.on_event("shutdown")
async def _shutdown() -> None:
    try:
        from app.scheduler import shutdown_scheduler

        shutdown_scheduler()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# ranked rows helper
# --------------------------------------------------------------------------- #
async def _resolve_rows(
    job_id: str | None, run_id: str | None
) -> tuple[list[MetricRow], dict[str, str]]:
    """Resolve which rows to show, preferring the newest data available.

    Order of preference:
      1. an explicitly requested run or job (the caller knows what it wants);
      2. an in-flight refresh, so a running job's partial rows are visible;
      3. the newest **stored snapshot** — not this process's last job.

    Step 3 is the important one. The snapshot directory is shared, so a refresh
    performed by another instance (or by the scheduler) is newer than anything
    this process remembers. Returning a finished in-memory job here is how a
    screen ends up showing yesterday's figures next to a newly added metric:
    the job's rows predate the columns it is being displayed in.
    """
    if run_id:
        rows = store.load_run(run_id)
        if not rows:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        return rows, {}

    if job_id:
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        if job.rows:
            return job.rows, job.errors
        raise HTTPException(status_code=409, detail=f"job {job_id} has no rows yet")

    active = manager.running()
    if active is not None and active.rows:
        return active.rows, active.errors

    latest = store.latest_run_id()
    if latest:
        rows = store.load_run(latest)
        if rows:
            return rows, {}

    # Nothing stored: fall back to this process's most recent finished job.
    finished = manager.freshest()
    if finished is not None:
        return finished.rows, finished.errors
    raise HTTPException(status_code=404, detail="no refresh data yet — run a refresh first")


def _export_paths() -> tuple[Path, Path]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (
        settings.export_dir / f"tech_ranker_{stamp}.csv",
        settings.export_dir / f"tech_ranker_{stamp}.xlsx",
    )


# --------------------------------------------------------------------------- #
# API — meta
# --------------------------------------------------------------------------- #
@app.get("/api/health")
async def health() -> dict:
    from app.health import results as probe_results, yahoo_usable

    probe = probe_results()
    return {
        "status": "ok",
        "time": datetime.now().isoformat(),
        "providers": {
            "fundamentals": "SEC XBRL -> Yahoo annual (aligned missing fields) -> akshare",
            "prices": "Sina -> akshare/Eastmoney -> Yahoo",
            "quotes": "Tencent -> Eastmoney -> Yahoo",
            "yahoo_mode": settings.yahoo_mode,
            "yahoo_in_use": yahoo_usable(),
        },
        "probe": probe,
        "store": store.stats(),
        "cache": cache.stats(),
    }


@app.get("/api/config")
async def get_config() -> dict:
    return {
        "default_tickers": settings.ticker_list,
        "max_concurrency": settings.max_concurrency,
        "weights": {
            "growth": settings.w_growth,
            "profitability": settings.w_profitability,
            "cash": settings.w_cash,
            "valuation": settings.w_valuation,
            "market": settings.w_market,
        },
    }


# --------------------------------------------------------------------------- #
# API — refresh
# --------------------------------------------------------------------------- #
@app.post("/api/refresh")
async def refresh(payload: dict | None = None) -> dict:
    payload = payload or {}
    raw = payload.get("tickers")
    if raw is None:
        tickers = settings.ticker_list
    elif isinstance(raw, str):
        tickers = [t for t in raw.replace("\n", ",").split(",") if t.strip()]
    else:
        tickers = [str(t) for t in raw]

    current = manager.current()
    if current and current.status in ("queued", "running"):
        raise HTTPException(
            status_code=409, detail=f"a refresh is already running (job {current.job_id})"
        )

    tickers = [t.strip().upper() for t in tickers if t and t.strip()]
    if not tickers:
        raise HTTPException(status_code=400, detail="no tickers provided")
    if len(tickers) > 100:
        raise HTTPException(status_code=400, detail="too many tickers (max 100)")

    job = manager.start(tickers, trigger="manual")
    return {"job_id": job.job_id, "tickers": tickers, "total": len(tickers)}


@app.get("/api/jobs")
async def list_jobs(limit: int = 20) -> dict:
    return {"jobs": manager.list_jobs(limit)}


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str) -> dict:
    job = manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job.snapshot()


@app.get("/api/jobs/{job_id}/stream")
async def job_stream(job_id: str, request: Request) -> StreamingResponse:
    """Server-sent events for live progress.

    Deliberately lock-free and never blocks: `Job.events_since` / `snapshot`
    only touch CPython-atomic structures, so the event loop keeps serving other
    requests (including `/api/health`) while a refresh runs. Heartbeat comments
    keep intermediaries from closing an idle connection.
    """
    job = manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")

    async def event_source():
        cursor = 0
        idle = 0
        # Send the current state immediately so the UI never shows a blank bar.
        yield f"data: {json.dumps({'kind': 'state', **job.snapshot()}, ensure_ascii=False)}\n\n"
        while True:
            if await request.is_disconnected():
                return
            events, cursor = job.events_since(cursor)
            for event in events:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                idle = 0
            if job.is_finished:
                yield f"data: {json.dumps({'kind': 'close', **job.snapshot()}, ensure_ascii=False)}\n\n"
                return
            idle += 1
            if idle % 15 == 0:  # ~10s of quiet: keep the connection warm
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.65)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --------------------------------------------------------------------------- #
# API — ranking
# --------------------------------------------------------------------------- #
@app.get("/api/ranking")
async def ranking(
    job_id: str | None = None,
    run_id: str | None = None,
    eligible_only: bool = False,
) -> dict:
    rows, errors = await _resolve_rows(job_id, run_id)
    if eligible_only:
        rows = [r for r in rows if r.rank_eligible]
    return {
        "count": len(rows),
        "errors": errors,
        "staleness": _staleness(rows),
        "metrics_version": _metrics_version(),
        "rows": [r.model_dump(mode="json") for r in rows],
    }


def _metrics_version() -> int:
    from app.engine.scoring import METRICS_VERSION

    return METRICS_VERSION


def _staleness(rows: list[MetricRow]) -> dict:
    """Whether the rows being shown were produced by the current metric set.

    Without this, a snapshot written by an older build renders as a screen full
    of blanks that looks exactly like a broken data source. Reporting the
    mismatch turns a silent nothing into an instruction: refresh.
    """
    if not rows:
        return {"stale": False, "reason": None, "missing_metrics": [],
                "age_hours": None, "stale_after_hours": settings.stale_after_hours}

    from app.engine.scoring import METRICS, METRICS_VERSION

    versions = {r.metrics_version for r in rows}
    ages = [
        (datetime.now() - r.fetched_at).total_seconds() / 3600.0
        for r in rows
        if r.fetched_at is not None
    ]
    age = round(min(ages), 2) if ages else None
    base = {"stale_after_hours": settings.stale_after_hours, "age_hours": age,
            "missing_metrics": []}

    # Which columns no row in this snapshot carries at all.
    missing = sorted(
        attr for attr, _, _ in METRICS
        if all(getattr(r, attr, None) is None for r in rows)
    )
    stale_shape = any(v < METRICS_VERSION for v in versions) and bool(missing)
    if stale_shape:
        return {**base, "stale": True, "reason": "older_metrics", "missing_metrics": missing}

    if age is not None and age > settings.stale_after_hours:
        return {**base, "stale": True, "reason": "old_snapshot", "missing_metrics": missing}
    return {**base, "stale": False, "reason": None, "missing_metrics": missing}


@app.get("/api/metrics")
async def metrics_catalog() -> dict:
    """Every displayed metric, its component and whether it is scored.

    The company detail view builds its blocks from this instead of a local list,
    so a metric can never appear in the UI without the backend agreeing on how
    (or whether) it is scored. That drift is what produced rows with a blank
    score column and no explanation.
    """
    from app.engine.scoring import METRICS, METRICS_VERSION, metric_catalog

    return {
        "metrics_version": METRICS_VERSION,
        "scored_count": len(METRICS),
        "metrics": metric_catalog(),
    }


@app.get("/api/ticker/{ticker}")
async def ticker_detail(ticker: str) -> dict:
    rows, _ = await _resolve_rows(None, None)
    match = next((r for r in rows if r.ticker == ticker.upper()), None)
    if not match:
        raise HTTPException(status_code=404, detail=f"{ticker} not in the current screen")
    return match.model_dump(mode="json")


@app.get("/api/insights/{ticker}")
async def company_insights(ticker: str, job_id: str | None = None, run_id: str | None = None) -> dict:
    from app.pipeline import validate_ticker
    from app.health import yahoo_usable
    from app.providers.prices import get_price_history
    from app.providers.news import get_news
    from app.providers.earnings import get_earnings_event
    from app.insights import build_insights

    try:
        symbol = validate_ticker(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rows, _ = await _resolve_rows(job_id, run_id)
    row = next((r for r in rows if r.ticker == symbol), None)
    if row is None:
        raise HTTPException(status_code=404, detail="ticker not in this snapshot")
    try:
        history = await asyncio.to_thread(get_price_history, symbol, allow_yahoo=yahoo_usable())
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"daily prices unavailable: {exc}") from exc
    try:
        news = await asyncio.wait_for(asyncio.to_thread(get_news, symbol, row.company), timeout=8)
    except Exception:
        news = None
    earnings = None
    if yahoo_usable():
        try:
            earnings = await asyncio.wait_for(asyncio.to_thread(get_earnings_event, symbol), timeout=7)
        except Exception:
            pass
    return build_insights(row, history, news, earnings)


# --------------------------------------------------------------------------- #
# API — history
# --------------------------------------------------------------------------- #
@app.get("/api/runs")
async def runs(limit: int = 50) -> dict:
    return {"runs": store.list_runs(limit)}


@app.get("/api/runs/{run_id}")
async def run_rows(run_id: str) -> dict:
    rows = store.load_run(run_id)
    if not rows:
        raise HTTPException(status_code=404, detail="run not found")
    return {"run_id": run_id, "count": len(rows), "rows": [r.model_dump(mode="json") for r in rows]}


@app.delete("/api/runs/{run_id}")
async def run_delete(run_id: str) -> dict:
    if not store.delete_run(run_id):
        raise HTTPException(status_code=404, detail="run not found")
    return {"deleted": run_id}


@app.get("/api/history/{ticker}")
async def history(ticker: str, limit: int = 60) -> dict:
    return {"ticker": ticker.upper(), "history": store.ticker_history(ticker, limit)}


# --------------------------------------------------------------------------- #
# API — export (one-click CSV / Excel)
# --------------------------------------------------------------------------- #
@app.get("/api/export/csv")
async def export_csv(
    job_id: str | None = None,
    run_id: str | None = None,
    download: bool = True,
) -> Response:
    rows, _ = await _resolve_rows(job_id, run_id)
    text = rows_to_csv(rows)
    if download:
        csv_path, _ = _export_paths()
        csv_path.write_text(text, encoding="utf-8-sig")
        return FileResponse(
            csv_path,
            media_type="text/csv",
            filename=csv_path.name,
        )
    return Response(content=text, media_type="text/csv; charset=utf-8")


@app.get("/api/export/xlsx")
async def export_xlsx(job_id: str | None = None, run_id: str | None = None) -> FileResponse:
    rows, _ = await _resolve_rows(job_id, run_id)
    _, xlsx_path = _export_paths()
    write_xlsx(rows, xlsx_path)
    return FileResponse(
        xlsx_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=xlsx_path.name,
    )


@app.get("/api/export/detail")
async def export_detail(
    ticker: str,
    job_id: str | None = None,
    run_id: str | None = None,
    raw: bool = False,
) -> Response:
    """Single-company review CSV, mirroring the workbook's `Stock Detail` sheet.

    Blocks: Meta / Dimension scores / Metrics (with peer median + within-group
    rank + score) / Calculation inputs.
    """
    rows, _ = await _resolve_rows(job_id, run_id)
    row = find_row(rows, ticker)
    if row is None:
        raise HTTPException(status_code=404, detail=f"{ticker.upper()} not in the current screen")

    text = detail_rows_to_csv(rows, row)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if raw:
        return Response(content=text, media_type="text/csv; charset=utf-8")
    name = f"tech_ranker_{row.ticker}_detail_{stamp}.csv"
    path = settings.export_dir / name
    path.write_text(text, encoding="utf-8-sig")
    return FileResponse(path, media_type="text/csv", filename=name)


@app.post("/api/export/both")
async def export_both(
    background: BackgroundTasks,
    job_id: str | None = None,
    run_id: str | None = None,
) -> dict:
    rows, _ = await _resolve_rows(job_id, run_id)
    csv_path, xlsx_path = _export_paths()
    write_csv(rows, csv_path)
    write_xlsx(rows, xlsx_path)
    return {
        "csv": str(csv_path),
        "xlsx": str(xlsx_path),
        "csv_name": csv_path.name,
        "xlsx_name": xlsx_path.name,
        "rows": len(rows),
    }


# --------------------------------------------------------------------------- #
# API — maintenance
# --------------------------------------------------------------------------- #
@app.post("/api/cache/clear")
async def cache_clear(namespace: str | None = None) -> dict:
    return {"removed": cache.clear(namespace)}


# --------------------------------------------------------------------------- #
# API — US ticker universe + akshare parameter preview
# --------------------------------------------------------------------------- #
@app.get("/api/us/tickers")
async def us_tickers(q: str = "", limit: int = 40) -> dict:
    """Search the US ticker universe for the picker (US equities only)."""
    from app import universe

    limit = max(1, min(limit, 200))
    results = await asyncio.to_thread(universe.search, q, limit)
    return {
        "query": q,
        "count": len(results),
        "tickers": [t.model_dump() for t in results],
    }


@app.get("/api/us/params")
async def us_param_catalog(lang: str = "zh") -> dict:
    """The static parameter catalogue: what the preview can compute, and how."""
    from app.preview import CATALOG, CATEGORY_EN, CATEGORY_TITLES, EXTRA_EN, EXTRA_RATIOS, PARAM_EN

    def param_dump(spec) -> dict:
        row = spec.model_dump()
        if lang == "en" and spec.key in PARAM_EN:
            label, formula, note = PARAM_EN[spec.key]
            row.update(label=label, formula=formula, note=note)
        return row

    return {
        "categories": CATEGORY_EN if lang == "en" else CATEGORY_TITLES,
        "parameters": [param_dump(spec) for spec in CATALOG],
        "extra_ratios": [
            {
                "key": key,
                "label": (EXTRA_EN.get(key, (label, ""))[0] if lang == "en" else label),
                "unit": unit,
                "why": (EXTRA_EN.get(key, (label, ""))[1] if lang == "en" else why),
            }
            for key, label, unit, why in EXTRA_RATIOS
        ],
    }


@app.get("/api/us/preview/{ticker}")
async def us_preview(ticker: str, price_points: int = 260, lang: str = "zh") -> dict:
    """Derive the workbook's parameters for one US ticker from akshare data.

    Runs in a worker thread: the statements plus the full daily history are
    sizeable downloads whose parsing must not stall the event loop.
    """
    from app.preview import CATEGORY_TITLES, build_preview
    from app.providers.akshare_us import AkshareUnavailable

    symbol = ticker.upper().strip()
    if not symbol or len(symbol) > 12:
        raise HTTPException(status_code=400, detail="invalid ticker")
    price_points = max(30, min(price_points, 1200))

    try:
        payload = await asyncio.to_thread(build_preview, symbol, price_points, lang)
    except AkshareUnavailable as exc:
        raise HTTPException(status_code=502, detail=f"akshare 数据不可用: {exc}") from exc
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("preview failed for %s", symbol)
        raise HTTPException(status_code=500, detail=f"构建参数失败: {exc}") from exc

    data = payload.model_dump(mode="json")
    data["category_titles"] = CATEGORY_TITLES
    return data


# --------------------------------------------------------------------------- #
# API — provider reachability
# --------------------------------------------------------------------------- #
@app.get("/api/providers")
async def providers(force: bool = False) -> dict:
    """Which data sources are reachable from *this* machine right now."""
    from app.health import results as probe_results

    return await asyncio.to_thread(probe_results, force=force)


@app.post("/api/providers/probe")
async def providers_probe() -> dict:
    from app.health import results as probe_results

    return await asyncio.to_thread(probe_results, force=True)


# --------------------------------------------------------------------------- #
# API — US sector classification and sector-relative ranking
# --------------------------------------------------------------------------- #
@app.get("/api/sectors")
async def list_sectors() -> dict:
    """The module list, with how many peers are currently resolvable."""
    from app import sectors as sector_mod

    coverage = sector_mod.index_coverage()
    return {
        "sectors": [
            {
                "key": key, "label": zh, "label_en": en,
                "seeded": len(sector_mod.sector_candidates(key, limit=1000)),
                "indexed": coverage.get(key, 0),
            }
            for key, zh, en in sector_mod.SECTORS
        ],
        "index_size": sector_mod.index_size(),
    }


@app.get("/api/sectors/resolve/{ticker}")
async def resolve_sector(ticker: str) -> dict:
    """Which module a ticker belongs to, from its SEC SIC code."""
    from app import sectors as sector_mod

    item = await asyncio.to_thread(sector_mod.get_company_sector, ticker)
    if item is None:
        raise HTTPException(
            status_code=404,
            detail=f"无法为 {ticker.upper()} 取得 SEC 行业分类（可能不在 SEC 名录内）",
        )
    if item.sector:
        await asyncio.to_thread(sector_mod.remember, item)
    zh, en = sector_mod.SECTOR_LABELS.get(item.sector or "", ("未分类", "Unclassified"))
    return {
        "ticker": item.ticker, "cik": item.cik, "sic": item.sic,
        "sic_description": item.sic_description, "sector": item.sector,
        "sector_label": zh, "sector_label_en": en,
        "company": item.name, "exchange": item.exchange,
    }


@app.get("/api/sectors/{sector}/ranking")
async def sector_ranking(
    sector: str, pool_size: int = 40, top: int = 10, focus: str | None = None
) -> dict:
    """Rank the module's peers against each other and return the top `top`.

    Scoring is cross-sectional, so the whole pool is fetched and scored in one
    pass; asking for a per-ticker score would be meaningless.
    """
    from app import sectors as sector_mod

    key = sector.strip().lower()
    if key not in sector_mod.SECTOR_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"未知板块 {sector!r}；可选：{', '.join(sector_mod.SECTOR_KEYS)}",
        )
    try:
        return await asyncio.to_thread(
            sector_mod.rank_sector, key,
            pool_size=max(4, min(pool_size, 80)),
            top=max(1, min(top, 50)),
            focus=focus,
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("sector ranking failed for %s", key)
        raise HTTPException(status_code=500, detail=f"板块排名失败: {exc}") from exc


@app.post("/api/sectors/build")
async def sector_build(payload: dict | None = None) -> dict:
    """Pre-warm the sector index from the SEC (opt-in; it is a slow crawl)."""
    from app import sectors as sector_mod

    payload = payload or {}
    limit = max(0, min(int(payload.get("limit") or 0), 12000))
    result = await asyncio.to_thread(
        sector_mod.build_index, payload.get("tickers"), limit=limit
    )
    return result


# --------------------------------------------------------------------------- #
# static UI
# --------------------------------------------------------------------------- #
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
