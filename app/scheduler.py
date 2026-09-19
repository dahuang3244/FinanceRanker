"""Scheduled auto-refresh.

Uses APScheduler in-process so a single `uvicorn` command gives you both the API
and the recurring refresh. Each run is persisted as a snapshot by the job
manager, so history accumulates without any extra wiring.
"""

from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings

log = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
JOB_ID = "auto-refresh"


def _run_refresh() -> None:
    from app.jobs import manager

    current = manager.current()
    if current and current.status in ("queued", "running"):
        log.info("scheduled refresh skipped: job %s still running", current.job_id)
        return
    log.info("scheduled refresh starting for %s", settings.ticker_list)
    manager.start(settings.ticker_list, trigger="scheduled")


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    # Run on local time so crontab expressions match the operator's wall clock.
    # `timezone=None` lets APScheduler resolve the real IANA zone via tzlocal;
    # passing `str(datetime.now().tzinfo)` would yield an abbreviation such as
    # "CST", which ZoneInfo rejects.
    _scheduler = BackgroundScheduler(timezone=None)
    tz = _scheduler.timezone

    if settings.schedule_cron:
        # e.g. FR_SCHEDULE_CRON="0 22 * * 1-5" -> weekdays 22:00 local time
        _scheduler.add_job(
            _run_refresh,
            CronTrigger.from_crontab(settings.schedule_cron, timezone=tz),
            id=JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        log.info("auto-refresh scheduled with cron %r (timezone %s)", settings.schedule_cron, tz)
    elif settings.schedule_hours > 0:
        _scheduler.add_job(
            _run_refresh,
            IntervalTrigger(hours=settings.schedule_hours, timezone=tz),
            id=JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(tz),
        )
        log.info("auto-refresh scheduled every %s hour(s), first run now", settings.schedule_hours)
    else:
        log.info("auto-refresh disabled (set FR_SCHEDULE_HOURS or FR_SCHEDULE_CRON)")
        return _scheduler

    _scheduler.start()
    for job in _scheduler.get_jobs():
        log.info("scheduler job %s next run at %s", job.id, job.next_run_time)
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
