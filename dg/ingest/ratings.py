"""Ingest dg_ratings.json into dg_snapshot / dg_team_rating."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from dg.storage.db import snapshot_exists

logger = logging.getLogger(__name__)

# Same-name float fields on the payload
_FLOAT_FIELDS = (
    "ppda",
    "match_pace_shots",
    "chaos_index",
    "nec_chaos",
    "control_raw",
    "ppda_index",
    "pace_index",
    "agix_index",
    "nec_index",
    "control_index",
    "dgr_index",
    "home_pace_index",
    "away_pace_index",
    "home_agix_index",
    "away_agix_index",
    "home_nec_index",
    "away_nec_index",
    "home_control_index",
    "away_control_index",
    "home_off_eff_index",
    "away_off_eff_index",
    "home_def_eff_index",
    "away_def_eff_index",
    "home_rating_index",
    "away_rating_index",
    "home_rating_raw_index",
    "away_rating_raw_index",
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
    "home_rating",
    "away_rating",
    "home_rating_raw",
    "away_rating_raw",
    "points",
)

# Column name -> payload key (mixed-case DG Rating fields)
_STRENGTH_KEY_MAP = {
    "dgrtg": "DGRtg",
    "ortg": "ORtg",
    "drtg": "DRtg",
    "ortg_raw": "ORtg_raw",
    "drtg_raw": "DRtg_raw",
}

STRENGTH_COLUMNS = tuple(_STRENGTH_KEY_MAP.keys()) + (
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


def _f(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def extract_strength_fields(payload: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Pull DG Rating strength fields from a raw team dict (payload or raw_json)."""
    out: Dict[str, Optional[float]] = {}
    for col, key in _STRENGTH_KEY_MAP.items():
        out[col] = _f(payload.get(key))
    for col in (
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
    ):
        out[col] = _f(payload.get(col))
    return out


def ingest_ratings(
    conn,
    teams: List[Dict[str, Any]],
    *,
    generated_at: str,
    payload_sha256: str,
    meta: Optional[Dict[str, Any]] = None,
) -> Tuple[int, bool]:
    """
    Insert a new snapshot + team rows.
    Returns (snapshot_id, inserted). If generated_at already exists, returns
    (existing_id, False) without writing.
    """
    if snapshot_exists(conn, generated_at):
        row = conn.execute(
            "SELECT id FROM dg_snapshot WHERE generated_at = ?",
            (generated_at,),
        ).fetchone()
        logger.info("Snapshot %s already present (id=%s); skip ratings ingest", generated_at, row["id"])
        return int(row["id"]), False

    scraped_at = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT INTO dg_snapshot (generated_at, scraped_at, payload_sha256, n_teams, meta_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            generated_at,
            scraped_at,
            payload_sha256,
            len(teams),
            json.dumps(meta or {}),
        ),
    )
    snapshot_id = int(cur.lastrowid)

    rows = []
    for t in teams:
        row = {
            "snapshot_id": snapshot_id,
            "team_id": int(t["team_id"]),
            "team": t.get("team") or "",
            "league": t.get("league"),
            "league_id": t.get("league_id"),
            "rank": t.get("rank"),
            "raw_json": json.dumps(t),
        }
        for k in _FLOAT_FIELDS:
            row[k] = _f(t.get(k))
        row.update(extract_strength_fields(t))
        rows.append(row)

    conn.executemany(
        """
        INSERT INTO dg_team_rating (
            snapshot_id, team_id, team, league, league_id,
            ppda, match_pace_shots, chaos_index, nec_chaos, control_raw,
            ppda_index, pace_index, agix_index, nec_index, control_index, dgr_index,
            home_pace_index, away_pace_index, home_agix_index, away_agix_index,
            home_nec_index, away_nec_index, home_control_index, away_control_index,
            home_off_eff_index, away_off_eff_index, home_def_eff_index, away_def_eff_index,
            home_rating_index, away_rating_index, home_rating_raw_index, away_rating_raw_index,
            dgrtg, ortg, drtg, ortg_raw, drtg_raw,
            home_rating, away_rating, home_rating_raw, away_rating_raw,
            consistency, consistency_index, luck_per, off_luck_per, def_luck_per,
            gf_per, ga_per, xgf_per, xga_per, coef_adj, off_eff_index, def_eff_index,
            rank, points, raw_json
        ) VALUES (
            :snapshot_id, :team_id, :team, :league, :league_id,
            :ppda, :match_pace_shots, :chaos_index, :nec_chaos, :control_raw,
            :ppda_index, :pace_index, :agix_index, :nec_index, :control_index, :dgr_index,
            :home_pace_index, :away_pace_index, :home_agix_index, :away_agix_index,
            :home_nec_index, :away_nec_index, :home_control_index, :away_control_index,
            :home_off_eff_index, :away_off_eff_index, :home_def_eff_index, :away_def_eff_index,
            :home_rating_index, :away_rating_index, :home_rating_raw_index, :away_rating_raw_index,
            :dgrtg, :ortg, :drtg, :ortg_raw, :drtg_raw,
            :home_rating, :away_rating, :home_rating_raw, :away_rating_raw,
            :consistency, :consistency_index, :luck_per, :off_luck_per, :def_luck_per,
            :gf_per, :ga_per, :xgf_per, :xga_per, :coef_adj, :off_eff_index, :def_eff_index,
            :rank, :points, :raw_json
        )
        """,
        rows,
    )
    logger.info("Inserted snapshot %s with %d teams", snapshot_id, len(rows))
    return snapshot_id, True


def team_ids_for_snapshot(conn, snapshot_id: int) -> set:
    rows = conn.execute(
        "SELECT team_id FROM dg_team_rating WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchall()
    return {int(r["team_id"]) for r in rows}


def load_team_map(conn, snapshot_id: int) -> Dict[int, Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM dg_team_rating WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchall()
    return {int(r["team_id"]): dict(r) for r in rows}
