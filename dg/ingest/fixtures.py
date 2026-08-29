"""Ingest DataGaffer fixture feeds into fixture / fixture_projection."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


def _nested(d: Any, *keys: str, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _f(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def ingest_fixtures(
    conn,
    fixtures: List[Dict[str, Any]],
    *,
    snapshot_id: Optional[int] = None,
    known_team_ids: Optional[Set[int]] = None,
) -> Tuple[int, int, List[str]]:
    """
    Upsert fixtures and append projections.
    Returns (n_upserted, n_projections, unresolved_warnings).
    """
    now = datetime.now(timezone.utc).isoformat()
    warnings: List[str] = []
    n_fix = 0
    n_proj = 0
    seen_ids: Set[int] = set()

    for fx in fixtures:
        try:
            fixture_id = int(fx["fixture_id"])
        except (KeyError, TypeError, ValueError):
            warnings.append("fixture missing fixture_id")
            continue
        if fixture_id in seen_ids:
            continue
        seen_ids.add(fixture_id)

        home = fx.get("home") or {}
        away = fx.get("away") or {}
        home_id = int(home.get("id") or fx.get("home_id") or 0)
        away_id = int(away.get("id") or fx.get("away_id") or 0)
        if not home_id or not away_id:
            warnings.append(f"fixture {fx.get('fixture_id')} missing team ids")
            continue
        home_name = home.get("name")
        away_name = away.get("name")
        league = _nested(fx, "league", "name") or ""
        league_id = _nested(fx, "league", "id") or fx.get("league_id")
        date_utc = fx.get("date") or ""

        if known_team_ids is not None:
            if home_id not in known_team_ids:
                warnings.append(f"unresolved home team_id={home_id} ({home_name})")
            if away_id not in known_team_ids:
                warnings.append(f"unresolved away team_id={away_id} ({away_name})")

        existing = conn.execute(
            "SELECT fixture_id FROM fixture WHERE fixture_id = ?",
            (fixture_id,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE fixture SET
                    date_utc=?, league=?, league_id=?, home_id=?, away_id=?,
                    home_name=?, away_name=?, round=?, last_seen_at=?, raw_json=?
                WHERE fixture_id=?
                """,
                (
                    date_utc,
                    league,
                    league_id,
                    home_id,
                    away_id,
                    home_name,
                    away_name,
                    fx.get("round"),
                    now,
                    json.dumps(fx),
                    fixture_id,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO fixture (
                    fixture_id, date_utc, league, league_id, home_id, away_id,
                    home_name, away_name, round, first_seen_at, last_seen_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fixture_id,
                    date_utc,
                    league,
                    league_id,
                    home_id,
                    away_id,
                    home_name,
                    away_name,
                    fx.get("round"),
                    now,
                    now,
                    json.dumps(fx),
                ),
            )
        n_fix += 1

        sim = fx.get("sim_stats") or {}
        percents = sim.get("percents") or {}
        xg = _nested(sim, "xg") or {}
        book = fx.get("book_odds") or {}

        conn.execute(
            """
            INSERT OR IGNORE INTO fixture_projection (
                fixture_id, observed_at, snapshot_id,
                sim_xg_home, sim_xg_away,
                home_win_pct, draw_pct, away_win_pct,
                over_2_5_pct, btts_pct, matchup_pace_score,
                book_odds_json, sim_stats_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fixture_id,
                now,
                snapshot_id,
                _f(xg.get("home")),
                _f(xg.get("away")),
                _f(percents.get("home_win_pct")),
                _f(percents.get("draw_pct")),
                _f(percents.get("away_win_pct")),
                _f(percents.get("over_2_5_pct")),
                _f(percents.get("btts_pct")),
                _f(_nested(sim, "matchup_pace", "score")),
                json.dumps(book),
                json.dumps(sim),
            ),
        )
        n_proj += 1

    logger.info("Upserted %d fixtures, %d projections (%d warnings)", n_fix, n_proj, len(warnings))
    return n_fix, n_proj, warnings
