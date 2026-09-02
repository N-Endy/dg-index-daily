"""Counterfactual Strongest selection audit — measure selection regret."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from dg.report.best_leans import (
    collect_gate_passing_candidates,
    grade_candidate_result,
    select_strongest_lean,
)
from dg.report.loaders import enrich_prediction_for_display
from dg.report.results_attach import attach_result_to_prediction, load_result_index


def selection_regret_audit(conn, *, days: int = 30) -> Dict[str, Any]:
    """
    Compare selected Strongest picks vs all gate-passing candidates on completed fixtures.

    Returns selected hit rate, oracle hit rate (any qualifying market hit), and regret rate.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """
        SELECT p.*, f.date_utc, f.league, f.league_id, f.league_country,
               f.home_name, f.away_name,
               f.home_id, f.away_id, f.home_logo, f.away_logo, f.is_neutral
        FROM prediction p
        JOIN fixture f ON f.fixture_id = p.fixture_id
        WHERE p.id IN (SELECT MAX(id) FROM prediction GROUP BY fixture_id)
          AND f.date_utc >= ?
        ORDER BY f.date_utc DESC
        """,
        (cutoff,),
    ).fetchall()

    result_index = load_result_index(conn)
    selected_graded = 0
    selected_hits = 0
    oracle_graded = 0
    oracle_hits = 0
    regret_cases = 0
    by_market: Dict[str, Dict[str, int]] = {}

    for r in rows:
        d = dict(r)
        try:
            d["drivers"] = json.loads(d.get("drivers_json") or "[]")
            d["markets"] = json.loads(d.get("markets_json") or "{}")
            d["probs"] = json.loads(d.get("probs_json") or "{}")
        except json.JSONDecodeError:
            continue
        attach_result_to_prediction(d, result_index)
        if not d.get("completed"):
            continue

        enriched = enrich_prediction_for_display(d)
        gate_cands = collect_gate_passing_candidates(enriched)
        if not gate_cands:
            continue

        graded_gate: List[Dict[str, Any]] = []
        any_hit = False
        for cand in gate_cands:
            rk, _ = grade_candidate_result(enriched, cand)
            if rk not in ("hit", "miss"):
                continue
            graded_gate.append({**cand, "lean_result_key": rk})
            if rk == "hit":
                any_hit = True

        if not graded_gate:
            continue

        oracle_graded += 1
        if any_hit:
            oracle_hits += 1

        pick = select_strongest_lean(enriched)
        if not pick:
            continue
        sel_rk = pick.get("lean_result_key")
        if sel_rk not in ("hit", "miss"):
            continue

        selected_graded += 1
        if sel_rk == "hit":
            selected_hits += 1
        elif any_hit:
            regret_cases += 1

        mk = str(pick.get("market_key") or "unknown")
        bucket = by_market.setdefault(mk, {"hits": 0, "graded": 0, "regret": 0})
        bucket["graded"] += 1
        if sel_rk == "hit":
            bucket["hits"] += 1
        elif any_hit:
            bucket["regret"] += 1

    selected_rate = (selected_hits / selected_graded) if selected_graded else None
    oracle_rate = (oracle_hits / oracle_graded) if oracle_graded else None
    regret_rate = (regret_cases / selected_graded) if selected_graded else None

    by_market_out: Dict[str, Any] = {}
    for mk, stats in by_market.items():
        g = stats["graded"]
        by_market_out[mk] = {
            "hits": stats["hits"],
            "n_graded": g,
            "hit_rate": stats["hits"] / g if g else None,
            "regret": stats["regret"],
            "regret_rate": stats["regret"] / g if g else None,
        }

    return {
        "window_days": days,
        "n_selected_graded": selected_graded,
        "n_selected_hits": selected_hits,
        "selected_hit_rate": selected_rate,
        "n_oracle_graded": oracle_graded,
        "n_oracle_hits": oracle_hits,
        "oracle_hit_rate": oracle_rate,
        "n_regret": regret_cases,
        "regret_rate": regret_rate,
        "by_market": by_market_out,
        "coverage_note": (
            "Oracle = at least one gate-passing market hit on the fixture. "
            "Regret = selected Strongest missed while another qualifying market hit."
        ),
    }
