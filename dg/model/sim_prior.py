"""
Extract typed DataGaffer simulation fields and derive market probabilities
from the published Dixon–Coles correct-score matrix (or percents fallback).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _num(v: Any, default: Optional[float] = None) -> Optional[float]:
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _nested(d: Any, *keys: str, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def extract_sim_fields(sim: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Flatten useful sim_stats into a typed dict for projections, features, and Luna.
    Missing fields become None; callers should tolerate sparse payloads.
    """
    sim = sim or {}
    perc = sim.get("percents") if isinstance(sim.get("percents"), dict) else {}
    xg = sim.get("xg") if isinstance(sim.get("xg"), dict) else {}
    pxg = sim.get("projected_xg") if isinstance(sim.get("projected_xg"), dict) else {}
    xgot = pxg.get("xgot") if isinstance(pxg.get("xgot"), dict) else {}
    pxg_xg = pxg.get("xg") if isinstance(pxg.get("xg"), dict) else {}
    sot = sim.get("shots_on_target") if isinstance(sim.get("shots_on_target"), dict) else {}
    shots = sim.get("shots") if isinstance(sim.get("shots"), dict) else {}
    corners = sim.get("corners") if isinstance(sim.get("corners"), dict) else {}
    cards = sim.get("cards") if isinstance(sim.get("cards"), dict) else {}
    sq = sim.get("shot_quality") if isinstance(sim.get("shot_quality"), dict) else {}
    vs = sim.get("value_score") if isinstance(sim.get("value_score"), dict) else {}
    rs = sim.get("regression_score") if isinstance(sim.get("regression_score"), dict) else {}
    alert = sim.get("recent_match_alert") if isinstance(sim.get("recent_match_alert"), dict) else {}
    fh = sim.get("first_half") if isinstance(sim.get("first_half"), dict) else {}
    fh_xg = fh.get("xg") if isinstance(fh.get("xg"), dict) else {}
    fh_sot = fh.get("shots_on_target") if isinstance(fh.get("shots_on_target"), dict) else {}
    cs = sim.get("correct_score") if isinstance(sim.get("correct_score"), dict) else {}
    top5 = cs.get("top_5") if isinstance(cs.get("top_5"), list) else []
    gs = sim.get("goal_sequences") if isinstance(sim.get("goal_sequences"), dict) else {}
    score_first = gs.get("score_first") if isinstance(gs.get("score_first"), dict) else {}
    bcc = sim.get("big_chances_created") if isinstance(sim.get("big_chances_created"), dict) else {}
    sib = sim.get("shots_inside_box") if isinstance(sim.get("shots_inside_box"), dict) else {}
    tilt = sim.get("field_tilt") if isinstance(sim.get("field_tilt"), dict) else {}
    pressure = sim.get("pressure") if isinstance(sim.get("pressure"), dict) else {}

    top_scores: List[Dict[str, Any]] = []
    for row in top5[:5]:
        if not isinstance(row, dict):
            continue
        top_scores.append(
            {
                "score": row.get("score"),
                "home": row.get("home"),
                "away": row.get("away"),
                "probability_pct": _num(row.get("probability_pct")),
            }
        )

    return {
        "sim_xg_home": _num(xg.get("home")),
        "sim_xg_away": _num(xg.get("away")),
        "sim_xg_total": _num(xg.get("total")),
        "projected_xg_home": _num(pxg_xg.get("home")),
        "projected_xg_away": _num(pxg_xg.get("away")),
        "projected_xg_total": _num(pxg_xg.get("total")),
        "xgot_home": _num(xgot.get("home")),
        "xgot_away": _num(xgot.get("away")),
        "xgot_total": _num(xgot.get("total")),
        "sot_home": _num(sot.get("home")),
        "sot_away": _num(sot.get("away")),
        "sot_total": _num(sot.get("total")),
        "shots_home": _num(shots.get("home")),
        "shots_away": _num(shots.get("away")),
        "shots_total": _num(shots.get("total")),
        "corners_home": _num(corners.get("home")),
        "corners_away": _num(corners.get("away")),
        "corners_total": _num(corners.get("total")),
        "cards_home": _num(cards.get("home")),
        "cards_away": _num(cards.get("away")),
        "cards_total": _num(cards.get("total")),
        "shot_accuracy_total": _num(_nested(sq, "shot_accuracy", "total")),
        "conversion_rate_total": _num(_nested(sq, "conversion_rate", "total")),
        "sot_conversion_total": _num(_nested(sq, "sot_conversion", "total")),
        "shot_accuracy_home": _num(_nested(sq, "shot_accuracy", "home")),
        "shot_accuracy_away": _num(_nested(sq, "shot_accuracy", "away")),
        "sot_conversion_home": _num(_nested(sq, "sot_conversion", "home")),
        "sot_conversion_away": _num(_nested(sq, "sot_conversion", "away")),
        "value_score": _num(vs.get("score")),
        "value_btts": _num(vs.get("btts")),
        "value_over_2_5": _num(vs.get("over_2_5")),
        "value_team_total": _num(vs.get("team_total")),
        "regression_score": _num(rs.get("score")),
        "regression_home": _num(rs.get("home")),
        "regression_away": _num(rs.get("away")),
        "congestion_home": bool(alert.get("home")) if alert else False,
        "congestion_away": bool(alert.get("away")) if alert else False,
        "congestion_any": bool(alert.get("home") or alert.get("away")) if alert else False,
        "fh_xg_home": _num(fh_xg.get("home")),
        "fh_xg_away": _num(fh_xg.get("away")),
        "fh_xg_total": _num(fh_xg.get("total")),
        "fh_sot_home": _num(fh_sot.get("home")),
        "fh_sot_away": _num(fh_sot.get("away")),
        "fh_sot_total": _num(fh_sot.get("total")),
        "home_win_pct": _num(perc.get("home_win_pct")),
        "draw_pct": _num(perc.get("draw_pct")),
        "away_win_pct": _num(perc.get("away_win_pct")),
        "over_2_5_pct": _num(perc.get("over_2_5_pct")),
        "over_3_5_pct": _num(perc.get("over_3_5_pct")),
        "btts_pct": _num(perc.get("btts_pct")),
        "fh_home_win_pct": _num(perc.get("fh_home_win_pct")),
        "fh_draw_pct": _num(perc.get("fh_draw_pct")),
        "fh_away_win_pct": _num(perc.get("fh_away_win_pct")),
        "sot_over_8_5_pct": _num(perc.get("sot_over_8_5_pct")),
        "big_chances_total": _num(bcc.get("total")),
        "shots_inside_box_total": _num(sib.get("total")),
        "field_tilt_home": _num(tilt.get("home")),
        "pressure_home": _num(pressure.get("home")),
        "pressure_away": _num(pressure.get("away")),
        "score_first_home_pct": _num(score_first.get("home")),
        "score_first_away_pct": _num(score_first.get("away")),
        "correct_score_model": cs.get("model"),
        "top_scores": top_scores,
        "has_score_matrix": isinstance(cs.get("matrix"), list) and bool(cs.get("matrix")),
        "two_leg_ctx": sim.get("two_leg_ctx"),
        "goal_timeline": sim.get("goal_timeline") if isinstance(sim.get("goal_timeline"), dict) else None,
        "matchup_pace_score": _num(_nested(sim, "matchup_pace", "score")),
    }


