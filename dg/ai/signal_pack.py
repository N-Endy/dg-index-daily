"""Compact DataGaffer signal packs for AI Picks (no dashboard lean chips)."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dg.features.matchup import book_lean as book_1x2_lean
from dg.features.matchup import sim_lean as sim_1x2_lean
from dg.model.evaluate import _devig, _devig2
from dg.model.markets import (
    MARKET_LINE_LADDERS,
    MARKET_ORDER,
    MARKET_SIDES,
    _ou_from_odds,
    _ou_from_pct,
    _pct_key_for_line,
    _yn_from_odds,
    select_line,
)
from dg.web.plain_language import (
    agreement_hint,
    confidence_blurb,
    lean_plain,
    market_chip_label,
    market_lean_plain,
    probability_plain,
)

# Catalog the LLM may pick from (market → valid leans).
AI_MARKET_LEANS: Dict[str, Tuple[str, ...]] = {
    "match_1x2": ("Home", "Draw", "Away"),
    **{k: tuple(v) for k, v in MARKET_SIDES.items()},
}

AI_MARKET_KEYS: Tuple[str, ...] = ("match_1x2",) + tuple(MARKET_ORDER)

# Compact book keys sent in the signal pack.
_BOOK_KEYS = (
    "home_win",
    "draw",
    "away_win",
    "over_2_5",
    "under_2_5",
    "over_3_5",
    "under_3_5",
    "btts_yes",
    "btts_no",
    "fh_over_0_5",
    "fh_under_0_5",
    "fh_home_win",
    "fh_draw",
    "fh_away_win",
    "home_o1_5",
    "away_o1_5",
)

# Compact sim percent keys.
_SIM_PCT_KEYS = (
    "home_win_pct",
    "draw_pct",
    "away_win_pct",
    "over_2_5_pct",
    "under_2_5_pct",
    "over_3_5_pct",
    "under_3_5_pct",
    "btts_pct",
    "btts_no_pct",
    "fh_home_win_pct",
    "fh_draw_pct",
    "fh_away_win_pct",
    "home_o1_5_pct",
    "away_o1_5_pct",
    "corners_over_9_5_pct",
    "shots_over_25_5_pct",
    "sot_over_8_5_pct",
)


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_to_unit(pct: Any) -> Optional[float]:
    """Convert DG percent (0–100 or 0–1) to unit probability."""
    p = _as_float(pct)
    if p is None:
        return None
    if p > 1.5:
        p = p / 100.0
    return max(0.01, min(0.99, p))


def _probs_dict(pred: Dict[str, Any]) -> Dict[str, Any]:
    probs = pred.get("probs")
    if probs is None and pred.get("probs_json"):
        try:
            probs = json.loads(pred["probs_json"])
        except (json.JSONDecodeError, TypeError):
            probs = {}
    return probs if isinstance(probs, dict) else {}


def _sim_dict(pred: Dict[str, Any]) -> Dict[str, Any]:
    sim = pred.get("sim_stats")
    if isinstance(sim, dict):
        return sim
    raw = pred.get("sim_stats_json")
    if raw:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _book_dict(pred: Dict[str, Any]) -> Dict[str, Any]:
    book = pred.get("book_odds")
    if isinstance(book, dict):
        return book
    raw = pred.get("book_odds_json")
    if raw:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _markets_dict(pred: Dict[str, Any]) -> Dict[str, Any]:
    markets = pred.get("markets")
    if markets is None and pred.get("markets_json"):
        try:
            markets = json.loads(pred["markets_json"])
        except (json.JSONDecodeError, TypeError):
            markets = {}
    return markets if isinstance(markets, dict) else {}


def _prop_sim_over_pct(pred: Dict[str, Any], market_key: str) -> Optional[float]:
    """DG over-% for corners/shots/SOT using markets line when present, else select_line."""
    sim = _sim_dict(pred)
    perc = sim.get("percents") if isinstance(sim.get("percents"), dict) else {}
    markets = _markets_dict(pred)
    m = markets.get(market_key) if isinstance(markets.get(market_key), dict) else {}
    ladder = MARKET_LINE_LADDERS.get(market_key)
    if m.get("line") is not None and ladder:
        pattern = ladder[0]
        try:
            key = _pct_key_for_line(pattern, float(m["line"]))
            if perc.get(key) is not None:
                return float(perc[key])
        except (TypeError, ValueError):
            pass
    _line, pct = select_line(perc, market_key)
    return float(pct) if pct is not None else None


def is_valid_ai_lean(market_key: str, lean: Any) -> bool:
    key = str(market_key or "").strip()
    side = str(lean or "").strip()
    allowed = AI_MARKET_LEANS.get(key)
    if not allowed or not side:
        return False
    return side in allowed


def allowed_markets_payload() -> List[Dict[str, Any]]:
    return [
        {"marketKey": k, "leans": list(AI_MARKET_LEANS[k])}
        for k in AI_MARKET_KEYS
        if k in AI_MARKET_LEANS
    ]


def build_fixture_signal_pack(pred: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compact DG/book/model-math pack for one fixture.
    Omits our dashboard lean / prob / confidence chips.
    """
    sim = _sim_dict(pred)
    book = _book_dict(pred)
    perc = sim.get("percents") if isinstance(sim.get("percents"), dict) else {}
    xg = sim.get("xg") if isinstance(sim.get("xg"), dict) else {}
    pace = sim.get("matchup_pace") if isinstance(sim.get("matchup_pace"), dict) else {}
    probs = _probs_dict(pred)

    sim_pcts = {k: perc.get(k) for k in _SIM_PCT_KEYS if perc.get(k) is not None}
    # Flattened projection columns as fallbacks.
    for src_key, dest in (
        ("home_win_pct", "home_win_pct"),
        ("draw_pct", "draw_pct"),
        ("away_win_pct", "away_win_pct"),
        ("over_2_5_pct", "over_2_5_pct"),
        ("btts_pct", "btts_pct"),
    ):
        if dest not in sim_pcts and pred.get(src_key) is not None:
            sim_pcts[dest] = pred.get(src_key)

    # Include the sim % for each selected prop line (may differ from default 9.5/25.5/8.5).
    markets = _markets_dict(pred)
    for market_key in ("corners_9_5", "shots_25_5", "sot_8_5"):
        ladder = MARKET_LINE_LADDERS.get(market_key)
        if not ladder:
            continue
        pattern = ladder[0]
        m = markets.get(market_key) if isinstance(markets.get(market_key), dict) else {}
        line = m.get("line")
        if line is None:
            line, _pct_val = select_line(perc, market_key)
        try:
            pct_key = _pct_key_for_line(pattern, float(line))
        except (TypeError, ValueError):
            continue
        if perc.get(pct_key) is not None:
            sim_pcts[pct_key] = perc[pct_key]

    book_out = {k: book.get(k) for k in _BOOK_KEYS if book.get(k) is not None}

    xg_home = _as_float(xg.get("home"))
    xg_away = _as_float(xg.get("away"))
    if xg_home is None:
        xg_home = _as_float(pred.get("sim_xg_home"))
    if xg_away is None:
        xg_away = _as_float(pred.get("sim_xg_away"))

    model_math = {
        "lamHome": probs.get("lam_home"),
        "lamAway": probs.get("lam_away"),
        "poissonOver25": probs.get("over_2_5"),
        "poissonBttsYes": probs.get("btts_yes"),
    }
    # Drop empty model math.
    model_math = {k: v for k, v in model_math.items() if v is not None}

    return {
        "fixtureId": pred.get("fixture_id"),
        "homeTeam": pred.get("home_name"),
        "awayTeam": pred.get("away_name"),
        "league": pred.get("league_display") or pred.get("league"),
        "kickoff": pred.get("kickoff_display") or pred.get("date_utc"),
        "strength": pred.get("strength_label"),
        "style": pred.get("style_label"),
        "sim": {
            "xgHome": xg_home,
            "xgAway": xg_away,
            "matchupPace": _as_float(pace.get("score"))
            if pace
            else _as_float(pred.get("matchup_pace_score")),
            "percents": sim_pcts,
        },
        "book": book_out,
        "modelMath": model_math,
        "note": (
            "modelMath is optional Poisson context from our ratings engine — "
            "not a pick to rubber-stamp. Prefer DataGaffer sim and book signals."
        ),
        "allowedMarkets": allowed_markets_payload(),
    }


