"""Per-team feature engineering: rolling means, trends, league z-scores."""
from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Optional, Sequence

from dg import config

INDEX_METRICS = list(config.INDEX_KEYS) + ["dgr_index"]
STRENGTH_METRICS = ["dgrtg", "ortg", "drtg"]

# Batch-predict memoization (cleared at start of predict_upcoming).
_league_stats_cache: Dict[tuple, Dict[str, Dict[str, float]]] = {}
_team_features_cache: Dict[tuple, Dict[str, Any]] = {}


def clear_feature_caches() -> None:
    """Drop snapshot-scoped feature memo used by batch predict."""
    _league_stats_cache.clear()
    _team_features_cache.clear()


def _mean(vals: Sequence[float]) -> Optional[float]:
    return statistics.mean(vals) if vals else None


def _stdev(vals: Sequence[float]) -> Optional[float]:
    if len(vals) < 2:
        return 0.0 if vals else None
    return statistics.pstdev(vals)


def _slope(vals: Sequence[float]) -> Optional[float]:
    """Simple linear slope over equally spaced readings (oldest -> newest)."""
    n = len(vals)
    if n < 2:
        return 0.0 if n == 1 else None
    xs = list(range(n))
    xbar = statistics.mean(xs)
    ybar = statistics.mean(vals)
    num = sum((x - xbar) * (y - ybar) for x, y in zip(xs, vals))
    den = sum((x - xbar) ** 2 for x in xs)
    if den == 0:
        return 0.0
    return num / den


def history_for_team(
    conn,
    team_id: int,
    *,
    limit: int = 15,
) -> List[Dict[str, Any]]:
    """Return team rating rows oldest->newest for trailing snapshots."""
    rows = conn.execute(
        """
        SELECT r.*, s.generated_at
        FROM dg_team_rating r
        JOIN dg_snapshot s ON s.id = r.snapshot_id
        WHERE r.team_id = ?
        ORDER BY s.generated_at DESC
        LIMIT ?
        """,
        (team_id, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def league_stats(
    conn, snapshot_id: int, league_id: Optional[int]
) -> Dict[str, Dict[str, float]]:
    """Per-metric mean/stdev within a league for one snapshot."""
    cache_key = (snapshot_id, league_id)
    cached = _league_stats_cache.get(cache_key)
    if cached is not None:
        return cached
    if league_id is None:
        rows = conn.execute(
            "SELECT * FROM dg_team_rating WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM dg_team_rating WHERE snapshot_id = ? AND league_id = ?",
            (snapshot_id, league_id),
        ).fetchall()
    out: Dict[str, Dict[str, float]] = {}
    for m in INDEX_METRICS + STRENGTH_METRICS + [
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
        "ppda_index",
        "consistency",
        "home_rating",
        "away_rating",
    ]:
        vals = [float(r[m]) for r in rows if r[m] is not None]
        mu = _mean(vals)
        sd = _stdev(vals) or 0.0
        if mu is not None:
            out[m] = {"mean": mu, "stdev": sd}
    _league_stats_cache[cache_key] = out
    return out


def zscore(value: Optional[float], stats: Optional[Dict[str, float]]) -> Optional[float]:
    if value is None or stats is None:
        return None
    sd = stats.get("stdev") or 0.0
    if sd == 0 or math.isnan(sd):
        return 0.0
    return (float(value) - stats["mean"]) / sd


def build_team_features(
    conn,
    team_id: int,
    snapshot_id: int,
    *,
    windows: Sequence[int] = (5, 10),
) -> Dict[str, Any]:
    cache_key = (snapshot_id, team_id)
    cached = _team_features_cache.get(cache_key)
    if cached is not None:
        return cached

    row = conn.execute(
        "SELECT * FROM dg_team_rating WHERE snapshot_id = ? AND team_id = ?",
        (snapshot_id, team_id),
    ).fetchone()
    if row is None:
        missing = {"team_id": team_id, "missing": True}
        _team_features_cache[cache_key] = missing
        return missing

    current = dict(row)
    hist = history_for_team(conn, team_id)
    league = league_stats(conn, snapshot_id, current.get("league_id"))

    feats: Dict[str, Any] = {
        "team_id": team_id,
        "team": current.get("team"),
        "league": current.get("league"),
        "league_id": current.get("league_id"),
        "history_n": len(hist),
        "missing": False,
    }

    # Current raw / index / venue
    for k, v in current.items():
        if k not in ("raw_json",):
            feats[k] = v

    for m in INDEX_METRICS + STRENGTH_METRICS:
        series = [float(h[m]) for h in hist if h.get(m) is not None]
        for w in windows:
            feats[f"{m}_roll{w}"] = _mean(series[-w:]) if series else None
        feats[f"{m}_trend"] = _slope(series) if series else None
        feats[f"{m}_z"] = zscore(current.get(m), league.get(m))

    # Venue z-scores used by matchup
    for m in (
        "ppda_index",
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
        "home_rating",
        "away_rating",
        "consistency",
    ):
        feats[f"{m}_z"] = zscore(current.get(m), league.get(m))

    _team_features_cache[cache_key] = feats
    return feats
