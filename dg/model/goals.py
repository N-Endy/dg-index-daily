"""
Poisson goal-expectation layer from DG Rating ORtg / DRtg.

Independence is assumed between home and away goal counts (no Dixon–Coles
low-score correction). Treat probabilities as directional / exploratory —
not calibrated betting odds.
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from dg import config

GOALS_WEIGHTS_PATH = config.CONFIG_DIR / "weights_goals_v1.yaml"


def load_goals_config(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or GOALS_WEIGHTS_PATH
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid goals weights: {path}")
    return data


def _num(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _blend_rate(stat: float, xg: float, weight: float) -> float:
    """Blend goals-for/against with xG when xG is available and non-zero."""
    if xg > 0.05 and weight > 0:
        return (1.0 - weight) * stat + weight * xg
    return stat


def league_avg_ortg(conn, snapshot_id: int, league_id: Optional[int]) -> float:
    """Mean ORtg (attack rate) within a league for the snapshot."""
    from dg.features.team import league_stats

    stats = league_stats(conn, snapshot_id, league_id)
    ortg = stats.get("ortg") or {}
    mean = ortg.get("mean")
    return float(mean) if mean is not None else 1.35


def expected_goals(
    matchup: Dict[str, Any],
    *,
    league_avg: Optional[float] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> Tuple[float, float]:
    """
    Multiplicative ratio model:
      lam_home = (home_att / L) * (away_def / L) * L * home_adv
    with optional xG blend and coef_adj scaling for cross-league context.
    """
    cfg = cfg or load_goals_config()
    L = float(league_avg if league_avg is not None else cfg.get("default_league_avg", 1.35))
    if L <= 0:
        L = 1.35
    home_adv = float(cfg.get("home_advantage", 1.08))
    xg_w = float(cfg.get("xg_blend_weight", 0.25))
    mn = float(cfg.get("min_lambda", 0.15))
    mx = float(cfg.get("max_lambda", 4.5))

    home_att = _blend_rate(_num(matchup.get("home_ortg"), L), _num(matchup.get("home_xgf")), xg_w)
    away_def = _blend_rate(_num(matchup.get("away_drtg"), L), _num(matchup.get("away_xga")), xg_w)
    away_att = _blend_rate(_num(matchup.get("away_ortg"), L), _num(matchup.get("away_xgf")), xg_w)
    home_def = _blend_rate(_num(matchup.get("home_drtg"), L), _num(matchup.get("home_xga")), xg_w)

    # League coefficient: average of both sides (1.0 for top leagues)
    coef = (_num(matchup.get("home_coef"), 1.0) + _num(matchup.get("away_coef"), 1.0)) / 2.0
    coef = max(0.5, min(1.1, coef))

    lam_h = (home_att / L) * (away_def / L) * L * home_adv * coef
    lam_a = (away_att / L) * (home_def / L) * L * coef

    # Optional blend toward match-level DG sim xG (when present on the matchup).
    sim_w = float(cfg.get("sim_xg_blend_weight", 0.0))
    sim_h = _num(matchup.get("sim_xg_home"))
    sim_a = _num(matchup.get("sim_xg_away"))
    if sim_w > 0 and sim_h > 0.05:
        lam_h = (1.0 - sim_w) * lam_h + sim_w * sim_h
    if sim_w > 0 and sim_a > 0.05:
        lam_a = (1.0 - sim_w) * lam_a + sim_w * sim_a

    return max(mn, min(mx, lam_h)), max(mn, min(mx, lam_a))


@lru_cache(maxsize=512)
def _poisson_pmf_row(lam: float, max_goals: int) -> Tuple[float, ...]:
    """P(K=k) for k=0..max_goals, with residual mass on max_goals."""
    if lam <= 0:
        out = [0.0] * (max_goals + 1)
        out[0] = 1.0
        return tuple(out)
    probs: List[float] = []
    # recursive: p(k+1) = p(k) * lam / (k+1)
    p = math.exp(-lam)
    for k in range(max_goals):
        probs.append(p)
        p = p * lam / (k + 1)
    # residual for k >= max_goals
    probs.append(max(0.0, 1.0 - sum(probs)))
    return tuple(probs)


def _tau(i: int, j: int, lam_h: float, lam_a: float, rho: float) -> float:
    if rho == 0.0:
        return 1.0
    if i == 0 and j == 0:
        return 1.0 - lam_h * lam_a * rho
    if i == 0 and j == 1:
        return 1.0 + lam_h * rho
    if i == 1 and j == 0:
        return 1.0 + lam_a * rho
    if i == 1 and j == 1:
        return 1.0 - rho
    return 1.0


def score_matrix(
    lam_h: float,
    lam_a: float,
    *,
    max_goals: int = 8,
    rho: float = 0.0,
) -> List[List[float]]:
    """Joint score probabilities [home][away], optional Dixon–Coles low-score correction."""
    ph = _poisson_pmf_row(round(lam_h, 4), max_goals)
    pa = _poisson_pmf_row(round(lam_a, 4), max_goals)
    mat = [
        [ph[i] * pa[j] * _tau(i, j, lam_h, lam_a, rho) for j in range(max_goals + 1)]
        for i in range(max_goals + 1)
    ]
    if rho != 0.0:
        total = sum(mat[i][j] for i in range(max_goals + 1) for j in range(max_goals + 1))
        if total > 0:
            mat = [[mat[i][j] / total for j in range(max_goals + 1)] for i in range(max_goals + 1)]
    return mat


def derive_probabilities(
    lam_h: float,
    lam_a: float,
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Full-time and first-half market probabilities from Poisson lambdas."""
    cfg = cfg or load_goals_config()
    max_g = int(cfg.get("max_goals", 8))
    fh_share = float(cfg.get("fh_goal_share", 0.42))
    rho = float(cfg.get("dixon_coles_rho", -0.05)) if cfg.get("dixon_coles_enabled") else 0.0

    mat = score_matrix(lam_h, lam_a, max_goals=max_g, rho=rho)
    p_home = p_draw = p_away = 0.0
    p_over_25 = p_over_35 = p_btts = 0.0
    p_home_o15 = p_away_o15 = 0.0

    for i in range(max_g + 1):
        for j in range(max_g + 1):
            p = mat[i][j]
            if i > j:
                p_home += p
            elif i < j:
                p_away += p
            else:
                p_draw += p
            total = i + j
            if total >= 3:
                p_over_25 += p
            if total >= 4:
                p_over_35 += p
            if i >= 1 and j >= 1:
                p_btts += p
            if i >= 2:
                p_home_o15 += p
            if j >= 2:
                p_away_o15 += p

    fh_h, fh_a = lam_h * fh_share, lam_a * fh_share
    fh_mat = score_matrix(fh_h, fh_a, max_goals=max_g, rho=rho)
    fh_home = fh_draw = fh_away = fh_over_05 = 0.0
    for i in range(max_g + 1):
        for j in range(max_g + 1):
            p = fh_mat[i][j]
            if i > j:
                fh_home += p
            elif i < j:
                fh_away += p
            else:
                fh_draw += p
            if i + j >= 1:
                fh_over_05 += p

    return {
        "lam_home": round(lam_h, 4),
        "lam_away": round(lam_a, 4),
        "home": round(p_home, 4),
        "draw": round(p_draw, 4),
        "away": round(p_away, 4),
        "over_2_5": round(p_over_25, 4),
        "under_2_5": round(1.0 - p_over_25, 4),
        "over_3_5": round(p_over_35, 4),
        "under_3_5": round(1.0 - p_over_35, 4),
        "btts_yes": round(p_btts, 4),
        "btts_no": round(1.0 - p_btts, 4),
        "home_over_1_5": round(p_home_o15, 4),
        "away_over_1_5": round(p_away_o15, 4),
        "fh_home": round(fh_home, 4),
        "fh_draw": round(fh_draw, 4),
        "fh_away": round(fh_away, 4),
        "fh_over_0_5": round(fh_over_05, 4),
        "fh_under_0_5": round(1.0 - fh_over_05, 4),
    }


def predict_goals(
    matchup: Dict[str, Any],
    *,
    league_avg: Optional[float] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Convenience: expected goals + derived market probabilities."""
    cfg = cfg or load_goals_config()
    lam_h, lam_a = expected_goals(matchup, league_avg=league_avg, cfg=cfg)
    probs = derive_probabilities(lam_h, lam_a, cfg=cfg)
    probs["version"] = cfg.get("version", "goals_v1")
    return probs
