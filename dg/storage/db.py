"""SQLite connection helpers and schema bootstrap."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from dg import config

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_STRENGTH_COLS = (
    "dgrtg",
    "ortg",
    "drtg",
    "ortg_raw",
    "drtg_raw",
    "home_rating",
    "away_rating",
    "home_rating_raw",
    "away_rating_raw",
    "consistency",
    "consistency_index",
    "luck_per",
    "off_luck_per",
    "def_luck_per",
    "gf_per",
    "ga_per",
    "xgf_per",
    "xga_per",
    "coef_adj",
    "off_eff_index",
    "def_eff_index",
)


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    config.ensure_dirs()
    path = db_path or config.DB_PATH
    timeout = float(config.SQLITE_BUSY_TIMEOUT_SEC)
    conn = sqlite3.connect(str(path), timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    busy_ms = int(max(0, timeout) * 1000)
    conn.execute(f"PRAGMA busy_timeout = {busy_ms}")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _ensure_additive_columns(c: sqlite3.Connection) -> None:
    pred_cols = {row[1] for row in c.execute("PRAGMA table_info(prediction)").fetchall()}
    for col in ("markets_json", "probs_json"):
        if col not in pred_cols:
            c.execute(f"ALTER TABLE prediction ADD COLUMN {col} TEXT")

    rating_cols = {row[1] for row in c.execute("PRAGMA table_info(dg_team_rating)").fetchall()}
    for col in _STRENGTH_COLS:
        if col not in rating_cols:
            c.execute(f"ALTER TABLE dg_team_rating ADD COLUMN {col} REAL")

    fixture_cols = {row[1] for row in c.execute("PRAGMA table_info(fixture)").fetchall()}
    for col in ("home_logo", "away_logo"):
        if col not in fixture_cols:
            c.execute(f"ALTER TABLE fixture ADD COLUMN {col} TEXT")
    if "is_neutral" not in fixture_cols:
        c.execute("ALTER TABLE fixture ADD COLUMN is_neutral INTEGER")
    if "league_country" not in fixture_cols:
        c.execute("ALTER TABLE fixture ADD COLUMN league_country TEXT")

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS model_calibration (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fitted_at TEXT NOT NULL,
            model_version TEXT NOT NULL,
            outcome TEXT NOT NULL,
            slope REAL NOT NULL,
            intercept REAL NOT NULL,
            n_labels INTEGER NOT NULL,
            UNIQUE (model_version, outcome)
        )
        """
    )


def init_db(conn: Optional[sqlite3.Connection] = None) -> sqlite3.Connection:
    own = conn is None
    c = conn or connect()
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    c.executescript(sql)
    _ensure_additive_columns(c)
    # Backfill strength from raw_json on existing DBs (lazy import avoids cycles)
    try:
        from dg.storage.migrations import backfill_league_country, backfill_strength_from_raw

        backfill_strength_from_raw(c)
        backfill_league_country(c)
    except Exception:
        pass
    c.commit()
    if own:
        return c
    return c


@contextmanager
def db_session(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    conn = init_db(connect(db_path))
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def snapshot_exists(conn: sqlite3.Connection, generated_at: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM dg_snapshot WHERE generated_at = ?",
        (generated_at,),
    ).fetchone()
    return row is not None


def latest_snapshot(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM dg_snapshot ORDER BY generated_at DESC LIMIT 1"
    ).fetchone()


def previous_snapshot(
    conn: sqlite3.Connection, snapshot_id: int
) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM dg_snapshot
        WHERE id < ?
        ORDER BY id DESC LIMIT 1
        """,
        (snapshot_id,),
    ).fetchone()
