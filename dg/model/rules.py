"""rule_v2: DG Rating strength + style composite, Poisson-blended 1X2, multi-market."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from dg import config
from dg.features.matchup import book_lean, build_matchup, sim_lean
from dg.features.team import build_team_features
from dg.model.goals import league_avg_ortg, predict_goals
from dg.model.markets import predict_markets
from dg.model.registry import load_config

logger = logging.getLogger(__name__)


def _score_matchup(matchup: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[float, Dict[str, float], List[str]]:
    w = cfg["weights"]
    # rating_gap is on a coarser scale (~ -2..+2 for venue ratings); normalize gently
    components = {
        "rating_gap": matchup.get("rating_gap", 0.0) / 1.5,
        "pressing_mismatch": matchup["pressing_mismatch"] / 50.0,
        "pace_clash": (matchup["pace_clash"] - 100.0) / 100.0,
        "attack_vs_control_home": matchup["attack_vs_control_home"] / 50.0,
        "attack_vs_control_away": matchup["attack_vs_control_away"] / 50.0,
        "aggression_asymmetry": matchup["aggression_asymmetry"] / 50.0,
        "efficiency_edge": matchup["efficiency_edge"] / 50.0,
        "form_trend": matchup["form_trend"] / 5.0,
    }
    total = 0.0
    weighted: Dict[str, float] = {}
    for key, raw in components.items():
        weight = float(w.get(key, 0.0))
        contrib = weight * raw
        weighted[key] = contrib
        total += contrib

    drivers = sorted(weighted.items(), key=lambda x: abs(x[1]), reverse=True)
    driver_lines = []
    for k, v in drivers[:4]:
        sign = "+" if v >= 0 else ""
        label = k.replace("_", " ")
        driver_lines.append(f"{label} ({sign}{v:.2f})")
    return total, weighted, driver_lines


def _character(matchup: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    c = cfg.get("character") or {}
    pace = matchup["pace_clash"]
    agg = abs(matchup["aggression_asymmetry"])
    if pace >= float(c.get("open_pace_sum_gte", 120)):
        base = "open"
    elif pace <= float(c.get("tight_pace_sum_lte", 80)):
        base = "tight"
    else:
        base = "balanced"
    if agg >= float(c.get("volatile_aggression_abs_gte", 25)):
        return "volatile" if base != "tight" else "tight-volatile"
    return base


def _blend_1x2(
    poisson: Dict[str, Any],
    style_score: float,
    cfg: Dict[str, Any],
    *,
    conn=None,
    model_version: Optional[str] = None,
) -> Dict[str, float]:
    """Tilt Poisson 1X2 probabilities with a bounded style composite."""
    ph = float(poisson.get("home", 1 / 3))
    pd = float(poisson.get("draw", 1 / 3))
    pa = float(poisson.get("away", 1 / 3))
    scale = float(cfg.get("style_tilt_scale", 0.18))
    cap = float(cfg.get("style_tilt_cap", 0.12))
    tilt = max(-cap, min(cap, style_score * scale))
    ph2 = max(0.02, ph + tilt)
    pa2 = max(0.02, pa - tilt)
    pd2 = max(0.02, pd)
    s = ph2 + pd2 + pa2
    blended = {"home": ph2 / s, "draw": pd2 / s, "away": pa2 / s}
    if config.SUPERVISED_ENABLED and conn is not None and model_version:
        from dg.model.supervised import apply_calibration, load_calibration

        params = load_calibration(conn, model_version=model_version)
        if params:
            blended = apply_calibration(blended, params)
    return blended


def _lean_from_probs(probs: Dict[str, float]) -> str:
    pairs = [("Home", probs["home"]), ("Draw", probs["draw"]), ("Away", probs["away"])]
    pairs.sort(key=lambda x: -x[1])
    return pairs[0][0]


def _confidence(
    score: float,
    matchup: Dict[str, Any],
    cfg: Dict[str, Any],
    *,
    probs: Optional[Dict[str, float]] = None,
) -> str:
    c = cfg.get("confidence") or {}
    hist = min(int(matchup.get("history_n_home") or 0), int(matchup.get("history_n_away") or 0))
    abs_s = abs(score)
    high = float(c.get("high_abs_score_gte", 0.30))
    med = float(c.get("medium_abs_score_gte", 0.12))
    min_hist = int(c.get("min_history_for_high", 3))
    min_cons = float(c.get("min_consistency_for_high", 0.55))
    cons = float(matchup.get("consistency_mean") or 0.55)

    margin = 0.0
    if probs:
        ordered = sorted([probs["home"], probs["draw"], probs["away"]], reverse=True)
        margin = ordered[0] - ordered[1]

    high_margin = float(c.get("high_prob_margin", 0.12))
    med_margin = float(c.get("medium_prob_margin", 0.05))

    if (
        abs_s >= high
        and hist >= min_hist
        and cons >= min_cons
        and (not probs or margin >= high_margin)
    ):
        return "high"
    if abs_s >= med or (probs and margin >= med_margin):
        return "medium"
    return "low"


def predict_fixture(
    conn,
    fixture_row: Dict[str, Any],
    snapshot_id: int,
    *,
    persist: bool = True,
) -> Optional[Dict[str, Any]]:
    version, cfg = load_config()
    home = build_team_features(conn, int(fixture_row["home_id"]), snapshot_id)
    away = build_team_features(conn, int(fixture_row["away_id"]), snapshot_id)
    matchup = build_matchup(home, away)
    if not matchup.get("ok"):
        return None

    score, weighted, drivers = _score_matchup(matchup, cfg)
    character = _character(matchup, cfg)

    league_avg = league_avg_ortg(conn, snapshot_id, matchup.get("league_id"))
    goal_probs = predict_goals(matchup, league_avg=league_avg)
    blended = _blend_1x2(goal_probs, score, cfg, conn=conn, model_version=version)
    lean = _lean_from_probs(blended)
    confidence = _confidence(score, matchup, cfg, probs=blended)

    proj = conn.execute(
        """
        SELECT * FROM fixture_projection
        WHERE fixture_id = ?
        ORDER BY observed_at DESC LIMIT 1
        """,
        (fixture_row["fixture_id"],),
    ).fetchone()
    book = json.loads(proj["book_odds_json"]) if proj and proj["book_odds_json"] else {}
    sim = json.loads(proj["sim_stats_json"]) if proj and proj["sim_stats_json"] else {}
    if proj:
        matchup["sim_xg_home"] = proj["sim_xg_home"]
        matchup["sim_xg_away"] = proj["sim_xg_away"]

    markets = predict_markets(matchup, book=book, sim=sim, goal_probs=goal_probs)

    probs_out = {
        "home": round(blended["home"], 4),
        "draw": round(blended["draw"], 4),
        "away": round(blended["away"], 4),
        "lam_home": goal_probs.get("lam_home"),
        "lam_away": goal_probs.get("lam_away"),
        "poisson_home": goal_probs.get("home"),
        "poisson_draw": goal_probs.get("draw"),
        "poisson_away": goal_probs.get("away"),
        "over_2_5": goal_probs.get("over_2_5"),
        "btts_yes": goal_probs.get("btts_yes"),
        "goals_version": goal_probs.get("version"),
        "dgrtg_home": matchup.get("dgrtg_home"),
        "dgrtg_away": matchup.get("dgrtg_away"),
        "rating_gap": matchup.get("rating_gap"),
    }

    result = {
        "fixture_id": fixture_row["fixture_id"],
        "date_utc": fixture_row.get("date_utc"),
        "league": fixture_row.get("league"),
        "home_name": fixture_row.get("home_name"),
        "away_name": fixture_row.get("away_name"),
        "home_id": fixture_row["home_id"],
        "away_id": fixture_row["away_id"],
        "model_version": version,
        "lean": lean,
        "confidence": confidence,
        "match_character": character,
        "score": round(score, 4),
        "scores": weighted,
        "drivers": drivers,
        "markets": markets,
        "probs": probs_out,
        "dgrtg_home": matchup.get("dgrtg_home"),
        "dgrtg_away": matchup.get("dgrtg_away"),
        "rating_gap": matchup.get("rating_gap"),
        "dg_sim_lean": sim_lean(
            proj["home_win_pct"] if proj else None,
            proj["draw_pct"] if proj else None,
            proj["away_win_pct"] if proj else None,
        )
        if proj
        else None,
        "book_lean": book_lean(book),
        "sim_xg_home": proj["sim_xg_home"] if proj else None,
        "sim_xg_away": proj["sim_xg_away"] if proj else None,
        "note": "rule-based composite + Poisson goals — not a trained model",
    }

    if persist:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO prediction (
                fixture_id, snapshot_id, predicted_at, model_version,
                lean, confidence, match_character, score, scores_json, drivers_json,
                markets_json, probs_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result["fixture_id"],
                snapshot_id,
                now,
                version,
                lean,
                confidence,
                character,
                score,
                json.dumps(weighted),
                json.dumps(drivers),
                json.dumps(markets),
                json.dumps(probs_out),
            ),
        )
    return result


def predict_upcoming(
    conn,
    snapshot_id: int,
    *,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM fixture WHERE 1=1"
    params: List[Any] = []
    if date_from:
        sql += " AND date_utc >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND date_utc < ?"
        params.append(date_to)
    sql += " ORDER BY date_utc"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    out = []
    for row in rows:
        pred = predict_fixture(conn, row, snapshot_id)
        if pred:
            out.append(pred)
    logger.info("Predicted %d / %d fixtures", len(out), len(rows))
    return out
