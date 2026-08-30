"""Conservative same-day strongest-lean selection over published markets + 1X2."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from dg.model.markets import MARKET_ORDER
from dg.report.loaders import enrich_prediction_for_display, load_dashboard_context
from dg.web.plain_language import (
    agreement_hint,
    confidence_blurb,
    driver_plain,
    lean_plain,
    market_chip_label,
    market_lean_plain,
    probability_plain,
)

MIN_PROB = 0.65
REQUIRED_CONF = "high"

# Markets whose lean probability comes primarily from the Poisson goal model
POISSON_BACKED = frozenset(
    {
        "match_1x2",
        "goals_2_5",
        "goals_3_5",
        "btts",
        "team_goals_home_1_5",
        "team_goals_away_1_5",
        "fh_1x2",
        "fh_over_0_5",
    }
)

_AGREE_RANK = {"aligned": 2, "partial": 1, "unknown": 0, "split": -1}


def today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _markets_dict(pred: Dict[str, Any]) -> Dict[str, Any]:
    markets = pred.get("markets")
    if markets is None and pred.get("markets_json"):
        try:
            markets = json.loads(pred["markets_json"])
        except (json.JSONDecodeError, TypeError):
            markets = {}
    return markets if isinstance(markets, dict) else {}


def _probs_dict(pred: Dict[str, Any]) -> Dict[str, Any]:
    probs = pred.get("probs")
    if probs is None and pred.get("probs_json"):
        try:
            probs = json.loads(pred["probs_json"])
        except (json.JSONDecodeError, TypeError):
            probs = {}
    return probs if isinstance(probs, dict) else {}


def _lean_prob_1x2(pred: Dict[str, Any], probs: Dict[str, Any]) -> Optional[float]:
    if pred.get("lean_prob") is not None:
        return _as_float(pred.get("lean_prob"))
    lean = pred.get("lean")
    if lean == "Home":
        return _as_float(probs.get("home"))
    if lean == "Away":
        return _as_float(probs.get("away"))
    if lean == "Draw":
        return _as_float(probs.get("draw"))
    return None


def _agreement_gate(lean: Optional[str], dg_lean: Any, book_lean: Any) -> bool:
    """
    When DG and/or book signals exist, every present signal must match the lean.
    No signals → pass (conf/prob still apply). Split → fail.
    """
    ours = (lean or "").strip()
    if not ours:
        return False
    checks: List[bool] = []
    if dg_lean:
        checks.append(str(dg_lean).strip() == ours)
    if book_lean:
        checks.append(str(book_lean).strip() == ours)
    if not checks:
        return True
    return all(checks)


def _agreement_tier(lean: Optional[str], dg_lean: Any, book_lean: Any) -> Tuple[str, str]:
    hint = agreement_hint(lean, dg_lean if dg_lean else None, book_lean if book_lean else None)
    return hint["key"], hint["label"]


def _passes_hard_gates(
    *,
    lean: Optional[str],
    confidence: Optional[str],
    prob: Optional[float],
    dg_lean: Any,
    book_lean: Any,
) -> bool:
    if not lean:
        return False
    if (confidence or "").lower() != REQUIRED_CONF:
        return False
    if prob is None or prob < MIN_PROB:
        return False
    if not _agreement_gate(lean, dg_lean, book_lean):
        return False
    return True


def _rank_tuple(
    *,
    agreement_key: str,
    market_key: str,
    prob: float,
    score: Optional[float],
) -> Tuple[int, int, float, float]:
    agree = _AGREE_RANK.get(agreement_key, 0)
    poisson = 1 if market_key in POISSON_BACKED else 0
    abs_score = abs(score) if score is not None else 0.0
    return (agree, poisson, prob, abs_score)


def _candidate_from_market(key: str, m: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    lean = m.get("lean")
    conf = m.get("confidence")
    prob = _as_float(m.get("prob"))
    dg_lean = m.get("dg_lean")
    book_lean = m.get("book_lean")
    if not _passes_hard_gates(
        lean=lean,
        confidence=conf,
        prob=prob,
        dg_lean=dg_lean,
        book_lean=book_lean,
    ):
        return None
    assert lean is not None and prob is not None
    agree_key, agree_label = _agreement_tier(lean, dg_lean, book_lean)
    score = _as_float(m.get("score"))
    drivers = list(m.get("drivers") or [])
    return {
        "market_key": key,
        "market_label": market_chip_label(key, m.get("label")),
        "lean": lean,
        "lean_plain": market_lean_plain(lean, key),
        "confidence": (conf or "").lower(),
        "confidence_blurb": confidence_blurb(conf),
        "prob": prob,
        "prob_plain": probability_plain(prob),
        "score": score,
        "drivers": drivers,
        "why": [driver_plain(d) for d in drivers],
        "agreement_key": agree_key,
        "agreement_label": agree_label,
        "dg_lean": dg_lean,
        "book_lean": book_lean,
        "_rank": _rank_tuple(
            agreement_key=agree_key,
            market_key=key,
            prob=prob,
            score=score,
        ),
    }


def _candidate_1x2(pred: Dict[str, Any], probs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    lean = pred.get("lean")
    conf = pred.get("confidence")
    prob = _lean_prob_1x2(pred, probs)
    dg_lean = pred.get("dg_sim_lean")
    book_lean = pred.get("book_lean")
    if not _passes_hard_gates(
        lean=lean,
        confidence=conf,
        prob=prob,
        dg_lean=dg_lean,
        book_lean=book_lean,
    ):
        return None
    assert lean is not None and prob is not None
    agree_key, agree_label = _agreement_tier(lean, dg_lean, book_lean)
    score = _as_float(pred.get("score"))
    drivers = list(pred.get("drivers") or [])
    why = pred.get("why")
    if not why:
        why = [driver_plain(d) for d in drivers]
    return {
        "market_key": "match_1x2",
        "market_label": "Match winner",
        "lean": lean,
        "lean_plain": lean_plain(lean),
        "confidence": (conf or "").lower(),
        "confidence_blurb": confidence_blurb(conf),
        "prob": prob,
        "prob_plain": probability_plain(prob),
        "score": score,
        "drivers": drivers,
        "why": list(why),
        "agreement_key": agree_key,
        "agreement_label": agree_label,
        "dg_lean": dg_lean,
        "book_lean": book_lean,
        "_rank": _rank_tuple(
            agreement_key=agree_key,
            market_key="match_1x2",
            prob=prob,
            score=score,
        ),
    }


def select_strongest_lean(pred: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Pick at most one conservative lean for a fixture from all markets + 1X2.
    Returns None when nothing clears the high bar.
    """
    markets = _markets_dict(pred)
    probs = _probs_dict(pred)

    candidates: List[Dict[str, Any]] = []
    one_x2 = _candidate_1x2(pred, probs)
    if one_x2:
        candidates.append(one_x2)

    for key in MARKET_ORDER:
        m = markets.get(key)
        if not isinstance(m, dict):
            continue
        cand = _candidate_from_market(key, m)
        if cand:
            candidates.append(cand)

    if not candidates:
        return None

    best = max(candidates, key=lambda c: c["_rank"])
    out = {
        "fixture_id": pred.get("fixture_id"),
        "home_name": pred.get("home_name"),
        "away_name": pred.get("away_name"),
        "league": pred.get("league"),
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
        **{k: v for k, v in best.items() if k != "_rank"},
        "_rank": best["_rank"],
    }
    # Featured lean hit/miss when the fixture is completed
    if out["completed"]:
        from dg.report.results_attach import lean_result, market_lean_result
        from dg.model.evaluate import _market_labels

        if best["market_key"] == "match_1x2":
            rk, rl = lean_result(best.get("lean"), pred.get("ftr"))
        else:
            labels = {}
            if pred.get("result_row"):
                labels = _market_labels(pred["result_row"])
            rk, rl = market_lean_result(best.get("lean"), labels.get(best["market_key"]))
        out["lean_result_key"] = rk
        out["lean_result_label"] = rl if rk == "pending" else (
            "Lean hit" if rk == "hit" else "Lean miss"
        )
    else:
        out["lean_result_key"] = "pending"
        out["lean_result_label"] = ""
    return out


def build_strongest_picks(predictions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Select and rank strongest leans across fixtures (strongest first)."""
    picks: List[Dict[str, Any]] = []
    for pred in predictions:
        pick = select_strongest_lean(pred)
        if pick:
            picks.append(pick)
    picks.sort(key=lambda p: p.get("_rank", (0, 0, 0.0, 0.0)), reverse=True)
    for p in picks:
        p.pop("_rank", None)
    return picks


def load_strongest_day(*, date: Optional[str] = None) -> Dict[str, Any]:
    """Load today's (or given UTC date) dashboard rows and attach strongest picks."""
    day = date or today_utc()
    ctx = load_dashboard_context(date_filter=day)
    enriched = [enrich_prediction_for_display(p) for p in ctx["predictions"]]
    picks = build_strongest_picks(enriched)
    return {
        **ctx,
        "day": day,
        "predictions": enriched,
        "picks": picks,
        "n_fixtures": len(enriched),
        "n_picks": len(picks),
    }
