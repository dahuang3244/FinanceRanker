"""Refresh job manager.

Holds in-flight refresh state so the browser can poll or subscribe (SSE) for
progress. Jobs run in a worker thread; state is guarded by a lock because the
SSE generator reads while the worker writes.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from app import pipeline, store
from app.models import MetricRow

log = logging.getLogger(__name__)


@dataclass
class Job:
    job_id: str
    tickers: list[str]
    status: str = "queued"          # queued | running | done | failed
    total: int = 0
    done: int = 0
    message: str = ""
    errors: dict[str, str] = field(default_factory=dict)
    rows: list[MetricRow] = field(default_factory=list)
    run_id: str | None = None
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None
    # `deque.append` and `len()` are atomic under CPython, which lets the async
    # SSE reader observe progress without ever taking a lock on the event loop.
    events: deque = field(default_factory=lambda: deque(maxlen=2000))

    def emit(self, kind: str, **payload) -> None:
        self.events.append({"kind": kind, "ts": datetime.now().isoformat(), **payload})

    def events_since(self, cursor: int) -> tuple[list[dict], int]:
        """Return events after `cursor` plus the new cursor.

        The first call after a job has already emitted more than `maxlen`
        events would otherwise return an empty list, so if the cursor has
        fallen off the window we restart from the beginning of what remains.
        """
        size = len(self.events)
        if cursor > size:
            cursor = 0
        return list(self.events)[cursor:], size

    @property
    def is_finished(self) -> bool:
        return self.status in ("done", "failed")

    def snapshot(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "message": self.message,
            "errors": dict(self.errors),
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "progress": round(self.done / self.total, 4) if self.total else 0.0,
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._current: str | None = None

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def current(self) -> Job | None:
        with self._lock:
            return self._jobs.get(self._current) if self._current else None

    def running(self) -> Job | None:
        """The most recently started job that is still in flight, if any."""
        with self._lock:
            active = [j for j in self._jobs.values() if not j.is_finished and j.rows]
            active += [j for j in self._jobs.values() if j.status == "running"]
        if not active:
            return None
        return max(active, key=lambda j: j.started_at)

    def freshest(self) -> Job | None:
        """The most recently *finished* job that produced rows.

        Ordering by `finished_at` rather than by "whoever started last" matters
        when more than one refresh is in flight, or when the same snapshot
        directory is served by more than one instance: the job that started
        first but finished last holds the newest numbers, and the job that
        started last can still be empty. Even a finished job is only a
        convenience — the caller prefers the newest stored snapshot, because a
        refresh run by another process is newer than this process's memory.
        """
        with self._lock:
            done = [j for j in self._jobs.values() if j.is_finished and j.rows]
        if not done:
            return None
        return max(done, key=lambda j: j.finished_at or j.started_at)

    def list_jobs(self, limit: int = 20) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.started_at, reverse=True)
        return [j.snapshot() for j in jobs[:limit]]

    def start(self, tickers: list[str], *, trigger: str = "manual") -> Job:
        """Create and launch a refresh job."""
        job = Job(job_id=uuid.uuid4().hex[:12], tickers=tickers, status="queued")
        with self._lock:
            self._jobs[job.job_id] = job
            self._current = job.job_id
        thread = threading.Thread(
            target=self._run, args=(job, trigger), name=f"refresh-{job.job_id}", daemon=True
        )
        thread.start()
        return job

    def _run(self, job: Job, trigger: str) -> None:
        job.status = "running"
        job.total = len(job.tickers)
        job.message = "fetching SPY benchmark"
        job.emit("start", total=job.total, tickers=job.tickers)

        def progress(ticker: str, status: str, done: int, total: int) -> None:
            job.done = done
            job.total = total
            job.message = f"{ticker}: {status}"
            job.emit("progress", ticker=ticker, status=status, done=done, total=total)

        try:
            rows, errors = pipeline.build_rows(job.tickers, progress=progress)
            job.rows = rows
            job.errors = errors
            job.done = job.total
            finished = datetime.now()
            try:
                job.run_id = store.save_run(
                    rows, errors, started_at=job.started_at, finished_at=finished, trigger=trigger
                )
            except Exception as exc:  # storage failure must not lose the result
                log.exception("snapshot save failed")
                job.emit("warning", message=f"snapshot save failed: {exc}")
            job.finished_at = finished
            job.status = "done"
            job.message = f"completed: {len(rows)} peers, {len(errors)} failed"
            job.emit("done", rows=len(rows), errors=len(errors), run_id=job.run_id)
            # Quarterly EPS is fetched per company page, and the first view of a cold
            # page waited 8 to 25 seconds for it — which reads as the panel being
            # absent rather than slow, and was reported as exactly that. Nothing about
            # it needs to be lazy: the filings and releases it reads are the same ones
            # the refresh has just been through, so warming it here costs a few seconds
            # in the background and removes the wait from every page thereafter.
            #
            # This runs on the refresh thread, so the synchronous call is made
            # directly; there is no event loop to block.
            self._warm_quarterly(job)
        except Exception as exc:
            log.exception("refresh job failed")
            job.status = "failed"
            job.message = str(exc)
            job.finished_at = datetime.now()
            job.emit("error", message=str(exc))

    def _warm_quarterly(self, job: Job) -> None:
        """Compute each ticker's quarterly EPS so the first page view is instant.

        Failures are swallowed deliberately: this is a cache warm, and a ticker whose
        filings cannot be read will compute on demand exactly as it did before. One
        unavailable filer must not mark a completed refresh as failed.
        """
        from app.providers.yahoo_analyst import get_analyst_detail
        from app.quarterly import quarterly_eps

        warmed = 0
        for index, ticker in enumerate(job.tickers, start=1):
            job.message = f"{ticker}: quarterly EPS {index}/{len(job.tickers)}"
            job.emit("progress", ticker=ticker, status="quarterly EPS",
                     done=job.done, total=job.total)
            try:
                analyst = None
                try:
                    analyst = get_analyst_detail(ticker, allow_yahoo=True)
                except Exception:  # noqa: BLE001 - the SEC path does not need it
                    analyst = None
                rows = quarterly_eps(ticker, quarters=8, analyst=analyst)
                if rows:
                    warmed += 1
            except Exception as exc:  # noqa: BLE001 - compute on demand later
                log.debug("quarterly warm failed for %s: %s", ticker, exc)
        job.message = (f"completed: {len(job.rows or [])} peers; "
                       f"quarterly EPS ready for {warmed}")
        job.emit("warning", message=f"quarterly EPS precomputed for {warmed} tickers")


manager = JobManager()
