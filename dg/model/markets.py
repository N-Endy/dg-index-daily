"""Multi-market rule engines (goals, BTTS, FH, corners, shots, cards)."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from dg import config
from dg.model.registry import weights_hash

MARKETS_WEIGHTS_PATH = config.CONFIG_DIR / "weights_markets_v1.yaml"

DEFAULT_MARKET_LINES = {
    "goals_2_5": 2.5,
    "goals_3_5": 3.5,
    "team_goals_home_1_5": 1.5,
    "team_goals_away_1_5": 1.5,
    "fh_over_0_5": 0.5,
    "corners_9_5": 9.5,
    "shots_25_5": 25.5,
    "sot_8_5": 8.5,
    "cards_3_5": 3.5,
}

MARKET_LINE_LADDERS = {
    "corners_9_5": ("corners_over_{}_pct", (7.5, 8.5, 9.5, 10.5, 11.5), 9.5),
    "shots_25_5": ("shots_over_{}_pct", (24.5, 25.5, 26.5, 27.5, 28.5), 25.5),
    "sot_8_5": ("sot_over_{}_pct", (7.5, 8.5, 9.5, 10.5, 11.5), 8.5),
}


def load_markets_config(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or MARKETS_WEIGHTS_PATH
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid markets weights: {path}")
    return data


def markets_model_tag(path: Optional[Path] = None) -> str:
    path = path or MARKETS_WEIGHTS_PATH
    data = load_markets_config(path)
    base = data.get("version", "markets_v1")
    return f"{base}_{weights_hash(path)}"


def _line_suffix(line: float) -> str:
    if line == int(line):
        return str(int(line))
    return str(line).replace(".", "_")


def _pct_key_for_line(pattern: str, line: float) -> str:
    return pattern.format(_line_suffix(line))


def select_line(perc: Dict[str, Any], market_key: str) -> Tuple[float, Optional[float]]:
    """Pick the most decisive sim line within the configured probability band."""
    default = DEFAULT_MARKET_LINES.get(market_key, 9.5)
    ladder_spec = MARKET_LINE_LADDERS.get(market_key)
    if not ladder_spec:
        return default, None
    pattern, ladder, fallback = ladder_spec
    if not config.MARKET_DYNAMIC_LINES:
        key = _pct_key_for_line(pattern, fallback)
        raw = perc.get(key)
        try:
            return fallback, float(raw) if raw is not None else None
        except (TypeError, ValueError):
            return fallback, None

    lo = config.MARKET_LINE_MIN_PCT
    hi = config.MARKET_LINE_MAX_PCT
    best_line = fallback
    best_pct: Optional[float] = None
    best_score = -1.0
    for line in ladder:
        key = _pct_key_for_line(pattern, line)
        raw = perc.get(key)
        if raw is None:
            continue
        try:
            p = float(raw)
        except (TypeError, ValueError):
            continue
        if p < lo or p > hi:
            continue
        score = abs(p - 50.0)
        if score > best_score:
            best_score = score
            best_line = line
            best_pct = p
    if best_pct is None:
        key = _pct_key_for_line(pattern, fallback)
        raw = perc.get(key)
        try:
            best_pct = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            best_pct = None
        best_line = fallback
    return best_line, best_pct


def market_line_from_dict(market: Optional[Dict[str, Any]], key: str) -> float:
    if isinstance(market, dict) and market.get("line") is not None:
        try:
            return float(market["line"])
        except (TypeError, ValueError):
            pass
    return DEFAULT_MARKET_LINES.get(key, 9.5)


def extract_market_lines(markets: Dict[str, Any]) -> Dict[str, float]:
    lines: Dict[str, float] = {}
    for key in MARKET_ORDER:
        m = markets.get(key)
        if isinstance(m, dict):
            lines[key] = market_line_from_dict(m, key)
    return lines


def _num(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _conf(score: float, matchup: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    c = cfg.get("confidence") or {}
    hist = min(int(matchup.get("history_n_home") or 0), int(matchup.get("history_n_away") or 0))
    abs_s = abs(score)
    high = float(c.get("high_abs_score_gte", 0.30))
    med = float(c.get("medium_abs_score_gte", 0.12))
    min_hist = int(c.get("min_history_for_high", 2))
    min_cons = float(c.get("min_consistency_for_high", 0.55))
    cons = float(matchup.get("consistency_mean") or 0.55)
    if abs_s >= high and hist >= min_hist and cons >= min_cons:
        return "high"
    if abs_s >= med:
        return "medium"
    return "low"


def _weighted(
    components: Dict[str, float],
    weights: Dict[str, Any],
) -> Tuple[float, Dict[str, float], List[str]]:
    total = 0.0
    weighted: Dict[str, float] = {}
    for key, raw in components.items():
        w = float(weights.get(key, 0.0))
        contrib = w * raw
        weighted[key] = contrib
        total += contrib
    drivers = sorted(weighted.items(), key=lambda x: abs(x[1]), reverse=True)
    lines = []
    for k, v in drivers[:3]:
        sign = "+" if v >= 0 else ""
        lines.append(f"{k.replace('_', ' ')} ({sign}{v:.2f})")
    return total, weighted, lines


def _binary_lean(score: float, pos: str, neg: str, *, dead: float = 0.06) -> str:
    if score > dead:
        return pos
    if score < -dead:
        return neg
    return pos if score >= 0 else neg


def _lean_from_prob(p_pos: float, pos: str, neg: str) -> str:
    return pos if p_pos >= 0.5 else neg


def _lean_side_prob(p_pos: Optional[float], lean: str, pos: str) -> Optional[float]:
    """Convert P(positive side) into P(lean side)."""
    if p_pos is None:
        return None
    p = min(0.98, max(0.02, float(p_pos)))
    return p if lean == pos else 1.0 - p


def _heuristic_p_pos(score: float) -> float:
    """Linear score ramp used when no distributional model is available."""
    return min(0.85, max(0.15, 0.5 + float(score) * 0.3))


def _sim_pct_p_pos(pct: Optional[float], score: float) -> float:
    """Prefer DG sim over-% as P(Over); fall back to the style score ramp."""
    if pct is not None:
        try:
            return min(0.98, max(0.02, float(pct) / 100.0))
        except (TypeError, ValueError):
            pass
    return _heuristic_p_pos(score)


def _fh_sim_over_p(fh_xg_total: float) -> Optional[float]:
    """Approximate P(FH Over 0.5) from first-half sim xG under independence."""
    if fh_xg_total <= 0:
        return None
    return min(0.98, max(0.02, 1.0 - math.exp(-float(fh_xg_total))))


def _ou_from_odds(over: Any, under: Any) -> Optional[str]:
    try:
        o, u = float(over), float(under)
    except (TypeError, ValueError):
        return None
    if o <= 0 or u <= 0:
        return None
    return "Over" if o < u else "Under"


def _yn_from_odds(yes: Any, no: Any) -> Optional[str]:
    try:
        y, n = float(yes), float(no)
    except (TypeError, ValueError):
        return None
    if y <= 0 or n <= 0:
        return None
    return "Yes" if y < n else "No"


def _ou_from_pct(over_pct: Any, *, threshold: float = 50.0) -> Optional[str]:
    if over_pct is None:
        return None
    try:
        p = float(over_pct)
    except (TypeError, ValueError):
        return None
    return "Over" if p >= threshold else "Under"


def _pack(
    *,
    key: str,
    label: str,
    lean: str,
    confidence: str,
    score: float,
    drivers: List[str],
    dg_lean: Optional[str] = None,
    book_lean: Optional[str] = None,
    prob: Optional[float] = None,
    line: Optional[float] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "key": key,
        "label": label,
        "lean": lean,
        "confidence": confidence,
        "score": round(score, 4),
        "drivers": drivers,
        "dg_lean": dg_lean,
        "book_lean": book_lean,
    }
    if line is not None:
        out["line"] = line
    if prob is not None:
        out["prob"] = round(float(prob), 4)
    return out


def predict_markets(
    matchup: Dict[str, Any],
    *,
    book: Optional[Dict[str, Any]] = None,
    sim: Optional[Dict[str, Any]] = None,
    cfg: Optional[Dict[str, Any]] = None,
    goal_probs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Return dict of market predictions keyed by market id.
    Positive composite scores favour Over / Yes / Home (for FH 1X2).
    When goal_probs is provided, goals-related markets use Poisson as primary.
    """
    cfg = cfg or load_markets_config()
    book = book or {}
    sim = sim or {}
    gp = goal_probs or {}
    perc = sim.get("percents") or {}
    xg = sim.get("xg") or {}
    fh = (sim.get("first_half") or {}).get("xg") or {}
    corners = sim.get("corners") or {}
    shots = sim.get("shots") or {}
    sot = sim.get("shots_on_target") or {}
    cards = sim.get("cards") or {}

    pace = _num(matchup.get("pace_clash"))
    nec_sum = _num(matchup.get("nec_sum"), _num(matchup.get("home_nec")) + _num(matchup.get("away_nec")))
    if nec_sum == 0:
        nec_sum = 100.0 + _num(matchup.get("attack_vs_control_home")) + _num(matchup.get("attack_vs_control_away"))
    agix_sum = _num(matchup.get("agix_sum"), _num(matchup.get("home_agix")) + _num(matchup.get("away_agix")))
    control_sum = _num(matchup.get("control_sum"), 100.0)
    pressing = _num(matchup.get("pressing_mismatch"))
    pressing_intensity = _num(matchup.get("pressing_intensity"), abs(pressing))
    eff = _num(matchup.get("efficiency_edge"))
    away_eff = _num(matchup.get("away_efficiency_edge"))
    form = _num(matchup.get("form_trend"))
    agg_asym = _num(matchup.get("aggression_asymmetry"))

    sim_xg_h = _num(xg.get("home"), _num(matchup.get("sim_xg_home")))
    sim_xg_a = _num(xg.get("away"), _num(matchup.get("sim_xg_away")))
    sim_xg_t = sim_xg_h + sim_xg_a
    fh_xg_h = _num(fh.get("home"))
    fh_xg_a = _num(fh.get("away"))
    fh_xg_t = fh_xg_h + fh_xg_a
    corners_t = _num(corners.get("total"))
    shots_t = _num(shots.get("total"))
    sot_t = _num(sot.get("total"))
    cards_t = _num(cards.get("total"))

    out: Dict[str, Any] = {"version": markets_model_tag()}

    # --- Goals O/U 2.5 ---
    p_over = gp.get("over_2_5")
    g_w = cfg.get("goals_2_5") or {}
    g_score, _, g_drv = _weighted(
        {
            "poisson_over_bias": (_num(p_over, 0.5) - 0.5) * 2.0 if p_over is not None else 0.0,
            "pace_clash": (pace - 100.0) / 100.0,
            "nec_sum": (nec_sum - 100.0) / 100.0,
            "xg_total_bias": (sim_xg_t - 2.5) / 2.0 if sim_xg_t else 0.0,
            "sim_over_bias": (_num(perc.get("over_2_5_pct"), 50.0) - 50.0) / 50.0,
        },
        g_w,
    )
    g_lean = (
        _lean_from_prob(_num(p_over, 0.5), "Over", "Under")
        if p_over is not None
        else _binary_lean(g_score, "Over", "Under")
    )
    g_p_pos = float(p_over) if p_over is not None else _heuristic_p_pos(g_score)
    out["goals_2_5"] = _pack(
        key="goals_2_5",
        label="Goals O/U 2.5",
        lean=g_lean,
        confidence=_conf(g_score, matchup, cfg),
        score=g_score,
        drivers=g_drv,
        dg_lean=_ou_from_pct(perc.get("over_2_5_pct")),
        book_lean=_ou_from_odds(book.get("over_2_5"), book.get("under_2_5")),
        prob=_lean_side_prob(g_p_pos, g_lean, "Over"),
        line=2.5,
    )

    # --- Goals O/U 3.5 ---
    p_over35 = gp.get("over_3_5")
    g35_w = cfg.get("goals_3_5") or {}
    g35_score, _, g35_drv = _weighted(
        {
            "poisson_over_bias": (_num(p_over35, 0.5) - 0.5) * 2.0 if p_over35 is not None else 0.0,
            "pace_clash": (pace - 100.0) / 100.0,
            "nec_sum": (nec_sum - 100.0) / 100.0,
            "xg_total_bias": (sim_xg_t - 3.5) / 2.0 if sim_xg_t else 0.0,
            "sim_over_bias": (_num(perc.get("over_3_5_pct"), 50.0) - 50.0) / 50.0,
        },
        g35_w,
    )
    g35_lean = (
        _lean_from_prob(_num(p_over35, 0.5), "Over", "Under")
        if p_over35 is not None
        else _binary_lean(g35_score, "Over", "Under")
    )
    out["goals_3_5"] = _pack(
        key="goals_3_5",
        label="Goals O/U 3.5",
        lean=g35_lean,
        confidence=_conf(g35_score, matchup, cfg),
        score=g35_score,
        drivers=g35_drv,
        dg_lean=_ou_from_pct(perc.get("over_3_5_pct")),
        book_lean=_ou_from_odds(book.get("over_3_5"), book.get("under_3_5")),
        prob=_lean_side_prob(
            float(p_over35) if p_over35 is not None else _heuristic_p_pos(g35_score),
            g35_lean,
            "Over",
        ),
        line=3.5,
    )

    # --- BTTS ---
    p_btts = gp.get("btts_yes")
    b_w = cfg.get("btts") or {}
    b_score, _, b_drv = _weighted(
        {
            "poisson_btts_bias": (_num(p_btts, 0.5) - 0.5) * 2.0 if p_btts is not None else 0.0,
            "nec_sum": (nec_sum - 100.0) / 100.0,
            "pace_clash": (pace - 100.0) / 100.0,
            "control_sum_inv": (100.0 - control_sum) / 100.0,
            "sim_btts_bias": (_num(perc.get("btts_pct"), 50.0) - 50.0) / 50.0,
        },
        b_w,
    )
    b_lean = _lean_from_prob(_num(p_btts, 0.5), "Yes", "No") if p_btts is not None else _binary_lean(b_score, "Yes", "No")
    out["btts"] = _pack(
        key="btts",
        label="BTTS",
        lean=b_lean,
        confidence=_conf(b_score, matchup, cfg),
        score=b_score,
        drivers=b_drv,
        dg_lean=("Yes" if _num(perc.get("btts_pct"), 50) >= 50 else "No") if perc.get("btts_pct") is not None else None,
        book_lean=_yn_from_odds(book.get("btts_yes"), book.get("btts_no")),
        prob=_lean_side_prob(float(p_btts) if p_btts is not None else None, b_lean, "Yes"),
    )

    # --- Team goals home O1.5 ---
    p_ho = gp.get("home_over_1_5")
    th_w = cfg.get("team_goals_home_1_5") or {}
    home_nec = _num(matchup.get("home_nec"), 50.0 + _num(matchup.get("attack_vs_control_home")))
    th_score, _, th_drv = _weighted(
        {
            "poisson_home_o15_bias": (_num(p_ho, 0.5) - 0.5) * 2.0 if p_ho is not None else 0.0,
            "home_nec": (home_nec - 50.0) / 50.0,
            "efficiency_edge": eff / 50.0,
            "home_xg_bias": (sim_xg_h - 1.5) / 1.5 if sim_xg_h else 0.0,
            "sim_home_o1_5_bias": (_num(perc.get("home_o1_5_pct"), 50.0) - 50.0) / 50.0,
        },
        th_w,
    )
    th_lean = _lean_from_prob(_num(p_ho, 0.5), "Over", "Under") if p_ho is not None else _binary_lean(th_score, "Over", "Under")
    out["team_goals_home_1_5"] = _pack(
        key="team_goals_home_1_5",
        label="Home O/U 1.5",
        lean=th_lean,
        confidence=_conf(th_score, matchup, cfg),
        score=th_score,
        drivers=th_drv,
        dg_lean=_ou_from_pct(perc.get("home_o1_5_pct")),
        book_lean=None,
        prob=_lean_side_prob(float(p_ho) if p_ho is not None else None, th_lean, "Over"),
        line=1.5,
    )
    if book.get("home_o1_5"):
        try:
            out["team_goals_home_1_5"]["book_lean"] = "Over" if float(book["home_o1_5"]) < 2.0 else "Under"
        except (TypeError, ValueError):
            pass

    # --- Team goals away O1.5 ---
    p_ao = gp.get("away_over_1_5")
    ta_w = cfg.get("team_goals_away_1_5") or {}
    away_nec = _num(matchup.get("away_nec"), 50.0 + _num(matchup.get("attack_vs_control_away")))
    ta_score, _, ta_drv = _weighted(
        {
            "poisson_away_o15_bias": (_num(p_ao, 0.5) - 0.5) * 2.0 if p_ao is not None else 0.0,
            "away_nec": (away_nec - 50.0) / 50.0,
            "away_efficiency": away_eff / 50.0,
            "away_xg_bias": (sim_xg_a - 1.5) / 1.5 if sim_xg_a else 0.0,
            "sim_away_o1_5_bias": (_num(perc.get("away_o1_5_pct"), 50.0) - 50.0) / 50.0,
        },
        ta_w,
    )
    ta_lean = _lean_from_prob(_num(p_ao, 0.5), "Over", "Under") if p_ao is not None else _binary_lean(ta_score, "Over", "Under")
    out["team_goals_away_1_5"] = _pack(
        key="team_goals_away_1_5",
        label="Away O/U 1.5",
        lean=ta_lean,
        confidence=_conf(ta_score, matchup, cfg),
        score=ta_score,
        drivers=ta_drv,
        dg_lean=_ou_from_pct(perc.get("away_o1_5_pct")),
        book_lean=None,
        prob=_lean_side_prob(float(p_ao) if p_ao is not None else None, ta_lean, "Over"),
        line=1.5,
    )
    if book.get("away_o1_5"):
        try:
            out["team_goals_away_1_5"]["book_lean"] = "Over" if float(book["away_o1_5"]) < 2.0 else "Under"
        except (TypeError, ValueError):
            pass

    # --- First half 1X2 ---
    fh_w = cfg.get("fh_1x2") or {}
    fh_p_h = gp.get("fh_home")
    fh_p_d = gp.get("fh_draw")
    fh_p_a = gp.get("fh_away")
    poisson_fh_edge = 0.0
    if fh_p_h is not None and fh_p_a is not None:
        poisson_fh_edge = float(fh_p_h) - float(fh_p_a)
    fh_score, _, fh_drv = _weighted(
        {
            "poisson_fh_edge": poisson_fh_edge,
            "agix_asymmetry": agg_asym / 50.0,
            "efficiency_edge": eff / 50.0,
            "fh_xg_edge": (fh_xg_h - fh_xg_a) / 1.0 if (fh_xg_h or fh_xg_a) else 0.0,
            "form_trend": form / 5.0,
        },
        fh_w,
    )
    if fh_p_h is not None and fh_p_d is not None and fh_p_a is not None:
        pairs = [("Home", float(fh_p_h)), ("Draw", float(fh_p_d)), ("Away", float(fh_p_a))]
        pairs.sort(key=lambda x: -x[1])
        fh_lean = pairs[0][0]
        fh_prob = pairs[0][1]
    else:
        if fh_score > 0.08:
            fh_lean = "Home"
        elif fh_score < -0.08:
            fh_lean = "Away"
        else:
            fh_lean = "Draw"
        fh_prob = None
    fh_dg = None
    if perc.get("fh_home_win_pct") is not None:
        pairs = [
            ("Home", _num(perc.get("fh_home_win_pct"))),
            ("Draw", _num(perc.get("fh_draw_pct"))),
            ("Away", _num(perc.get("fh_away_win_pct"))),
        ]
        pairs.sort(key=lambda x: -x[1])
        fh_dg = pairs[0][0]
    fh_book = None
    if book.get("fh_home_win") and book.get("fh_draw") and book.get("fh_away_win"):
        pairs_b = [
            ("Home", float(book["fh_home_win"])),
            ("Draw", float(book["fh_draw"])),
            ("Away", float(book["fh_away_win"])),
        ]
        pairs_b.sort(key=lambda x: x[1])
        fh_book = pairs_b[0][0]
    out["fh_1x2"] = _pack(
        key="fh_1x2",
        label="First half 1X2",
        lean=fh_lean,
        confidence=_conf(fh_score, matchup, cfg),
        score=fh_score,
        drivers=fh_drv,
        dg_lean=fh_dg,
        book_lean=fh_book,
        prob=fh_prob,
    )

    # --- FH Over 0.5 ---
    p_fho = gp.get("fh_over_0_5")
    fho_w = cfg.get("fh_over_0_5") or {}
    sim_fh_p = _fh_sim_over_p(fh_xg_t)
    sim_fh_bias = (float(sim_fh_p) - 0.5) * 2.0 if sim_fh_p is not None else 0.0
    fho_score, _, fho_drv = _weighted(
        {
            "poisson_fh_over_bias": (_num(p_fho, 0.5) - 0.5) * 2.0 if p_fho is not None else 0.0,
            "agix_sum": (agix_sum - 100.0) / 100.0,
            "pace_clash": (pace - 100.0) / 100.0,
            "fh_xg_total_bias": (fh_xg_t - 0.5) / 1.0 if fh_xg_t else (pace - 100.0) / 100.0,
            "sim_fh_over_bias": sim_fh_bias,
        },
        fho_w,
    )
    fho_lean = _lean_from_prob(_num(p_fho, 0.5), "Over", "Under") if p_fho is not None else _binary_lean(fho_score, "Over", "Under")
    fh_over_book = _ou_from_odds(book.get("fh_over_0_5"), book.get("fh_under_0_5"))
    if sim_fh_p is not None:
        fh_dg = "Over" if sim_fh_p >= 0.5 else "Under"
    else:
        fh_dg = "Over" if fh_xg_t >= math.log(2) else ("Under" if fh_xg_t > 0 else None)
    out["fh_over_0_5"] = _pack(
        key="fh_over_0_5",
        label="FH O/U 0.5",
        lean=fho_lean,
        confidence=_conf(fho_score, matchup, cfg),
        score=fho_score,
        drivers=fho_drv,
        dg_lean=fh_dg,
        book_lean=fh_over_book,
        prob=_lean_side_prob(float(p_fho) if p_fho is not None else None, fho_lean, "Over"),
        line=0.5,
    )

    # --- Corners O/U (dynamic line) ---
    corners_line, corners_pct = select_line(perc, "corners_9_5")
    c_w = cfg.get("corners_9_5") or {}
    c_score, _, c_drv = _weighted(
        {
            "pace_clash": (pace - 100.0) / 100.0,
            "nec_sum": (nec_sum - 100.0) / 100.0,
            "corners_proj_bias": (corners_t - corners_line) / 5.0 if corners_t else (pace - 100.0) / 100.0,
            "sim_corners_over_bias": (_num(corners_pct, 50.0) - 50.0) / 50.0,
        },
        c_w,
    )
    c_lean = _binary_lean(c_score, "Over", "Under")
    out["corners_9_5"] = _pack(
        key="corners_9_5",
        label=f"Corners O/U {corners_line}",
        lean=c_lean,
        confidence=_conf(c_score, matchup, cfg),
        score=c_score,
        drivers=c_drv,
        dg_lean=_ou_from_pct(corners_pct),
        book_lean=None,
        prob=_lean_side_prob(_sim_pct_p_pos(corners_pct, c_score), c_lean, "Over"),
        line=corners_line,
    )

    # --- Shots O/U (dynamic line) ---
    shots_line, shots_pct = select_line(perc, "shots_25_5")
    s_w = cfg.get("shots_25_5") or {}
    s_score, _, s_drv = _weighted(
        {
            "pace_clash": (pace - 100.0) / 100.0,
            "shots_proj_bias": (shots_t - shots_line) / 10.0 if shots_t else (pace - 100.0) / 100.0,
            "sim_shots_over_bias": (_num(shots_pct, 50.0) - 50.0) / 50.0,
        },
        s_w,
    )
    s_lean = _binary_lean(s_score, "Over", "Under")
    out["shots_25_5"] = _pack(
        key="shots_25_5",
        label=f"Shots O/U {shots_line}",
        lean=s_lean,
        confidence=_conf(s_score, matchup, cfg),
        score=s_score,
        drivers=s_drv,
        dg_lean=_ou_from_pct(shots_pct),
        book_lean=None,
        prob=_lean_side_prob(_sim_pct_p_pos(shots_pct, s_score), s_lean, "Over"),
        line=shots_line,
    )

    # --- SOT O/U (dynamic line) ---
    sot_line, sot_pct = select_line(perc, "sot_8_5")
    so_w = cfg.get("sot_8_5") or {}
    so_score, _, so_drv = _weighted(
        {
            "pace_clash": (pace - 100.0) / 100.0,
            "nec_sum": (nec_sum - 100.0) / 100.0,
            "sot_proj_bias": (sot_t - sot_line) / 4.0 if sot_t else (pace - 100.0) / 100.0,
            "sim_sot_over_bias": (_num(sot_pct, 50.0) - 50.0) / 50.0,
        },
        so_w,
    )
    so_lean = _binary_lean(so_score, "Over", "Under")
    out["sot_8_5"] = _pack(
        key="sot_8_5",
        label=f"SOT O/U {sot_line}",
        lean=so_lean,
        confidence=_conf(so_score, matchup, cfg),
        score=so_score,
        drivers=so_drv,
        dg_lean=_ou_from_pct(sot_pct),
        book_lean=None,
        prob=_lean_side_prob(_sim_pct_p_pos(sot_pct, so_score), so_lean, "Over"),
        line=sot_line,
    )

    # --- Cards O/U 3.5 ---
    cards_line = 3.5
    cd_w = cfg.get("cards_3_5") or {}
    cd_score, _, cd_drv = _weighted(
        {
            "agix_sum": (agix_sum - 100.0) / 100.0,
            "pressing_intensity": pressing_intensity / 2.0,
            "cards_proj_bias": (cards_t - cards_line) / 2.0 if cards_t else (agix_sum - 100.0) / 100.0,
            "aggression_volatility": abs(agg_asym) / 50.0,
        },
        cd_w,
    )
    cd_lean = _binary_lean(cd_score, "Over", "Under")
    out["cards_3_5"] = _pack(
        key="cards_3_5",
        label=f"Cards O/U {cards_line}",
        lean=cd_lean,
        confidence=_conf(cd_score, matchup, cfg),
        score=cd_score,
        drivers=cd_drv,
        dg_lean=("Over" if cards_t >= cards_line else "Under") if cards_t else None,
        book_lean=None,
        prob=_lean_side_prob(_heuristic_p_pos(cd_score), cd_lean, "Over"),
        line=cards_line,
    )

    return out


# Ordered keys for UI
MARKET_ORDER = (
    "goals_2_5",
    "goals_3_5",
    "btts",
    "team_goals_home_1_5",
    "team_goals_away_1_5",
    "fh_1x2",
    "fh_over_0_5",
    "corners_9_5",
    "shots_25_5",
    "sot_8_5",
    "cards_3_5",
)

# Valid lean sides per market (shared by form + filter validator)
MARKET_SIDES = {
    "goals_2_5": ("Over", "Under"),
    "goals_3_5": ("Over", "Under"),
    "btts": ("Yes", "No"),
    "team_goals_home_1_5": ("Over", "Under"),
    "team_goals_away_1_5": ("Over", "Under"),
    "fh_1x2": ("Home", "Draw", "Away"),
    "fh_over_0_5": ("Over", "Under"),
    "corners_9_5": ("Over", "Under"),
    "shots_25_5": ("Over", "Under"),
    "sot_8_5": ("Over", "Under"),
    "cards_3_5": ("Over", "Under"),
}
