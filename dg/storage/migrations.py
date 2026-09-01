"""Lightweight schema bootstrap + additive migrations."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from dg.ingest.ratings import STRENGTH_COLUMNS, extract_strength_fields
from dg.storage.db import connect, init_db

logger = logging.getLogger(__name__)

_STRENGTH_COLS = list(STRENGTH_COLUMNS)
_PRED_EXTRA = ("markets_json", "probs_json")


def _table_cols(conn, table: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_columns(conn) -> None:
    """Add columns introduced after initial schema (SQLite has no IF NOT EXISTS for columns)."""
    pred_cols = _table_cols(conn, "prediction")
    for col in _PRED_EXTRA:
        if col not in pred_cols:
            conn.execute(f"ALTER TABLE prediction ADD COLUMN {col} TEXT")

    rating_cols = _table_cols(conn, "dg_team_rating")
    for col in _STRENGTH_COLS:
        if col not in rating_cols:
            conn.execute(f"ALTER TABLE dg_team_rating ADD COLUMN {col} REAL")

    fixture_cols = _table_cols(conn, "fixture")
    for col in ("home_logo", "away_logo"):
        if col not in fixture_cols:
            conn.execute(f"ALTER TABLE fixture ADD COLUMN {col} TEXT")
    if "is_neutral" not in fixture_cols:
        conn.execute("ALTER TABLE fixture ADD COLUMN is_neutral INTEGER")

    conn.execute(
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


def backfill_strength_from_raw(conn) -> int:
    """
    Parse raw_json on existing dg_team_rating rows and fill strength columns
    where dgrtg is still NULL. Returns number of rows updated.
    """
    rating_cols = _table_cols(conn, "dg_team_rating")
    if "dgrtg" not in rating_cols:
        return 0
    rows = conn.execute(
        "SELECT snapshot_id, team_id, raw_json FROM dg_team_rating WHERE dgrtg IS NULL"
    ).fetchall()
    updated = 0
    for r in rows:
        try:
            payload = json.loads(r["raw_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        fields = extract_strength_fields(payload)
        if fields.get("dgrtg") is None and fields.get("ortg") is None:
            continue
        sets = ", ".join(f"{c} = ?" for c in _STRENGTH_COLS)
        vals = [fields.get(c) for c in _STRENGTH_COLS]
        vals.extend([r["snapshot_id"], r["team_id"]])
        conn.execute(
            f"UPDATE dg_team_rating SET {sets} WHERE snapshot_id = ? AND team_id = ?",
            vals,
        )
        updated += 1
    if updated:
        logger.info("Backfilled strength fields on %d dg_team_rating rows", updated)
    return updated


def migrate(db_path: Optional[Path] = None) -> None:
    """Apply schema.sql and additive column migrations + strength backfill."""
    conn = init_db(connect(db_path) if db_path is not None else None)
    _ensure_columns(conn)
    backfill_strength_from_raw(conn)
    conn.commit()
    conn.close()
