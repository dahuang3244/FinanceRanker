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


def test_latest_snapshot_prefers_the_current_metric_set():
    """A renderable snapshot beats a newer one that predates the columns.

    Ranking purely by time would serve an older-shaped snapshot whenever it was
    captured later, and the screen would show blanks with nothing broken.
    """
    from app.engine.scoring import METRICS_VERSION

    with tempfile.TemporaryDirectory() as tmp:
        store = _isolated_store(Path(tmp))
        base = datetime(2026, 3, 1, 12, 0, 0)
        # Current-shaped rows, captured EARLIER.
        current = store.save_run(
            [_row("CURRENT", METRICS_VERSION, base)], {},
            started_at=base, finished_at=base,
        )
        # Older-shaped rows, captured LATER.
        older = store.save_run(
            [_row("OLD", METRICS_VERSION - 1, base + timedelta(hours=1))], {},
            started_at=base + timedelta(hours=1), finished_at=base + timedelta(hours=1),
        )
        assert store.latest_run_id() == current, (
            "a snapshot predating the current metric set must not win on time alone"
        )
        assert store.load_run(current)[0].ticker == "CURRENT"
        assert store.load_run(older)[0].ticker == "OLD"


def test_latest_snapshot_falls_back_when_nothing_matches_the_current_version():
    """With no current-version snapshot the newest is still returned, so the
    caller can report it stale rather than showing nothing at all."""
    from app.engine.scoring import METRICS_VERSION

    with tempfile.TemporaryDirectory() as tmp:
        store = _isolated_store(Path(tmp))
        base = datetime(2026, 3, 1, 12, 0, 0)
        old = store.save_run(
            [_row("OLD", METRICS_VERSION - 2, base)], {},
            started_at=base, finished_at=base,
        )
        newer = store.save_run(
            [_row("NEWER", METRICS_VERSION - 1, base + timedelta(hours=2))], {},
            started_at=base + timedelta(hours=2), finished_at=base + timedelta(hours=2),
        )
        assert store.latest_run_id() == newer
        assert store.load_run(old)[0].ticker == "OLD"


def test_newest_capture_wins_within_the_same_metric_version():
    with tempfile.TemporaryDirectory() as tmp:
        store = _isolated_store(Path(tmp))
        from app.engine.scoring import METRICS_VERSION

        base = datetime(2026, 3, 1, 12, 0, 0)
        # An OLD run that started earlier but finished LATER (a slow job whose
        # rows are stale), against a fast job of the same version.
        slow = store.save_run(
            [_row("SLOW", METRICS_VERSION, base - timedelta(hours=5))], {},
            started_at=base - timedelta(hours=6), finished_at=base,
        )
        fast = store.save_run(
            [_row("FAST", METRICS_VERSION, base + timedelta(minutes=1))], {},
            started_at=base + timedelta(minutes=1), finished_at=base + timedelta(minutes=2),
        )
        assert store.latest_run_id() == fast, "within one version, newest capture wins"
        assert store.load_run(slow)[0].ticker == "SLOW"
        assert store.load_run(fast)[0].ticker == "FAST"


def _complete_row(ticker: str, version: int, when: datetime):
    """A row carrying every catalogued metric, so nothing reads as missing.

    Staleness asks "does no row carry this column at all", so a test about
    version or age has to start from a complete row or it will be flagged for
    gaps instead of the thing under test.
    """
    from app.engine.scoring import METRICS, metric_catalog

    fields = {attr: 1.0 for attr, _, _ in METRICS}
    for entry in metric_catalog():
        fields.setdefault(entry["attr"], 1.0)
    return _row(ticker, version, when, **fields)


def test_snapshot_written_by_an_older_metric_set_is_reported_stale():
    import app.main as main
    from app.engine.scoring import METRICS, METRICS_VERSION, metric_catalog

    # Rows from a build that knew about none of the current columns: nothing is
    # populated, so the mismatch is detected from what is missing.
    old = [_row(f"T{i}", METRICS_VERSION - 1, datetime.now()) for i in range(3)]
    info = main._staleness(old)
    assert info["stale"] is True
    assert info["reason"] == "older_metrics"
    # Every displayed column is named, so the notice can list what is absent.
    assert set(info["missing_metrics"]) == {e["attr"] for e in metric_catalog()}
    assert info["stale_after_hours"] is not None


def test_a_few_absent_metrics_do_not_make_a_current_snapshot_stale():
    """Legitimately absent figures (negative FCF, no SBC tag) are not a shape
    mismatch — only columns no row carries at all are."""
    import app.main as main
    from app.engine.scoring import METRICS_VERSION, metric_catalog

    fields = {e["attr"]: 1.0 for e in metric_catalog()}
    # A handful of genuinely-unavailable figures, as a real snapshot has.
    for missing in ("price_to_fcf", "peg_ratio", "sbc_pct_revenue", "fcf_growth_yoy"):
        fields[missing] = None
    info = main._staleness([_row("AAA", METRICS_VERSION, datetime.now(), **fields)])
    assert info["stale"] is False
    assert info["reason"] is None
    # They are still reported as absent, they just do not imply an old snapshot.
    assert set(info["missing_metrics"]) == {
        "price_to_fcf", "peg_ratio", "sbc_pct_revenue", "fcf_growth_yoy",
    }


def test_genuinely_old_snapshot_is_flagged_for_age():
    import app.main as main
    from app.config import settings
    from app.engine.scoring import METRICS_VERSION

    ancient = _complete_row("AAA", METRICS_VERSION, datetime.now() - timedelta(hours=100))
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