def fixture_has_ai_signals(pred: Dict[str, Any]) -> bool:
    """True when the fixture has usable DG projection or book data for AI."""
    sim = _sim_dict(pred)
    book = _book_dict(pred)
    if book:
        return True
    if sim:
        return True
    if pred.get("sim_xg_home") is not None or pred.get("sim_xg_away") is not None:
        return True
    if pred.get("home_win_pct") is not None:
        return True
    return False


def dg_book_sides_for_market(
    pred: Dict[str, Any], market_key: str
) -> Tuple[Optional[str], Optional[str]]:
    """Implied DG and book sides for a market (independent of our lean)."""
    sim = _sim_dict(pred)
    book = _book_dict(pred)
    perc = sim.get("percents") if isinstance(sim.get("percents"), dict) else {}

    def _pct(key: str) -> Any:
        if perc.get(key) is not None:
            return perc.get(key)
        return pred.get(key)

    if market_key == "match_1x2":
        dg = sim_1x2_lean(
            _pct("home_win_pct") if _pct("home_win_pct") is not None else pred.get("home_win_pct"),
            _pct("draw_pct") if _pct("draw_pct") is not None else pred.get("draw_pct"),
            _pct("away_win_pct") if _pct("away_win_pct") is not None else pred.get("away_win_pct"),
        )
        if dg is None:
            dg = pred.get("dg_sim_lean")
        bk = book_1x2_lean(book) if book else pred.get("book_lean")
        return dg, bk

    if market_key == "goals_2_5":
        return _ou_from_pct(_pct("over_2_5_pct")), _ou_from_odds(
            book.get("over_2_5"), book.get("under_2_5")
        )
    if market_key == "goals_3_5":
        return _ou_from_pct(_pct("over_3_5_pct")), _ou_from_odds(
            book.get("over_3_5"), book.get("under_3_5")
        )
    if market_key == "btts":
        bp = _pct("btts_pct")
        dg = None
        if bp is not None:
            try:
                dg = "Yes" if float(bp) >= 50 else "No"
            except (TypeError, ValueError):
                dg = None
        return dg, _yn_from_odds(book.get("btts_yes"), book.get("btts_no"))
    if market_key == "team_goals_home_1_5":
        dg = _ou_from_pct(_pct("home_o1_5_pct"))
        bk = None
        if book.get("home_o1_5") is not None:
            try:
                bk = "Over" if float(book["home_o1_5"]) < 2.0 else "Under"
            except (TypeError, ValueError):
                bk = None
        return dg, bk
    if market_key == "team_goals_away_1_5":
        dg = _ou_from_pct(_pct("away_o1_5_pct"))
        bk = None
        if book.get("away_o1_5") is not None:
            try:
                bk = "Over" if float(book["away_o1_5"]) < 2.0 else "Under"
            except (TypeError, ValueError):
                bk = None
        return dg, bk
    if market_key == "fh_1x2":
        dg = sim_1x2_lean(
            _pct("fh_home_win_pct"), _pct("fh_draw_pct"), _pct("fh_away_win_pct")
        )
        bk = None
        if book.get("fh_home_win") and book.get("fh_draw") and book.get("fh_away_win"):
            try:
                ph, pd, pa = _devig(
                    float(book["fh_home_win"]),
                    float(book["fh_draw"]),
                    float(book["fh_away_win"]),
                )
                bk = max(
                    (("Home", ph), ("Draw", pd), ("Away", pa)),
                    key=lambda t: t[1],
                )[0]
            except (TypeError, ValueError):
                bk = None
        return dg, bk
    if market_key == "fh_over_0_5":
        return None, _ou_from_odds(book.get("fh_over_0_5"), book.get("fh_under_0_5"))
    if market_key in ("corners_9_5", "shots_25_5", "sot_8_5"):
        return _ou_from_pct(_prop_sim_over_pct(pred, market_key)), None
    if market_key == "cards_3_5":
        cards = sim.get("cards") if isinstance(sim.get("cards"), dict) else {}
        total = _as_float(cards.get("total"))
        if total is not None:
            return ("Over" if total >= 3.5 else "Under"), None
        return None, None
    return None, None


