"""Conservative same-day strongest-lean selection over published markets + 1X2."""
from __future__ import annotations

import json
from functools import cmp_to_key
from typing import Any, Dict, List, Optional, Tuple

from dg import config
from dg.model.markets import MARKET_ORDER
from dg.report.loaders import (
    _fixture_sort_key,
    enrich_prediction_for_display,
    get_connection,
    load_dashboard_context,
    today_wat,
)
from dg.web.plain_language import (
    agreement_hint,
    confidence_blurb,
    driver_plain,
    lean_plain,
    market_chip_label,
    market_lean_plain,
    probability_plain,
)

MIN_PROB = config.STRONGEST_MIN_PROB
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

# Markets that count toward the day-level Over-family diversification cap.
OVER_FAMILY_MARKETS = frozenset(
    {
        "goals_2_5",
        "goals_3_5",
        "btts",
        "fh_over_0_5",
        "team_goals_home_1_5",
        "team_goals_away_1_5",
    }
)
OVER_FAMILY_LEANS = frozenset({"Over", "Yes"})

_AGREE_RANK = {"aligned": 2, "partial": 1, "unknown": 0, "split": -1}

_market_hit_rates_cache: Optional[Dict[str, float]] = None
_market_aucs_cache: Optional[Dict[str, Dict[str, Any]]] = None


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


def _agreement_tier(lean: Optional[str], dg_lean: Any, book_lean: Any) -> Tuple[str, str, list, int]:
    hint = agreement_hint(lean, dg_lean if dg_lean else None, book_lean if book_lean else None)
    return hint["key"], hint["label"], hint.get("sources", []), hint.get("n_sources", 0)


def _passes_hard_gates(
    *,
    lean: Optional[str],
    confidence: Optional[str],
    prob: Optional[float],
    dg_lean: Any,
    book_lean: Any,
    market_key: Optional[str] = None,
    scoring_env: Optional[Dict[str, Any]] = None,
) -> bool:
    if not lean:
        return False
    if (confidence or "").lower() != REQUIRED_CONF:
        return False
    from dg.report.scoring_env import over_market_prob_bump

    floor = float(config.strongest_min_prob(market_key)) + over_market_prob_bump(
        market_key, lean, scoring_env
    )
    if prob is None or prob < floor:
        return False
    if not _agreement_gate(lean, dg_lean, book_lean):
        return False
    return True


def _auc_ok(market_key: str, market_aucs: Optional[Dict[str, Any]]) -> bool:
    """Exclude only markets shown, with confidence, to lack discrimination."""
    if not market_aucs:
        return True
    rec = market_aucs.get(market_key)
    if not rec:
        return True
    # Legacy bare-float callers: treat as powered point estimate.
    if isinstance(rec, (int, float)):
        return float(rec) >= float(config.STRONGEST_MIN_AUC)
    if not isinstance(rec, dict):
        return True
    try:
        n_labels = int(rec.get("n_labels") or 0)
        n_weeks = int(rec.get("n_weeks") or 0)
        auc = float(rec["auc"])
    except (TypeError, ValueError, KeyError):
        return True
    if n_labels < int(config.STRONGEST_AUC_MIN_LABELS):
        return True
    if n_weeks < int(config.STRONGEST_AUC_MIN_WEEKS):
        return True
    se_raw = rec.get("auc_se")
    try:
        se = float(se_raw) if se_raw is not None else None
    except (TypeError, ValueError):
        se = None
    upper = auc + 1.64 * se if se is not None else auc
    return upper >= float(config.STRONGEST_MIN_AUC)


def _rank_tuple(
    *,
    agreement_key: str,
    n_sources: int,
    market_key: str,
    prob: float,
    score: Optional[float],
) -> Tuple[int, int, int, float, float]:
    agree = _AGREE_RANK.get(agreement_key, 0)
    poisson = 1 if market_key in POISSON_BACKED else 0
    abs_score = abs(score) if score is not None else 0.0
    return (agree, n_sources, poisson, prob, abs_score)


def _market_hit_rate(market_key: str, rates: Optional[Dict[str, float]]) -> float:
    if not rates:
        return 0.0
    return float(rates.get(market_key, 0.0))


