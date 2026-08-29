"""Venue-aware matchup features between two teams."""
from __future__ import annotations

from typing import Any, Dict, Optional


def _num(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def build_matchup(
    home: Dict[str, Any],
    away: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Home team's home_* indices vs away team's away_* indices.
    Pressing uses ppda_index (higher = more aggressive pressing).
    """
    if home.get("missing") or away.get("missing"):
        return {"ok": False, "reason": "missing team features"}

    home_ppda_z = _num(home.get("ppda_index_z"))
    away_ppda_z = _num(away.get("ppda_index_z"))
    # Prefer venue-specific if we had venue ppda; fall back to overall
    pressing_mismatch = home_ppda_z - away_ppda_z

    home_pace = _num(home.get("home_pace_index"), _num(home.get("pace_index")))
    away_pace = _num(away.get("away_pace_index"), _num(away.get("pace_index")))
    pace_clash = home_pace + away_pace

    home_nec = _num(home.get("home_nec_index"), _num(home.get("nec_index")))
    away_nec = _num(away.get("away_nec_index"), _num(away.get("nec_index")))
    home_ctrl = _num(home.get("home_control_index"), _num(home.get("control_index")))
    away_ctrl = _num(away.get("away_control_index"), _num(away.get("control_index")))
    attack_vs_control_home = home_nec - away_ctrl
    attack_vs_control_away = away_nec - home_ctrl

    home_agix = _num(home.get("home_agix_index"), _num(home.get("agix_index")))
    away_agix = _num(away.get("away_agix_index"), _num(away.get("agix_index")))
    aggression_asymmetry = home_agix - away_agix

    home_off = _num(home.get("home_off_eff_index"))
    away_def = _num(away.get("away_def_eff_index"))
    efficiency_edge = home_off - away_def

    home_trend = _num(home.get("dgr_index_trend"))
    away_trend = _num(away.get("dgr_index_trend"))
    form_trend = home_trend - away_trend

    away_off = _num(away.get("away_off_eff_index"))
    home_def = _num(home.get("home_def_eff_index"))
    away_efficiency_edge = away_off - home_def

    # Venue-aware power rating gap (home team's home_rating vs away team's away_rating)
    home_venue_rtg = _num(home.get("home_rating"), _num(home.get("dgrtg")))
    away_venue_rtg = _num(away.get("away_rating"), _num(away.get("dgrtg")))
    # When falling back to dgrtg both sides, gap is still meaningful; when using venue
    # ratings the scale differs (can be negative) — normalize by typical spread ~1.0
    rating_gap = home_venue_rtg - away_venue_rtg
    dgrtg_home = _num(home.get("dgrtg"))
    dgrtg_away = _num(away.get("dgrtg"))
    dgrtg_gap = dgrtg_home - dgrtg_away

    cons_h = home.get("consistency")
    cons_a = away.get("consistency")
    if cons_h is not None and cons_a is not None:
        consistency_mean = (_num(cons_h) + _num(cons_a)) / 2.0
    elif cons_h is not None:
        consistency_mean = _num(cons_h)
    elif cons_a is not None:
        consistency_mean = _num(cons_a)
    else:
        consistency_mean = 0.55  # neutral mid when missing

    luck_gap = _num(home.get("luck_per")) - _num(away.get("luck_per"))

    nec_sum = home_nec + away_nec
    agix_sum = home_agix + away_agix
    control_sum = home_ctrl + away_ctrl
    # Both teams aggressive pressing → higher card/chaos environment
    pressing_intensity = abs(home_ppda_z) + abs(away_ppda_z)

    return {
        "ok": True,
        "home_team": home.get("team"),
        "away_team": away.get("team"),
        "home_id": home.get("team_id"),
        "away_id": away.get("team_id"),
        "history_n_home": home.get("history_n", 0),
        "history_n_away": away.get("history_n", 0),
        "pressing_mismatch": pressing_mismatch,
        "pressing_intensity": pressing_intensity,
        "pace_clash": pace_clash,
        "attack_vs_control_home": attack_vs_control_home,
        "attack_vs_control_away": attack_vs_control_away,
        "aggression_asymmetry": aggression_asymmetry,
        "efficiency_edge": efficiency_edge,
        "away_efficiency_edge": away_efficiency_edge,
        "form_trend": form_trend,
        "rating_gap": rating_gap,
        "dgrtg_gap": dgrtg_gap,
        "dgrtg_home": dgrtg_home,
        "dgrtg_away": dgrtg_away,
        "home_venue_rating": home_venue_rtg,
        "away_venue_rating": away_venue_rtg,
        "consistency_mean": consistency_mean,
        "luck_gap": luck_gap,
        "home_pace": home_pace,
        "away_pace": away_pace,
        "home_agix": home_agix,
        "away_agix": away_agix,
        "home_nec": home_nec,
        "away_nec": away_nec,
        "nec_sum": nec_sum,
        "agix_sum": agix_sum,
        "control_sum": control_sum,
        # Pass through attack/defence rates for Poisson layer
        "home_ortg": _num(home.get("ortg"), _num(home.get("gf_per"))),
        "home_drtg": _num(home.get("drtg"), _num(home.get("ga_per"))),
        "away_ortg": _num(away.get("ortg"), _num(away.get("gf_per"))),
        "away_drtg": _num(away.get("drtg"), _num(away.get("ga_per"))),
        "home_xgf": _num(home.get("xgf_per")),
        "home_xga": _num(home.get("xga_per")),
        "away_xgf": _num(away.get("xgf_per")),
        "away_xga": _num(away.get("xga_per")),
        "home_coef": _num(home.get("coef_adj"), 1.0),
        "away_coef": _num(away.get("coef_adj"), 1.0),
        "league_id": home.get("league_id") or away.get("league_id"),
    }


def book_lean(book_odds: Optional[Dict[str, Any]]) -> Optional[str]:
    if not book_odds:
        return None
    h = book_odds.get("home_win")
    d = book_odds.get("draw")
    a = book_odds.get("away_win")
    if not all(isinstance(x, (int, float)) and x > 0 for x in (h, d, a)):
        return None
    # Lowest odds = favourite
    pairs = [("Home", float(h)), ("Draw", float(d)), ("Away", float(a))]  # type: ignore[arg-type]
    pairs.sort(key=lambda x: x[1])
    return pairs[0][0]


def sim_lean(home_pct: Optional[float], draw_pct: Optional[float], away_pct: Optional[float]) -> Optional[str]:
    if home_pct is None or draw_pct is None or away_pct is None:
        return None
    pairs = [("Home", home_pct), ("Draw", draw_pct), ("Away", away_pct)]
    pairs.sort(key=lambda x: -x[1])
    return pairs[0][0]