def reference_prob_for_lean(
    pred: Dict[str, Any], market_key: str, lean: str
) -> Optional[float]:
    """
    Reference probability for calibration banding.
    Prefer DG sim % for the chosen side, else book-implied, else Poisson modelMath.
    """
    sim = _sim_dict(pred)
    book = _book_dict(pred)
    perc = sim.get("percents") if isinstance(sim.get("percents"), dict) else {}
    probs = _probs_dict(pred)
    side = str(lean or "").strip()

    def _pct(key: str) -> Optional[float]:
        if perc.get(key) is not None:
            return _pct_to_unit(perc.get(key))
        return _pct_to_unit(pred.get(key))

    def _side_from_over(over_p: Optional[float]) -> Optional[float]:
        if over_p is None:
            return None
        return over_p if side == "Over" else (1.0 - over_p if side == "Under" else None)

    if market_key == "match_1x2":
        mapping = {
            "Home": _pct("home_win_pct"),
            "Draw": _pct("draw_pct"),
            "Away": _pct("away_win_pct"),
        }
        p = mapping.get(side)
        if p is not None:
            return p
        if book.get("home_win") and book.get("draw") and book.get("away_win"):
            try:
                ph, pd, pa = _devig(
                    float(book["home_win"]), float(book["draw"]), float(book["away_win"])
                )
                return {"Home": ph, "Draw": pd, "Away": pa}.get(side)
            except (TypeError, ValueError):
                pass
        return _as_float(probs.get({"Home": "home", "Draw": "draw", "Away": "away"}.get(side, "")))

    if market_key == "goals_2_5":
        p = _side_from_over(_pct("over_2_5_pct"))
        if p is not None:
            return p
        if book.get("over_2_5") and book.get("under_2_5"):
            try:
                po, pu = _devig2(float(book["over_2_5"]), float(book["under_2_5"]))
                return po if side == "Over" else pu if side == "Under" else None
            except (TypeError, ValueError):
                pass
        over = _as_float(probs.get("over_2_5"))
        return _side_from_over(over)

    if market_key == "goals_3_5":
        p = _side_from_over(_pct("over_3_5_pct"))
        if p is not None:
            return p
        if book.get("over_3_5") and book.get("under_3_5"):
            try:
                po, pu = _devig2(float(book["over_3_5"]), float(book["under_3_5"]))
                return po if side == "Over" else pu if side == "Under" else None
            except (TypeError, ValueError):
                pass
        return None

    if market_key == "btts":
        yes = _pct("btts_pct")
        if yes is not None:
            return yes if side == "Yes" else (1.0 - yes if side == "No" else None)
        if book.get("btts_yes") and book.get("btts_no"):
            try:
                py, pn = _devig2(float(book["btts_yes"]), float(book["btts_no"]))
                return py if side == "Yes" else pn if side == "No" else None
            except (TypeError, ValueError):
                pass
        yes_m = _as_float(probs.get("btts_yes"))
        if yes_m is not None:
            return yes_m if side == "Yes" else (1.0 - yes_m if side == "No" else None)
        return None

    if market_key == "team_goals_home_1_5":
        return _side_from_over(_pct("home_o1_5_pct"))
    if market_key == "team_goals_away_1_5":
        return _side_from_over(_pct("away_o1_5_pct"))
    if market_key == "fh_1x2":
        mapping = {
            "Home": _pct("fh_home_win_pct"),
            "Draw": _pct("fh_draw_pct"),
            "Away": _pct("fh_away_win_pct"),
        }
        return mapping.get(side)
    if market_key == "fh_over_0_5":
        if book.get("fh_over_0_5") and book.get("fh_under_0_5"):
            try:
                po, pu = _devig2(float(book["fh_over_0_5"]), float(book["fh_under_0_5"]))
                return po if side == "Over" else pu if side == "Under" else None
            except (TypeError, ValueError):
                pass
        return None
    if market_key in ("corners_9_5", "shots_25_5", "sot_8_5"):
        over_pct = _prop_sim_over_pct(pred, market_key)
        return _side_from_over(_pct_to_unit(over_pct) if over_pct is not None else None)
    if market_key == "cards_3_5":
        return None
    return None


