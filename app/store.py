"""Snapshot storage (SQLite).

Every refresh is written as an immutable snapshot so rankings can be compared
over time ("what did the screen say last Friday?"). SQLite keeps this
dependency-free; the schema is deliberately flat and query-friendly.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime

from app.config import settings
from app.models import MetricRow

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    started_at    TEXT NOT NULL,
    finished_at   TEXT NOT NULL,
    tickers       TEXT NOT NULL,
    row_count     INTEGER NOT NULL,
    error_count   INTEGER NOT NULL,
    trigger       TEXT DEFAULT 'manual'
);

CREATE TABLE IF NOT EXISTS snapshots (
    run_id        TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    captured_at   TEXT NOT NULL,
    company       TEXT,
    price         REAL,
    market_cap    REAL,
    revenue_fy0   REAL,
    gaap_eps      REAL,
    score_overall REAL,
    rank          INTEGER,
    payload       TEXT NOT NULL,
    PRIMARY KEY (run_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_ticker ON snapshots(ticker, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);
"""


@contextmanager
def _connect():
    conn = sqlite3.connect(settings.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(SCHEMA)


def save_run(
    rows: list[MetricRow],
    errors: dict[str, str],
    *,
    started_at: datetime,
    finished_at: datetime,
    trigger: str = "manual",
) -> str:
    """Persist a refresh as a snapshot. Returns the run id."""
    init_db()
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    with _connect() as conn:
        conn.execute(
            "INSERT INTO runs (run_id, started_at, finished_at, tickers, row_count, "
            "error_count, trigger) VALUES (?,?,?,?,?,?,?)",
            (
                run_id,
                started_at.isoformat(timespec="seconds"),
                finished_at.isoformat(timespec="seconds"),
                ",".join(r.ticker for r in rows),
                len(rows),
                len(errors),
                trigger,
            ),
        )
        for row in rows:
            conn.execute(
                "INSERT OR REPLACE INTO snapshots (run_id, ticker, captured_at, company, "
                "price, market_cap, revenue_fy0, gaap_eps, score_overall, rank, payload) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    row.ticker,
                    (row.fetched_at or finished_at).isoformat(timespec="seconds"),
                    row.company,
                    row.price,
                    row.market_cap,
                    row.revenue_fy0,
                    row.gaap_eps,
                    row.score_overall,
                    row.rank,
                    row.model_dump_json(),
                ),
            )
    log.info("saved run %s (%s rows, %s errors)", run_id, len(rows), len(errors))
    return run_id


def list_runs(limit: int = 50) -> list[dict]:
    init_db()
    with _connect() as conn:
        cursor = conn.execute(
            "SELECT run_id, started_at, finished_at, tickers, row_count, error_count, trigger "
            "FROM runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]


def load_run(run_id: str) -> list[MetricRow]:
    init_db()
    with _connect() as conn:
        cursor = conn.execute(
            "SELECT payload FROM snapshots WHERE run_id = ? ORDER BY rank IS NULL, rank",
            (run_id,),
        )
        return [MetricRow.model_validate_json(r["payload"]) for r in cursor.fetchall()]


def latest_run_id() -> str | None:
    """The snapshot that holds the newest usable data.

    Preference order, and why it is not simply "newest":

    1. the newest snapshot written by the **current metric set**;
    2. failing that, the newest snapshot of any vintage.

    Step 1 matters because "newest" and "renderable" are different questions. A
    snapshot predating the current columns renders as blanks, so ranking purely
    by time would serve that older-shaped snapshot over a newer complete one
    whenever it happened to be captured later — which is how a full screen of
    blanks appears with nothing actually broken.

    Step 2 exists so a database with no current-version snapshot still returns
    something; the API marks it stale and the UI offers a refresh.

    `metrics_version` is read from the stored payload, so no schema change is
    needed and rows written before the field existed simply report 0.
    """
    init_db()
    from app.engine.scoring import METRICS_VERSION

    with _connect() as conn:
        row = conn.execute(
            """
            SELECT run_id
              FROM snapshots
             GROUP BY run_id
            HAVING MAX(COALESCE(json_extract(payload, '$.metrics_version'), 0)) = ?
             ORDER BY MAX(captured_at) DESC
             LIMIT 1
            """,
            (METRICS_VERSION,),
        ).fetchone()
        if row:
            return row["run_id"]
        row = conn.execute(
            "SELECT run_id FROM snapshots ORDER BY captured_at DESC LIMIT 1"
        ).fetchone()
        if row:
            return row["run_id"]
        # No snapshot rows yet: fall back to the run bookkeeping.
        row = conn.execute(
            "SELECT run_id FROM runs ORDER BY finished_at DESC, started_at DESC LIMIT 1"
        ).fetchone()
        return row["run_id"] if row else None


def ticker_history(ticker: str, limit: int = 60) -> list[dict]:
    init_db()
    with _connect() as conn:
        cursor = conn.execute(
            "SELECT captured_at, price, market_cap, revenue_fy0, gaap_eps, score_overall, rank "
            "FROM snapshots WHERE ticker = ? ORDER BY captured_at DESC LIMIT ?",
            (ticker.upper(), limit),
        )
        return [dict(r) for r in cursor.fetchall()]


def delete_run(run_id: str) -> bool:
    init_db()
    with _connect() as conn:
        conn.execute("DELETE FROM snapshots WHERE run_id = ?", (run_id,))
        cursor = conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
        return cursor.rowcount > 0


def stats() -> dict:
    init_db()
    with _connect() as conn:
        runs = conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
        rows = conn.execute("SELECT COUNT(*) AS n FROM snapshots").fetchone()["n"]
        last = conn.execute("SELECT MAX(captured_at) AS t FROM snapshots").fetchone()["t"]
    return {"runs": runs, "snapshots": rows, "last_capture": last}