def _matrix_to_probs(matrix: List[List[Any]]) -> Optional[Dict[str, float]]:
    """Derive 1X2 / goals / BTTS / team totals from a correct-score probability matrix (%)."""
    if not matrix or not isinstance(matrix, list):
        return None
    rows = []
    for row in matrix:
        if not isinstance(row, (list, tuple)):
            return None
        nums = []
        for cell in row:
            v = _num(cell)
            if v is None:
                return None
            nums.append(v)
        rows.append(nums)
    if not rows or not rows[0]:
        return None

    # Matrices are usually percent (sum ~100); also accept unit probs.
    total = sum(sum(r) for r in rows)
    if total <= 0:
        return None
    scale = 100.0 if total > 2.0 else 1.0

    p_home = p_draw = p_away = 0.0
    p_over_25 = p_over_35 = p_btts = 0.0
    p_home_o15 = p_away_o15 = 0.0
    lam_h = lam_a = 0.0

    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            p = float(cell) / scale
            lam_h += i * p
            lam_a += j * p
            if i > j:
                p_home += p
            elif i < j:
                p_away += p
            else:
                p_draw += p
            goals = i + j
            if goals >= 3:
                p_over_25 += p
            if goals >= 4:
                p_over_35 += p
            if i >= 1 and j >= 1:
                p_btts += p
            if i >= 2:
                p_home_o15 += p
            if j >= 2:
                p_away_o15 += p

    s = p_home + p_draw + p_away
    if s <= 0:
        return None
    p_home, p_draw, p_away = p_home / s, p_draw / s, p_away / s

    return {
        "home": round(p_home, 4),
        "draw": round(p_draw, 4),
        "away": round(p_away, 4),
        "over_2_5": round(min(0.98, max(0.02, p_over_25)), 4),
        "under_2_5": round(min(0.98, max(0.02, 1.0 - p_over_25)), 4),
        "over_3_5": round(min(0.98, max(0.02, p_over_35)), 4),
        "under_3_5": round(min(0.98, max(0.02, 1.0 - p_over_35)), 4),
        "btts_yes": round(min(0.98, max(0.02, p_btts)), 4),
        "btts_no": round(min(0.98, max(0.02, 1.0 - p_btts)), 4),
        "home_over_1_5": round(min(0.98, max(0.02, p_home_o15)), 4),
        "away_over_1_5": round(min(0.98, max(0.02, p_away_o15)), 4),
        "lam_home": round(lam_h, 4),
        "lam_away": round(lam_a, 4),
    }