def materialize_ai_candidate(
    pred: Dict[str, Any], market_key: str, lean: str
) -> Optional[Dict[str, Any]]:
    """Build a publishable candidate from AI-chosen market+lean and DG/book signals."""
    if not is_valid_ai_lean(market_key, lean):
        return None
    side = str(lean).strip()
    mkey = str(market_key).strip()
    dg_lean, book_lean = dg_book_sides_for_market(pred, mkey)
    prob = reference_prob_for_lean(pred, mkey, side)
    if prob is None:
        # Calibration still works with default rate; keep a neutral stand-in.
        prob = 0.55
    hint = agreement_hint(side, dg_lean, book_lean)
    if mkey == "match_1x2":
        label = "Match winner"
        lean_p = lean_plain(side)
    else:
        label = market_chip_label(mkey, None, line=None)
        lean_p = market_lean_plain(side, mkey)
    return {
        "fixture_id": pred.get("fixture_id"),
        "home_name": pred.get("home_name"),
        "away_name": pred.get("away_name"),
        "home_logo": pred.get("home_logo"),
        "away_logo": pred.get("away_logo"),
        "is_neutral": bool(pred.get("is_neutral")),
        "league": pred.get("league"),
        "league_id": pred.get("league_id"),
        "league_country": pred.get("league_country"),
        "league_display": pred.get("league_display"),
        "date_utc": pred.get("date_utc"),
        "kickoff_display": pred.get("kickoff_display") or "",
        "strength_label": pred.get("strength_label"),
        "style_label": pred.get("style_label"),
        "completed": bool(pred.get("completed")),
        "ft_score": pred.get("ft_score"),
        "ft_home": pred.get("ft_home"),
        "ft_away": pred.get("ft_away"),
        "ftr": pred.get("ftr"),
        "awaiting_score": bool(pred.get("awaiting_score")),
        "market_key": mkey,
        "market_label": label,
        "lean": side,
        "lean_plain": lean_p,
        "confidence": "medium",
        "confidence_blurb": confidence_blurb("medium"),
        "prob": float(prob),
        "prob_raw": float(prob),
        "prob_plain": probability_plain(prob),
        "score": None,
        "drivers": [],
        "why": [],
        "agreement_key": hint["key"],
        "agreement_label": hint["label"],
        "agreement_sources": hint.get("sources", []),
        "agreement_n_sources": hint.get("n_sources", 0),
        "dg_lean": dg_lean,
        "book_lean": book_lean,
    }


def build_ai_signal_fixture_groups(
    predictions: Sequence[Dict[str, Any]],
    *,
    require_signals: bool = True,
) -> List[Dict[str, Any]]:
    """One group per fixture with a signal pack (no pre-computed lean candidates)."""
    groups: List[Dict[str, Any]] = []
    for pred in predictions:
        if require_signals and not fixture_has_ai_signals(pred):
            continue
        pack = build_fixture_signal_pack(pred)
        groups.append(
            {
                "fixture_id": pred.get("fixture_id"),
                "home_name": pred.get("home_name"),
                "away_name": pred.get("away_name"),
                "league": pred.get("league"),
                "league_display": pred.get("league_display"),
                "date_utc": pred.get("date_utc"),
                "kickoff_display": pred.get("kickoff_display") or "",
                "prediction": pred,
                "signal_pack": pack,
                "candidates": [],  # lean chosen by LLM, not pre-listed
            }
        )
    return groups