def _better_candidate(
    a: Dict[str, Any],
    b: Dict[str, Any],
    *,
    market_hit_rates: Optional[Dict[str, float]] = None,
) -> bool:
    """Return True if candidate a should rank above b."""
    ra = a["_rank"]
    rb = b["_rank"]
    if ra[0] != rb[0]:
        return ra[0] > rb[0]
    if ra[1] != rb[1]:
        return ra[1] > rb[1]
    # Prefer historically stronger markets before raw stated probability.
    if market_hit_rates:
        mka = str(a.get("market_key") or "")
        mkb = str(b.get("market_key") or "")
        hra = _market_hit_rate(mka, market_hit_rates)
        hrb = _market_hit_rate(mkb, market_hit_rates)
        if abs(hra - hrb) >= 0.01:
            return hra > hrb
    pa, pb = ra[3], rb[3]
    epsilon = config.STRONGEST_POISSON_PROB_EPSILON
    if abs(pa - pb) >= epsilon:
        return pa > pb
    if ra[2] != rb[2]:
        return ra[2] > rb[2]
    if ra[3] != rb[3]:
        return ra[3] > rb[3]
    return ra[4] > rb[4]


def _sort_candidates(
    candidates: List[Dict[str, Any]],
    *,
    market_hit_rates: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    def _cmp(a: Dict[str, Any], b: Dict[str, Any]) -> int:
        if _better_candidate(a, b, market_hit_rates=market_hit_rates):
            return -1
        if _better_candidate(b, a, market_hit_rates=market_hit_rates):
            return 1
        return 0

    return sorted(candidates, key=cmp_to_key(_cmp))


def _candidate_from_market(
    key: str,
    m: Dict[str, Any],
    *,
    market_aucs: Optional[Dict[str, Any]] = None,
    scoring_env: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
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
        market_key=key,
        scoring_env=scoring_env,
    ):
        return None
    if not _auc_ok(key, market_aucs):
        return None
    assert lean is not None and prob is not None
    agree_key, agree_label, agree_sources, agree_n = _agreement_tier(lean, dg_lean, book_lean)
    score = _as_float(m.get("score"))
    drivers = list(m.get("drivers") or [])
    prob_raw = _as_float(m.get("prob_raw"))
    if prob_raw is None:
        prob_raw = prob
    return {
        "market_key": key,
        "market_label": market_chip_label(key, m.get("label"), line=m.get("line")),
        "lean": lean,
        "lean_plain": market_lean_plain(lean, key),
        "confidence": (conf or "").lower(),
        "confidence_blurb": confidence_blurb(conf),
        "prob": prob,
        "prob_raw": prob_raw,
        "prob_plain": probability_plain(prob),
        "score": score,
        "drivers": drivers,
        "why": [driver_plain(d) for d in drivers],
        "agreement_key": agree_key,
        "agreement_label": agree_label,
        "agreement_sources": agree_sources,
        "agreement_n_sources": agree_n,
        "dg_lean": dg_lean,
        "book_lean": book_lean,
        "_rank": _rank_tuple(
            agreement_key=agree_key,
            n_sources=agree_n,
            market_key=key,
            prob=prob,
            score=score,
        ),
    }


def _candidate_1x2(
    pred: Dict[str, Any],
    probs: Dict[str, Any],
    *,
    market_aucs: Optional[Dict[str, Any]] = None,
    scoring_env: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
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
        market_key="match_1x2",
        scoring_env=scoring_env,
    ):
        return None
    if not _auc_ok("match_1x2", market_aucs):
        return None
    assert lean is not None and prob is not None
    agree_key, agree_label, agree_sources, agree_n = _agreement_tier(lean, dg_lean, book_lean)
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
        "prob_raw": prob,
        "prob_plain": probability_plain(prob),
        "score": score,
        "drivers": drivers,
        "why": list(why),
        "agreement_key": agree_key,
        "agreement_label": agree_label,
        "agreement_sources": agree_sources,
        "agreement_n_sources": agree_n,
        "dg_lean": dg_lean,
        "book_lean": book_lean,
        "_rank": _rank_tuple(
            agreement_key=agree_key,
            n_sources=agree_n,
            market_key="match_1x2",
            prob=prob,
            score=score,
        ),
    }