def _fh_from_timeline_or_perc(
    sim: Dict[str, Any],
    fields: Dict[str, Any],
) -> Dict[str, float]:
    """First-half 1X2 + Over 0.5 from percents, or FH xG Poisson share fallback."""
    out: Dict[str, float] = {}
    ph = fields.get("fh_home_win_pct")
    pd = fields.get("fh_draw_pct")
    pa = fields.get("fh_away_win_pct")
    if ph is not None and pd is not None and pa is not None:
        s = ph + pd + pa
        if s > 0:
            out["fh_home"] = round(ph / s, 4)
            out["fh_draw"] = round(pd / s, 4)
            out["fh_away"] = round(pa / s, 4)
    fh_xg_t = fields.get("fh_xg_total")
    if fh_xg_t is not None and fh_xg_t > 0:
        # P(at least one goal) ≈ 1 - exp(-λ) under independence of halves
        import math

        out["fh_over_0_5"] = round(min(0.98, max(0.02, 1.0 - math.exp(-fh_xg_t))), 4)
        out["fh_under_0_5"] = round(1.0 - out["fh_over_0_5"], 4)
    timeline = fields.get("goal_timeline")
    if isinstance(timeline, dict) and "fh_over_0_5" not in out:
        import math

        fh_lam = 0.0
        for key in ("0_15", "15_30", "30_45"):
            block = timeline.get(key)
            if isinstance(block, dict):
                fh_lam += _num(block.get("home"), 0.0) or 0.0
                fh_lam += _num(block.get("away"), 0.0) or 0.0
        if fh_lam > 0:
            out["fh_over_0_5"] = round(min(0.98, max(0.02, 1.0 - math.exp(-fh_lam))), 4)
            out["fh_under_0_5"] = round(1.0 - out["fh_over_0_5"], 4)
    return out


def _probs_from_percents(fields: Dict[str, Any]) -> Optional[Dict[str, float]]:
    ph = fields.get("home_win_pct")
    pd = fields.get("draw_pct")
    pa = fields.get("away_win_pct")
    if ph is None or pd is None or pa is None:
        return None
    s = ph + pd + pa
    if s <= 0:
        return None
    out: Dict[str, float] = {
        "home": round(ph / s, 4),
        "draw": round(pd / s, 4),
        "away": round(pa / s, 4),
    }
    o25 = fields.get("over_2_5_pct")
    if o25 is not None:
        p = min(0.98, max(0.02, o25 / 100.0))
        out["over_2_5"] = round(p, 4)
        out["under_2_5"] = round(1.0 - p, 4)
    o35 = fields.get("over_3_5_pct")
    if o35 is not None:
        p = min(0.98, max(0.02, o35 / 100.0))
        out["over_3_5"] = round(p, 4)
        out["under_3_5"] = round(1.0 - p, 4)
    btts = fields.get("btts_pct")
    if btts is not None:
        p = min(0.98, max(0.02, btts / 100.0))
        out["btts_yes"] = round(p, 4)
        out["btts_no"] = round(1.0 - p, 4)
    xg_h = fields.get("sim_xg_home") or fields.get("projected_xg_home")
    xg_a = fields.get("sim_xg_away") or fields.get("projected_xg_away")
    if xg_h is not None:
        out["lam_home"] = round(float(xg_h), 4)
    if xg_a is not None:
        out["lam_away"] = round(float(xg_a), 4)
    # Team O1.5 from percents when present
    return out


