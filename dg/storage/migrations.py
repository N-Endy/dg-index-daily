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
_PROJECTION_EXTRA = (
    "xgot_home",
    "xgot_away",
    "xgot_total",
    "sot_home",
    "sot_away",
    "sot_total",
    "value_score",
    "value_over_2_5",
    "value_btts",
    "regression_home",
    "regression_away",
    "congestion_home",
    "congestion_away",
    "over_3_5_pct",
    "sot_over_8_5_pct",
    "projected_meta_json",
)


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
    if "league_country" not in fixture_cols:
        conn.execute("ALTER TABLE fixture ADD COLUMN league_country TEXT")

    proj_cols = _table_cols(conn, "fixture_projection")
    for col in _PROJECTION_EXTRA:
        if col not in proj_cols:
            if col in ("congestion_home", "congestion_away"):
                conn.execute(f"ALTER TABLE fixture_projection ADD COLUMN {col} INTEGER")
            elif col == "projected_meta_json":
                conn.execute(f"ALTER TABLE fixture_projection ADD COLUMN {col} TEXT")
            else:
                conn.execute(f"ALTER TABLE fixture_projection ADD COLUMN {col} REAL")

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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS residual_model (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fitted_at TEXT NOT NULL,
            model_key TEXT NOT NULL,
            market_key TEXT NOT NULL,
            head TEXT NOT NULL,
            n_train INTEGER NOT NULL,
            n_holdout INTEGER NOT NULL,
            holdout_brier REAL,
            baseline_brier REAL,
            beat_baseline INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 0,
            weights_json TEXT NOT NULL,
            feature_names_json TEXT NOT NULL,
            UNIQUE (model_key, market_key, head)
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


def backfill_league_country(conn) -> int:
    """Fill fixture.league_country from league_id map where still NULL."""
    from dg.leagues import load_league_countries

    countries = load_league_countries()
    if not countries:
        return 0
    updated = 0
    for league_id, country in countries.items():
        cur = conn.execute(
            """
            UPDATE fixture SET league_country = ?
            WHERE league_id = ? AND (league_country IS NULL OR league_country = '')
            """,
            (country, league_id),
        )
        updated += cur.rowcount
    if updated:
        logger.info("Backfilled league_country on %d fixture rows", updated)
    return updated


def backfill_projection_typed(conn) -> int:
    """Parse sim_stats_json into typed projection columns where still NULL."""
    from dg.model.sim_prior import extract_sim_fields

    proj_cols = _table_cols(conn, "fixture_projection")
    if "xgot_total" not in proj_cols:
        return 0
    rows = conn.execute(
        """
        SELECT id, sim_stats_json FROM fixture_projection
        WHERE sim_stats_json IS NOT NULL
          AND (xgot_total IS NULL AND sot_total IS NULL AND value_score IS NULL)
        """
    ).fetchall()
    updated = 0
    for r in rows:
        try:
            sim = json.loads(r["sim_stats_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(sim, dict):
            continue
        fields = extract_sim_fields(sim)
        meta = {
            "top_scores": fields.get("top_scores") or [],
            "correct_score_model": fields.get("correct_score_model"),
            "has_score_matrix": fields.get("has_score_matrix"),
            "shot_accuracy_total": fields.get("shot_accuracy_total"),
            "sot_conversion_total": fields.get("sot_conversion_total"),
            "big_chances_total": fields.get("big_chances_total"),
            "fh_sot_total": fields.get("fh_sot_total"),
            "score_first_home_pct": fields.get("score_first_home_pct"),
        }
        conn.execute(
            """
            UPDATE fixture_projection SET
                xgot_home=?, xgot_away=?, xgot_total=?,
                sot_home=?, sot_away=?, sot_total=?,
                value_score=?, value_over_2_5=?, value_btts=?,
                regression_home=?, regression_away=?,
                congestion_home=?, congestion_away=?,
                over_3_5_pct=?, sot_over_8_5_pct=?,
                projected_meta_json=?
            WHERE id=?
            """,
            (
                fields.get("xgot_home"),
                fields.get("xgot_away"),
                fields.get("xgot_total"),
                fields.get("sot_home"),
                fields.get("sot_away"),
                fields.get("sot_total"),
                fields.get("value_score"),
                fields.get("value_over_2_5"),
                fields.get("value_btts"),
                fields.get("regression_home"),
                fields.get("regression_away"),
                1 if fields.get("congestion_home") else 0,
                1 if fields.get("congestion_away") else 0,
                fields.get("over_3_5_pct"),
                fields.get("sot_over_8_5_pct"),
                json.dumps(meta),
                r["id"],
            ),
        )
        updated += 1
    if updated:
        logger.info("Backfilled typed sim fields on %d fixture_projection rows", updated)
    return updated


def migrate(db_path: Optional[Path] = None) -> None:
    """Apply schema.sql and additive column migrations + strength backfill."""
    conn = init_db(connect(db_path) if db_path is not None else None)
    _ensure_columns(conn)
    backfill_strength_from_raw(conn)
    backfill_league_country(conn)
    backfill_projection_typed(conn)
    conn.commit()
    conn.close()
