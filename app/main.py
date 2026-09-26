"""FastAPI application: refresh, progress, ranking and one-click export."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import date as _date
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


# --------------------------------------------------------------------------- #
# static asset freshness
# --------------------------------------------------------------------------- #
# Browsers cache JS and CSS aggressively. The app is deployed by copying new
# files over old ones and serves on a stable localhost port, so without this a
# reload after an upgrade keeps running the *previous* build's scripts — which is
# exactly how a page ends up calling an API method that no longer exists and
# rendering raw i18n keys, while the files on disk are perfectly correct.
#
# The fix is the standard one: HTML is never cached and references its assets
# with a version query, so a change to any asset produces a new URL the browser
# has never seen. Asset URLs are then safe to cache indefinitely.
_ASSET_STAMP: str | None = None


def asset_stamp() -> str:
    """A short hash over the static assets, used to version their URLs.

    Derived from name, size and mtime rather than file contents: hashing ~1 MB on
    every request would be wasteful, and a rebuild always changes mtime. Cached
    after the first call.
    """
    global _ASSET_STAMP
    if _ASSET_STAMP is None:
        digest = hashlib.sha256()
        try:
            for path in sorted(STATIC_DIR.rglob("*")):
                if path.suffix.lower() not in (".js", ".css") or not path.is_file():
                    continue
                stat = path.stat()
                digest.update(f"{path.relative_to(STATIC_DIR)}:{stat.st_size}:{stat.st_mtime_ns}".encode())
        except OSError:  # pragma: no cover - depends on the filesystem
            pass
        _ASSET_STAMP = digest.hexdigest()[:10]
    return _ASSET_STAMP


_ASSET_REF = re.compile(r'(?P<attr>\b(?:src|href))="(?P<path>(?!https?:|//|data:|mailto:|#)'
                        r'[^"?]+?\.(?:js|css))"')


class VersionedStaticFiles(StaticFiles):
    """StaticFiles that keeps HTML fresh and versions the assets it references.

    StaticFiles would otherwise answer with an ETag and a `Last-Modified` but no
    `Cache-Control`, leaving the browser free to reuse a stale script without
    revalidating — the failure this class exists to prevent.
    """

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        content_type = response.headers.get("content-type", "")

        if "text/html" in content_type:
            # FileResponse streams from disk rather than exposing a body, so the
            # file is read directly and re-served with rewritten asset URLs.
            # A directory request resolves to index.html, which `path` does not
            # name, so that case is mapped before falling back to the original.
            target = (STATIC_DIR / path)
            if target.is_dir():
                target = target / "index.html"
            try:
                source = target.resolve()
                source.relative_to(Path(STATIC_DIR).resolve())
                text = source.read_text(encoding="utf-8")
            except (OSError, ValueError):
                # Unreadable or outside the static root: serve the original.
                return response
            stamp = asset_stamp()
            text = _ASSET_REF.sub(
                lambda m: f'{m.group("attr")}="{m.group("path")}?v={stamp}"', text)
            # The stamp is also exposed as a meta tag. A page cached from an earlier
            # build carries an older stamp, which lets the scripts notice they were
            # loaded by stale markup and reload once — the browser cache otherwise
            # keeps serving an old nav and old renderers indefinitely.
            text = text.replace(
                "</head>",
                f'<meta name="fr-build" content="{stamp}" /></head>', 1)
            headers = dict(response.headers)
            headers.pop("content-length", None)
            # `no-store` alone was not enough. A browser that had already cached a
            # page from an earlier build kept serving it — which is how a removed
            # navigation entry and a deleted renderer both survived several
            # deploys. The extra directives plus a zero expiry cover the aggressive
            # and legacy caches that ignore `no-store` on their own.
            headers["cache-control"] = (
                "no-store, no-cache, must-revalidate, max-age=0, private"
            )
            headers["expires"] = "0"
            headers["pragma"] = "no-cache"
            return Response(content=text.encode("utf-8"), status_code=response.status_code,
                            headers=headers, media_type="text/html")

        if any(t in content_type for t in ("javascript", "text/css")):
            # A *versioned* URL is safe to keep indefinitely: the stamp changes
            # whenever the file does, so the browser can never be holding the
            # wrong bytes for that URL.
            #
            # An *unversioned* URL is not. A page served from an older build — or
            # a browser that cached the HTML before stamping existed — requests
            # `/js/options.js` with no query string. Serving that immutably pinned
            # a stale script in the browser for a year, which is how a build whose
            # i18n keys no longer matched kept rendering raw keys like
            # `op.th.nearFlow` long after the file on disk had been fixed. Such a
            # request is answered `no-store` so it always revalidates.
            query = (scope.get("query_string") or b"").decode("latin-1")
            if "v=" in query:
                response.headers["cache-control"] = "public, max-age=31536000, immutable"
            else:
                response.headers["cache-control"] = "no-store, must-revalidate"
        return response


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
    from app.engine.scoring import default_strategy

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
        # When these differ from the balanced preset they become the default
        # blend, so a configured `FR_W_*` set is honoured rather than ignored.
        "default_strategy": default_strategy(),
        "risk_free_rate": settings.risk_free_rate,
    }


# --------------------------------------------------------------------------- #
# API — refresh
# --------------------------------------------------------------------------- #
@app.post("/api/refresh")
async def refresh(payload: dict | None = None) -> dict:
    payload = payload or {}
    raw = payload.get("tickers")
    if raw is None:
        # Reuse the universe the last run actually used rather than falling back to
        # the configured default. A refresh with no explicit list would otherwise
        # silently shrink a hand-picked pool back to the default, which is how a
        # 15-name pool became 13 and lost two companies from the ranking without
        # anything saying so.
        previous: list[str] = []
        try:
            recent = store.list_runs(limit=1)
            if recent:
                # `list_runs` may hand back a model or a plain dict depending on the
                # caller, so both shapes are read rather than assuming one.
                first = recent[0]
                recorded = (first.get("tickers") if isinstance(first, dict)
                            else getattr(first, "tickers", "")) or ""
                previous = [
                    t.strip().upper()
                    for t in str(recorded).split(",")
                    if t.strip()
                ]
        except Exception:  # noqa: BLE001 - fall back to the configured default
            previous = []
        tickers = previous if len(previous) > 1 else settings.ticker_list
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
@app.get("/api/strategies")
async def strategies() -> dict:
    """The selectable weight profiles for re-scoring a ranking.

    Exposed so the UI never hardcodes a weight again: the blend is a stated
    choice, and changing it re-ranks the same percentile scores rather than
    recomputing the evidence. Includes a `custom` profile built from the
    `FR_W_*` settings so a configured blend is selectable rather than ignored.
    """
    from app.engine.scoring import all_presets, default_strategy

    presets = all_presets()
    return {
        "default": default_strategy(),
        "strategies": [
            {"key": key, "label": preset["label"], "blurb": preset["blurb"],
             "weights": preset["weights"],
             "differs_from_default": bool(preset.get("differs_from_default"))}
            for key, preset in presets.items()
        ],
    }


@app.get("/api/ranking")
async def ranking(
    job_id: str | None = None,
    run_id: str | None = None,
    eligible_only: bool = False,
    strategy: str | None = None,
) -> dict:
    rows, errors = await _resolve_rows(job_id, run_id)
    # Re-score on the requested blend: percentile scores are independent of the
    # weights, so this re-ranks the stored evidence instead of refetching it.
    from app.engine.scoring import default_strategy, preset_weights, score_peers

    active = (strategy or default_strategy()).strip().lower()
    if rows:
        score_peers(rows, strategy=active)
    if eligible_only:
        rows = [r for r in rows if r.rank_eligible]
    return {
        "count": len(rows),
        "errors": errors,
        "staleness": _staleness(rows),
        "metrics_version": _metrics_version(),
        "strategy": active,
        "weights": preset_weights(active),
        "rows": [_row_payload(r) for r in rows],
    }


def _row_payload(row: MetricRow) -> dict:
    """One row as the API ships it.

    `non_gaap` is deliberately withheld. It is the *annual* bridge, and the company
    page now derives its reconciliation per quarter from `/api/eps`, where the
    period is period-specific and labelled. A page running older JavaScript would
    render the withheld annual block as an unlabelled "GAAP to adjusted EPS
    reconciliation" — which is exactly the confusion this removes, and which
    survived a deploy because the browser kept the old script. Withholding the
    field means the stale renderer draws nothing rather than drawing something
    wrong.
    """
    payload = row.model_dump(mode="json")
    payload.pop("non_gaap", None)
    return payload


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

    from app.engine.scoring import METRICS, METRICS_VERSION, metric_catalog

    versions = {r.metrics_version for r in rows}
    ages = [
        (datetime.now() - r.fetched_at).total_seconds() / 3600.0
        for r in rows
        if r.fetched_at is not None
    ]
    age = round(min(ages), 2) if ages else None
    base = {"stale_after_hours": settings.stale_after_hours, "age_hours": age,
            "missing_metrics": []}

    # Which columns no row in this snapshot carries at all, across the whole
    # displayed catalogue rather than just the scored metrics: the non-GAAP
    # bridge columns are reported but not scored, and a snapshot predating them
    # would otherwise pass unremarked and render blank.
    displayed = [entry["attr"] for entry in metric_catalog()]
    missing = sorted(
        attr for attr in displayed
        if all(getattr(r, attr, None) is None for r in rows)
    )

    # Distinguish a stale *shape* from a merely sparse row. A couple of genuinely
    # unavailable figures are normal — a negative free cash flow, a filer that
    # tags no share-based compensation — and flagging those would cry wolf on
    # healthy data. A snapshot written before the current metric set is missing a
    # large block of columns at once, which is what this ratio detects.
    absent_ratio = len(missing) / max(1, len(displayed))

    # A snapshot older than the current metric set is detected by what it is
    # missing, not only by a version inequality: relying on the number alone
    # meant a forgotten bump hid the gap completely, with the version matching
    # while the columns were absent.
    outdated = any(v < METRICS_VERSION for v in versions) or absent_ratio >= 0.25
    if outdated and missing:
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


@app.get("/api/analyst/{ticker}")
async def analyst_detail(ticker: str) -> dict:
    """Sell-side ratings, published target changes, earnings surprises and estimates.

    Enrichment, not a scored input: none of it feeds the peer ranking. It is
    fetched on demand so adding a company to the comparison pool costs one
    request rather than one per section.
    """
    from app.pipeline import validate_ticker
    from app.health import yahoo_usable
    from app.providers.yahoo_analyst import get_analyst_detail

    try:
        symbol = validate_ticker(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    detail = await asyncio.to_thread(get_analyst_detail, symbol, allow_yahoo=yahoo_usable())
    if detail is None:
        return {
            "ticker": symbol, "available": False,
            "reason": "Yahoo has no analyst coverage for this symbol, or the "
                      "crumb-authenticated endpoint is unreachable",
        }
    payload = detail.model_dump(mode="json")
    payload["available"] = True

    # Attach the adjusted figure to each reported quarter, because the feed's
    # `eps_actual` and `eps_estimate` are not always the same measure: for Alphabet
    # the "actual" is GAAP diluted EPS (9.11) while the estimate is a non-GAAP
    # forecast (2.90), so the surprise chart read "+214%" for a quarter that, on a
    # like-for-like basis, missed. Publishing the adjusted figure alongside lets the
    # chart compare like with like instead of repeating that.
    try:
        from app.quarterly import quarterly_eps

        quarters = await asyncio.to_thread(quarterly_eps, symbol, quarters=8, analyst=detail)
        # Index by the calendar month the period ends in, then match conservatively.
        # A month-only match mis-joined Qualcomm — its March 2026 quarter was given
        # the adjusted figure of 6.88 belonging to a different period, turning a
        # 3.3% beat into 168%. Where the match is not confident the field is left
        # empty, so the chart falls back to the feed's own pair rather than
        # plotting a figure from the wrong quarter.
        by_month: dict[tuple[int, int], object] = {}
        for quarter in quarters:
            try:
                when = _date.fromisoformat(quarter.end)
            except (ValueError, TypeError):
                continue
            by_month[(when.year, when.month)] = quarter

        for item in payload.get("earnings_history") or []:
            try:
                when = _date.fromisoformat(str(item.get("quarter_end"))[:10])
            except (ValueError, TypeError):
                continue
            # A fiscal period ending 2026-06-28 is the June quarter; one ending
            # 2026-07-26 is the July quarter. Only the same year and the same or
            # the immediately preceding month count as the same period.
            previous = when.month - 1 if when.month > 1 else 12
            match = (by_month.get((when.year, when.month))
                     or by_month.get((when.year, previous)))
            if match is None or match.adjusted_eps is None:
                continue

            # Attach only when the two sources clearly describe the same quarter.
            # A month match alone still mis-joined Qualcomm: its March 2026 filings
            # give a GAAP EPS of 6.88 while the feed reports an actual of 2.65 for
            # the same month, so the periods are not the same measurement and
            # enriching one with the other turned a 3.3% beat into 168%.
            #
            # The test is whether the feed's own actual agrees with this site's GAAP
            # figure. When it does the two are describing one quarter, so the
            # adjusted figure belongs to it. When it does not, the field is left
            # empty and the chart falls back to the feed's own actual/estimate pair,
            # which is internally consistent by construction.
            feed_actual = item.get("eps_actual")
            gaap = match.gaap_eps
            if feed_actual is None or gaap is None:
                continue
            tolerance = max(0.05, abs(gaap) * 0.05)
            if abs(feed_actual - gaap) > tolerance:
                continue

            item["adjusted_eps"] = match.adjusted_eps
            item["gaap_eps"] = gaap
    except Exception as exc:  # noqa: BLE001 - the chart falls back to the feed's pair
        log.debug("could not attach adjusted EPS to analyst history: %s", exc)

    return payload


@app.get("/api/eps/{ticker}")
async def eps_quarters(ticker: str, quarters: int = 4) -> dict:
    """Per-quarter GAAP and adjusted EPS, with the bridge behind each quarter.

    Separate from the ranking payload because it costs a companyfacts fetch, and
    separate from the annual bridge in `/api/metrics` because the two are
    different measurements: the annual figure divides a year of adjustments by a
    year of shares, which hides a quarter that carried the whole year.
    """
    from app.pipeline import validate_ticker
    from app.quarterly import quarterly_bridge, quarterly_eps
    from app.providers.yahoo_analyst import get_analyst_detail
    from app.health import yahoo_usable

    try:
        symbol = validate_ticker(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    span = max(1, min(int(quarters or 4), 12))

    # The analyst feed is fetched first and passed in, because it serves two
    # purposes: it supplies consensus for the SEC-sourced quarters, and it *is* the
    # source for a filer with no quarterly XBRL facts (a foreign private issuer files
    # a 6-K, not a 10-Q, so TSMC has no quarterly durations at all).
    detail = None
    if yahoo_usable():
        try:
            # `allow_yahoo=True` is required: without it the call returns nothing and
            # the consensus join silently matched no quarter.
            detail = await asyncio.to_thread(
                get_analyst_detail, symbol, allow_yahoo=True)
        except Exception:  # noqa: BLE001 - the surprise columns degrade
            detail = None

    rows = await asyncio.to_thread(
        quarterly_eps, symbol, quarters=span, analyst=detail)
    bridge = await asyncio.to_thread(quarterly_bridge, symbol)

    # Join consensus to the quarter. An exact date match is right for a calendar
    # filer, but a company with an offset fiscal calendar ends its quarter on
    # whatever weekday the period closes — NVIDIA's 2026 Q3 ended 2026-07-26 and
    # Apple's 2026 Q2 ended 2026-06-27, while the analyst feed dates the same
    # quarter by its calendar end (2026-07-31 and 2026-06-30). Matching on the date
    # alone therefore found nothing for either. A fallback matches on the calendar
    # month the fiscal period ends in, which is the quarter both sources mean.
    if rows and detail is not None:
        by_end: dict[str, object] = {}
        by_month: dict[tuple[int, int], object] = {}
        for item in list(getattr(detail, "earnings_history", None) or []):
            end = getattr(item, "quarter_end", None)
            if not end:
                continue
            stamp = str(end)[:10]
            by_end[stamp] = item
            try:
                when = end if hasattr(end, "year") else _date.fromisoformat(stamp)
            except (ValueError, TypeError):
                continue
            by_month[(when.year, when.month)] = item

        for row in rows:
            if row.consensus_eps is not None:
                continue
            match = by_end.get(str(row.end)[:10])
            if match is None:
                try:
                    when = _date.fromisoformat(str(row.end)[:10])
                except (ValueError, TypeError):
                    continue
                # The same calendar month, or the month before: a period ending
                # 2026-06-27 is the June quarter (Apple) and one ending 2026-07-26
                # is the July quarter (NVIDIA).
                previous = when.month - 1 if when.month > 1 else 12
                match = (by_month.get((when.year, when.month))
                         or by_month.get((when.year, previous)))
            if match is not None:
                estimate = getattr(match, "eps_estimate", None)
                row.consensus_eps = estimate
                if estimate not in (None, 0):
                    # The consensus is a non-GAAP forecast — measured across the
                    # pool, its level tracks the adjusted figure for seven of nine
                    # filers whose two bases differ (Alphabet's forward estimate of
                    # 2.62 against an adjusted 2.37 and a GAAP 4.85). So it is
                    # compared with the adjusted figure whenever the two bases
                    # actually differ, and with GAAP when they coincide.
                    #
                    # The earlier version paired the feed's own actual with its
                    # estimate. That is internally consistent but uses the feed's
                    # *adjusted* actual, which is not the same definition as this
                    # site's — so a quarter could read "beat" on the card while the
                    # adjusted figure shown beside it was below the estimate.
                    gaap = row.gaap_eps
                    adjusted = row.adjusted_eps
                    bases_differ = (
                        gaap is not None and adjusted is not None
                        and abs(gaap - adjusted) > abs(estimate) * 0.10
                    )
                    actual = adjusted if bases_differ else gaap
                    basis = "adjusted" if bases_differ else "gaap"
                    if actual is None:
                        actual, basis = gaap, "gaap"
                    if actual is not None:
                        row.surprise_actual = actual
                        row.surprise_basis = basis
                        row.surprise_pct = (actual - estimate) / abs(estimate)
                        # Even on the adjusted basis the figure can sit far from the
                        # consensus, which means a definition this site does not
                        # capture. Say so rather than presenting it as a clean beat.
                        if abs(actual - estimate) > abs(estimate) * 0.35:
                            row.surprise_mixed_basis = True

    if not rows:
        return {
            "ticker": symbol, "quarters": [], "bridge": None, "available": False,
            "source": "",
            "note": (
                "No quarterly EPS available for this filer: it has no quarterly XBRL "
                "facts (a foreign private issuer files a 6-K rather than a 10-Q) and "
                "the analyst feed carries no earnings history for it either. The "
                "annual bridge on the company page still applies."
            ),
        }

    sourced = rows[0].source
    note = ""
    if sourced == "analyst":
        note = (
            "Quarterly reported EPS and the consensus it was measured against, from "
            "the analyst feed. This filer does not tag its quarters in XBRL, so there "
            "are no disclosed adjustment lines to bridge — adjusted EPS is left blank "
            "rather than shown as equal to GAAP."
        )
    return {
        "ticker": symbol,
        "available": True,
        "source": sourced,
        "quarters": [q.model_dump(mode="json") for q in rows],
        "bridge": bridge.model_dump(mode="json") if bridge else None,
        "note": note,
    }


@app.get("/api/rates")
async def rates_view() -> dict:
    """The US Treasury par yield curve, the reference every return is judged against.

    A Sharpe ratio needs a risk-free rate and an earnings yield needs something to
    compare with, so this is fetched live from the Treasury's own daily feed rather
    than left as a configured constant that silently goes stale.
    """
    from app.treasury import get_yield_curve

    curve = await asyncio.to_thread(get_yield_curve)
    return {"curve": curve, "available": bool(curve)}


@app.get("/api/record/{ticker}")
async def company_record(ticker: str) -> dict:
    """A company's dated record and its catalysts, from the SEC filing index.

    Kept off the ranking payload because it costs a submissions fetch per company
    and is only needed when one company is being read. Every row is a dated filing
    that can be opened and checked; whether an event *worked* is not something a
    filing index can say, and the payload says so rather than implying otherwise.
    """
    from app.events import get_company_events
    from app.pipeline import validate_ticker
    from app.providers.fundamentals import lookup_cik
    from app.providers.yahoo_analyst import get_analyst_detail
    from app.health import yahoo_usable
    from app.trackrecord import build_catalysts, build_record

    try:
        symbol = validate_ticker(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    has_cik = lookup_cik(symbol) is not None
    events = await asyncio.to_thread(get_company_events, symbol, limit=14)

    detail = None
    if yahoo_usable():
        try:
            detail = await asyncio.to_thread(
                get_analyst_detail, symbol, allow_yahoo=True)
        except Exception:  # noqa: BLE001 - the scheduled row degrades
            detail = None

    milestones = build_record(events)
    catalysts = build_catalysts(events, detail)

    note = ""
    if not has_cik:
        note = ("This filer has no SEC CIK — a foreign private issuer files its "
                "annual report on Form 20-F and does not appear in the 8-K index, "
                "so its dated record is not available here.")
    elif not milestones and not catalysts:
        note = "No material 8-K filings in the last six years."

    return {
        "ticker": symbol,
        "has_cik": has_cik,
        "milestones": [m.model_dump(mode="json") for m in milestones],
        "catalysts": [c.model_dump(mode="json") for c in catalysts],
        "note": note,
    }


@app.get("/api/options/{ticker}")
async def options_view(ticker: str, expiries: int = 6, refresh: bool = False) -> dict:
    """Option-market positioning for one ticker.

    On demand and cached: a chain costs several requests, and nothing in the peer
    ranking depends on it. `expiries` caps how many chains are read; `refresh`
    bypasses the cache after a fetch.
    """
    from app.pipeline import validate_ticker
    from app.health import yahoo_usable
    from app.options import build_options_snapshot
    from app.providers.yahoo_options import MAX_EXPIRIES

    try:
        symbol = validate_ticker(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not yahoo_usable():
        return {
            "ticker": symbol, "available": False,
            "reason": "the option chain requires Yahoo Finance, which the last "
                      "provider probe could not reach",
        }

    expiries = max(1, min(expiries, MAX_EXPIRIES))
    if refresh:
        from app import cache

        cache.clear("options_yahoo")

    # Prefer the screened spot so the option view and the equity view agree;
    # fall back to the chain's own quote when the ticker is not in the screen.
    spot = None
    try:
        rows, _ = await _resolve_rows(None, None)
        match = next((r for r in rows if r.ticker == symbol), None)
        if match is not None:
            spot = match.price
    except HTTPException:
        spot = None

    # Realised volatility needs a price history. It is cached by the price
    # provider and is not required: without it the implied leg still works.
    history = None
    try:
        from app.pipeline import get_price_basis
        from app.providers import prices

        history = await asyncio.to_thread(
            prices.get_price_history, symbol,
            allow_yahoo=yahoo_usable(), basis=get_price_basis(),
        )
    except Exception:  # noqa: BLE001 - the volatility block degrades, nothing else
        history = None

    # The next dated release. It is the one catalyst invisible in volume and open
    # interest, so the verdict needs it to warn when it falls inside the expiry.
    next_earnings = None
    try:
        from app.providers.yahoo_analyst import get_analyst_detail

        detail = await asyncio.to_thread(
            get_analyst_detail, symbol, allow_yahoo=yahoo_usable())
        next_earnings = getattr(detail, "next_earnings_date", None) if detail else None
    except Exception:  # noqa: BLE001 - the catalyst note degrades
        next_earnings = None

    snapshot = await asyncio.to_thread(
        build_options_snapshot, symbol, spot=spot, expiries=expiries,
        allow_yahoo=yahoo_usable(), history=history, next_earnings=next_earnings,
    )
    if snapshot is None:
        return {
            "ticker": symbol, "available": False,
            "reason": "no listed option chain for this symbol",
        }
    payload = snapshot.model_dump(mode="json")
    payload["available"] = True
    return payload


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
    return {"run_id": run_id, "count": len(rows),
            "rows": [_row_payload(r) for r in rows]}


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
app.mount("/", VersionedStaticFiles(directory=str(STATIC_DIR), html=True), name="static")