def derive_sim_probabilities(sim: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Prefer Dixon–Coles correct_score.matrix; fall back to percents.
    Returns None when neither source is usable (caller should use homemade Poisson).
    """
    sim = sim or {}
    fields = extract_sim_fields(sim)
    cs = sim.get("correct_score") if isinstance(sim.get("correct_score"), dict) else {}
    matrix = cs.get("matrix")
    source = "none"
    probs: Optional[Dict[str, float]] = None
    if isinstance(matrix, list) and matrix:
        probs = _matrix_to_probs(matrix)
        if probs:
            source = "dg_dixon_coles"
    if probs is None:
        probs = _probs_from_percents(fields)
        if probs:
            source = "dg_percents"
    if probs is None:
        return None

    fh = _fh_from_timeline_or_perc(sim, fields)
    for k, v in fh.items():
        probs.setdefault(k, v)

    # Team O1.5 from percents if not from matrix
    perc = sim.get("percents") if isinstance(sim.get("percents"), dict) else {}
    for key, pct_key in (
        ("home_over_1_5", "home_o1_5_pct"),
        ("away_over_1_5", "away_o1_5_pct"),
    ):
        if key not in probs and perc.get(pct_key) is not None:
            p = min(0.98, max(0.02, float(perc[pct_key]) / 100.0))
            probs[key] = round(p, 4)

    probs["version"] = "goals_v2_sim_prior"
    probs["prior_source"] = source
    probs["xgot_total"] = fields.get("xgot_total")
    probs["sot_total"] = fields.get("sot_total")
    probs["congestion_any"] = fields.get("congestion_any")
    probs["value_score"] = fields.get("value_score")
    probs["value_over_2_5"] = fields.get("value_over_2_5")
    probs["regression_home"] = fields.get("regression_home")
    probs["regression_away"] = fields.get("regression_away")
    return probs


def congestion_penalty(fields: Dict[str, Any], *, base: float = 0.04) -> float:
    """Down-weight high-energy overs / SOT when either side has a short turnaround."""
    if fields.get("congestion_any"):
        return float(base)
    return 0.0


def luck_fade(fields: Dict[str, Any], *, max_fade: float = 0.05) -> float:
    """
    Positive = fade overs (both sides converting above expected).
    Negative = lean toward overs (underperforming finishing).
    Uses regression_score when present.
    """
    rh = fields.get("regression_home")
    ra = fields.get("regression_away")
    if rh is None and ra is None:
        return 0.0
    avg = ((rh or 0.0) + (ra or 0.0)) / 2.0
    # Higher regression / luck → fade overs
    fade = max(-max_fade, min(max_fade, avg * 0.02))
    return fade


def sot_over_probability(
    sim: Optional[Dict[str, Any]],
    *,
    line: float = 8.5,
    sim_pct: Optional[float] = None,
) -> Tuple[Optional[float], List[str]]:
    """
    SOT Over probability from projected SOT + sim ladder, with shot-quality / xGOT tilt.
    xGOT is finishing quality (not SOT volume); it only nudges when volume is near the line.
    Returns (p_over, driver strings).
    """
    fields = extract_sim_fields(sim)
    drivers: List[str] = []
    parts: List[Tuple[float, float]] = []  # (weight, p)

    if sim_pct is not None:
        raw = float(sim_pct)
        p = min(0.95, max(0.05, raw / 100.0 if raw > 1.5 else raw))
        parts.append((0.45, p))
        drivers.append(f"sim SOT ladder ({p:.0%})")

    sot_t = fields.get("sot_total")
    if sot_t is not None and line > 0:
        import math

        z = (sot_t - line) / 2.0
        p = 1.0 / (1.0 + math.exp(-z))
        p = min(0.92, max(0.08, p))
        parts.append((0.40, p))
        drivers.append(f"SOT proj {sot_t:.1f} vs {line}")

    # Quality tilt: higher shot accuracy / xGOT supports conversion, not volume
    acc = fields.get("shot_accuracy_total")
    xgot = fields.get("xgot_total")
    quality_bias = 0.0
    if acc is not None:
        quality_bias += (acc - 35.0) / 200.0
        drivers.append(f"shot accuracy {acc:.0f}%")
    if xgot is not None and sot_t is not None and line > 0:
        # Near the line, strong xGOT slightly favors Over finishing path
        if abs(sot_t - line) < 2.5:
            quality_bias += max(-0.04, min(0.04, (xgot - 1.5) / 50.0))
            drivers.append(f"xGOT {xgot:.2f} quality tilt")
    if quality_bias != 0.0 and parts:
        # Apply as a small additive on the blended base later
        pass
    elif quality_bias != 0.0:
        p = min(0.85, max(0.15, 0.5 + quality_bias * 2))
        parts.append((0.15, p))

    if not parts:
        return None, drivers

    wsum = sum(w for w, _ in parts)
    p_over = sum(w * p for w, p in parts) / wsum
    p_over = min(0.95, max(0.05, p_over + quality_bias))

    # Congestion / luck adjustments
    pen = congestion_penalty(fields)
    if pen:
        p_over = max(0.05, p_over - pen)
        drivers.append("congestion fade")
    fade = luck_fade(fields)
    if abs(fade) > 0.001:
        p_over = min(0.95, max(0.05, p_over - fade))
        if fade > 0:
            drivers.append("finishing luck fade")
        else:
            drivers.append("underperformance lean")

    return round(p_over, 4), drivers[:4]