def collect_gate_passing_candidates(
    pred: Dict[str, Any],
    *,
    market_aucs: Optional[Dict[str, Any]] = None,
    scoring_env: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """All markets + 1X2 that clear the Strongest hard gates for a fixture."""
    markets = _markets_dict(pred)
    probs = _probs_dict(pred)
    candidates: List[Dict[str, Any]] = []
    one_x2 = _candidate_1x2(
        pred, probs, market_aucs=market_aucs, scoring_env=scoring_env
    )
    if one_x2:
        candidates.append(one_x2)
    for key in MARKET_ORDER:
        m = markets.get(key)
        if not isinstance(m, dict):
            continue
        cand = _candidate_from_market(
            key, m, market_aucs=market_aucs, scoring_env=scoring_env
        )
        if cand:
            candidates.append(cand)
    return candidates


def _market_result_labels(pred: Dict[str, Any]) -> Dict[str, Any]:
    from dg.model.evaluate import _market_labels
    from dg.model.markets import extract_market_lines

    if not pred.get("result_row"):
        return {}
    return _market_labels(pred["result_row"], extract_market_lines(_markets_dict(pred)))


def grade_candidate_result(pred: Dict[str, Any], candidate: Dict[str, Any]) -> Tuple[str, str]:
    """Return (lean_result_key, lean_result_label) for a gate-passing candidate."""
    from dg.report.results_attach import lean_result, market_lean_result

    if not pred.get("completed"):
        return "pending", ""
    mkey = str(candidate.get("market_key") or "")
    lean = candidate.get("lean")
    if mkey == "match_1x2":
        rk, rl = lean_result(lean, pred.get("ftr"))
    else:
        labels = _market_result_labels(pred)
        rk, rl = market_lean_result(lean, labels.get(mkey))
    if rk == "pending":
        return rk, ""
    return rk, "Lean hit" if rk == "hit" else "Lean miss"


def _attach_fixture_fields(pred: Dict[str, Any], best: Dict[str, Any]) -> Dict[str, Any]:
    out = {
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
        **{k: v for k, v in best.items() if k != "_rank"},
        "_rank": best["_rank"],
    }
    rk, rl = grade_candidate_result(pred, best)
    out["lean_result_key"] = rk
    out["lean_result_label"] = rl
    return out


def get_market_hit_rates(conn) -> Dict[str, float]:
    """Load cached backtest hit rates per market for ranking tie-breaks."""
    global _market_hit_rates_cache
    if _market_hit_rates_cache is not None:
        return _market_hit_rates_cache
    from dg.report.market_reliability import market_hit_rates_from_backtest

    _market_hit_rates_cache = market_hit_rates_from_backtest(conn)
    return _market_hit_rates_cache


def clear_market_hit_rates_cache() -> None:
    global _market_hit_rates_cache, _market_aucs_cache
    _market_hit_rates_cache = None
    _market_aucs_cache = None


def get_market_aucs(conn) -> Dict[str, Dict[str, Any]]:
    """Load cached per-market AUC records for Strongest discrimination gating."""
    global _market_aucs_cache
    if _market_aucs_cache is not None:
        return _market_aucs_cache
    from dg.model.supervised import load_market_aucs

    _market_aucs_cache = load_market_aucs(conn)
    return _market_aucs_cache


def select_top_n_candidates(
    pred: Dict[str, Any],
    n: int = 3,
    *,
    market_hit_rates: Optional[Dict[str, float]] = None,
    market_aucs: Optional[Dict[str, Any]] = None,
    scoring_env: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Return up to n gate-passing candidates for a fixture, best first."""
    raw = collect_gate_passing_candidates(
        pred, market_aucs=market_aucs, scoring_env=scoring_env
    )
    if not raw:
        return []
    ranked = _sort_candidates(raw, market_hit_rates=market_hit_rates)
    top = ranked[: max(1, int(n))]
    return [_attach_fixture_fields(pred, c) for c in top]


def select_strongest_lean(
    pred: Dict[str, Any],
    *,
    market_hit_rates: Optional[Dict[str, float]] = None,
    market_aucs: Optional[Dict[str, Any]] = None,
    scoring_env: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Pick at most one conservative lean for a fixture from all markets + 1X2.
    Returns None when nothing clears the high bar.
    """
    picks = select_top_n_candidates(
        pred,
        1,
        market_hit_rates=market_hit_rates,
        market_aucs=market_aucs,
        scoring_env=scoring_env,
    )
    if not picks:
        return None
    out = picks[0]
    return out


def diversify_day_picks(picks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Greedily keep ranked picks while capping same-market and Over-family share.
    Always keeps at least one pick when the input is non-empty.
    """
    if not picks or not config.STRONGEST_DIVERSIFY_ENABLED:
        return picks
    n = len(picks)
    if n <= 1:
        return picks
    max_same = max(1, int(n * float(config.STRONGEST_MAX_SAME_MARKET_SHARE)))
    max_over = max(1, int(n * float(config.STRONGEST_MAX_OVER_FAMILY_SHARE)))
    kept: List[Dict[str, Any]] = []
    by_market: Dict[str, int] = {}
    over_n = 0
    for p in picks:
        mk = str(p.get("market_key") or "")
        lean = str(p.get("lean") or "").strip()
        is_over = mk in OVER_FAMILY_MARKETS and lean in OVER_FAMILY_LEANS
        if by_market.get(mk, 0) >= max_same:
            continue
        if is_over and over_n >= max_over:
            continue
        kept.append(p)
        by_market[mk] = by_market.get(mk, 0) + 1
        if is_over:
            over_n += 1
    return kept if kept else picks[:1]


def build_strongest_picks(
    predictions: List[Dict[str, Any]],
    *,
    market_hit_rates: Optional[Dict[str, float]] = None,
    market_aucs: Optional[Dict[str, Any]] = None,
    scoring_env: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Select and rank strongest leans across fixtures (strongest first)."""
    picks: List[Dict[str, Any]] = []
    for pred in predictions:
        pick = select_strongest_lean(
            pred,
            market_hit_rates=market_hit_rates,
            market_aucs=market_aucs,
            scoring_env=scoring_env,
        )
        if pick:
            picks.append(pick)
    picks.sort(key=lambda p: p.get("_rank", (0, 0, 0, 0.0, 0.0)), reverse=True)
    for p in picks:
        p.pop("_rank", None)
    return diversify_day_picks(picks)


def build_ai_vet_fixture_groups(
    predictions: List[Dict[str, Any]],
    *,
    top_n: Optional[int] = None,
    market_hit_rates: Optional[Dict[str, float]] = None,
    market_aucs: Optional[Dict[str, Any]] = None,
    scoring_env: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Group top-N gate-passing candidates per fixture for LLM market selection.
    Returns list of {fixture_id, home_name, away_name, ..., candidates: [...]}.
    """
    n = int(top_n if top_n is not None else config.AI_VET_TOP_N)
    groups: List[Dict[str, Any]] = []
    for pred in predictions:
        cands = select_top_n_candidates(
            pred,
            n,
            market_hit_rates=market_hit_rates,
            market_aucs=market_aucs,
            scoring_env=scoring_env,
        )
        if not cands:
            continue
        for c in cands:
            c.pop("_rank", None)
        groups.append(
            {
                "fixture_id": pred.get("fixture_id"),
                "home_name": pred.get("home_name"),
                "away_name": pred.get("away_name"),
                "league": pred.get("league"),
                "league_display": pred.get("league_display"),
                "date_utc": pred.get("date_utc"),
                "kickoff_display": pred.get("kickoff_display") or "",
                "candidates": cands,
            }
        )
    return groups

def flatten_vet_groups(groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten fixture groups into a candidate list for gate_screen_scores."""
    out: List[Dict[str, Any]] = []
    for g in groups:
        for c in g.get("candidates") or []:
            out.append(c)
    return out


def load_strongest_day(*, date: Optional[str] = None) -> Dict[str, Any]:
    """Load today's (or given WAT date) dashboard rows and attach strongest picks."""
    from dg.report.scoreboard import recent_strongest_performance
    from dg.report.scoring_env import load_scoring_environment
    from dg.report.selection_audit import selection_regret_audit

    day = date or today_wat()
    ctx = load_dashboard_context(date_filter=day)
    conn = get_connection()
    try:
        scoreboard = recent_strongest_performance(conn)
        selection_audit = selection_regret_audit(conn)
        scoring_env = load_scoring_environment(conn)
        market_hit_rates = None
        market_aucs = get_market_aucs(conn)
        if config.STRONGEST_USE_MARKET_HIT_RATES:
            market_hit_rates = get_market_hit_rates(conn)
    finally:
        conn.close()
    enriched = [enrich_prediction_for_display(p) for p in ctx["predictions"]]
    picks = build_strongest_picks(
        enriched,
        market_hit_rates=market_hit_rates,
        market_aucs=market_aucs,
        scoring_env=scoring_env,
    )
    picks.sort(key=_fixture_sort_key)
    return {
        **ctx,
        "day": day,
        "predictions": enriched,
        "picks": picks,
        "n_fixtures": len(enriched),
        "n_picks": len(picks),
        "scoreboard": scoreboard,
        "selection_audit": selection_audit,
        "scoring_env": scoring_env,
    }
