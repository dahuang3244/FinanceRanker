"""Snapshot resolution and staleness reporting.

The bug these pin down: `_resolve_rows` used to return whichever job this
process ran last, even a finished one, so a newer snapshot written by another
instance was ignored and the screen showed figures that predated the columns it
was rendering them in.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _isolated_store(tmp: Path):
    """A private SQLite file so the assertions never touch real snapshots."""
    from app import store
    from app.config import settings

    settings.db_path = tmp / "test.db"
    store.init_db()
    return store


def _row(ticker: str, version: int, when: datetime, **fields):
    from app.models import MetricRow

    return MetricRow(ticker=ticker, fetched_at=when, metrics_version=version, **fields)


def test_latest_snapshot_wins_by_capture_time_not_by_start_time():
    with tempfile.TemporaryDirectory() as tmp:
        store = _isolated_store(Path(tmp))
        base = datetime(2026, 3, 1, 12, 0, 0)
        # An OLD run that started earlier but finished LATER (the exact shape
        # that broke the screen: a slow job whose rows are stale).
        slow = store.save_run(
            [_row("OLD", 0, base - timedelta(hours=5))], {},
            started_at=base - timedelta(hours=6), finished_at=base,
        )
        fast = store.save_run(
            [_row("NEW", 2, base + timedelta(minutes=1))], {},
            started_at=base + timedelta(minutes=1), finished_at=base + timedelta(minutes=2),
        )
        # Inserting in this order also proves the choice is not "last written".
        assert store.latest_run_id() == fast, "the newest captured rows must win"
        assert store.load_run(store.latest_run_id())[0].ticker == "NEW"
        assert store.load_run(slow)[0].ticker == "OLD"
        assert store.load_run(fast)[0].ticker == "NEW"


def test_snapshot_written_by_an_older_metric_set_is_reported_stale():
    import app.main as main
    from app.engine.scoring import METRICS, METRICS_VERSION

    # A row from a build that knew about none of the current metrics.
    old = [_row(f"T{i}", METRICS_VERSION - 1, datetime.now()) for i in range(3)]
    info = main._staleness(old)
    assert info["stale"] is True
    assert info["reason"] == "older_metrics"
    # Every unscored metric in the catalogue is named, so the notice can list them.
    assert set(info["missing_metrics"]) == {attr for attr, _, _ in METRICS}
    assert info["stale_after_hours"] is not None


def test_current_metric_set_is_not_stale_even_when_a_few_metrics_are_absent():
    import app.main as main
    from app.engine.scoring import METRICS_VERSION

    # A current snapshot that legitimately lacks one metric (negative FCF, say)
    # must not be reported as a shape mismatch.
    partial = _row("AAA", METRICS_VERSION, datetime.now(), return_3m=0.05)
    info = main._staleness([partial])
    assert info["stale"] is False
    assert info["reason"] is None


def test_genuinely_old_snapshot_is_flagged_for_age():
    import app.main as main
    from app.config import settings
    from app.engine.scoring import METRICS_VERSION

    ancient = _row("AAA", METRICS_VERSION, datetime.now() - timedelta(hours=100))
    info = main._staleness([ancient])
    assert info["stale"] is True
    assert info["reason"] == "old_snapshot"
    assert info["age_hours"] > settings.stale_after_hours


def test_job_manager_prefers_the_most_recently_finished_job():
    from app.jobs import Job, JobManager

    manager = JobManager()
    early = Job(job_id="early", tickers=["A"], status="done",
                started_at=datetime(2026, 3, 1, 10, 0), finished_at=datetime(2026, 3, 1, 12, 0))
    early.rows = [_row("A", 2, datetime.now())]
    late = Job(job_id="late", tickers=["B"], status="done",
               started_at=datetime(2026, 3, 1, 9, 0), finished_at=datetime(2026, 3, 1, 13, 0))
    late.rows = [_row("B", 2, datetime.now())]
    with manager._lock:
        manager._jobs = {"early": early, "late": late}
        manager._current = "early"
    # A finished job must never be mistaken for an in-flight one.
    assert manager.running() is None
    assert manager.freshest() is late


def test_active_job_is_visible_while_it_runs():
    from app.jobs import Job, JobManager

    manager = JobManager()
    running = Job(job_id="run", tickers=["A"], status="running",
                  started_at=datetime(2026, 3, 1, 10, 0))
    running.rows = [_row("A", 2, datetime.now())]
    with manager._lock:
        manager._jobs = {"run": running}
        manager._current = "run"
    assert manager.running() is running
    assert manager.freshest() is None


def test_captured_at_is_stored_per_snapshot_row():
    """The selection key must be persisted on the row, not only on `runs`."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        store = _isolated_store(Path(tmp))
        when = datetime(2026, 3, 1, 10, 30)
        store.save_run([_row("AAA", 2, when)], {}, started_at=when, finished_at=when)
        con = sqlite3.connect(str(db))
        try:
            captured = con.execute("SELECT captured_at FROM snapshots").fetchone()[0]
            payload = con.execute("SELECT payload FROM snapshots").fetchone()[0]
        finally:
            con.close()
        assert captured.startswith("2026-03-01T10:30")
        assert json.loads(payload)["metrics_version"] == 2


if __name__ == "__main__":
    import traceback

    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception:
                failed += 1
                print(f"FAIL  {name}")
                traceback.print_exc()
            else:
                passed += 1
                print(f"PASS  {name}")
    print(f"\n{passed}/{passed + failed} passed")
    raise SystemExit(1 if failed else 0)
