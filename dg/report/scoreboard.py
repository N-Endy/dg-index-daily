"""Recent Strongest lean performance from stored predictions."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from dg.report.best_leans import select_strongest_lean
from dg.report.loaders import enrich_prediction_for_display
from dg.report.results_attach import attach_result_to_prediction, load_result_index


def recent_strongest_performance(conn, *, days: int = 30) -> Dict[str, Any]:
    """Replay stored predictions through select_strongest_lean and grade them."""
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
    n_graded = 0
    n_hits = 0
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
        pick = select_strongest_lean(enriched)
        if not pick:
            continue
        rk = pick.get("lean_result_key")
        if rk not in ("hit", "miss"):
            continue
        n_graded += 1
        if rk == "hit":
            n_hits += 1
        mk = str(pick.get("market_key") or "unknown")
        bucket = by_market.setdefault(mk, {"hits": 0, "graded": 0})
        bucket["graded"] += 1
        if rk == "hit":
            bucket["hits"] += 1

    hit_rate = (n_hits / n_graded) if n_graded else None
    by_market_out: Dict[str, Any] = {}
    for mk, stats in by_market.items():
        g = stats["graded"]
        by_market_out[mk] = {
            "hits": stats["hits"],
            "n_graded": g,
            "hit_rate": stats["hits"] / g if g else None,
        }

    return {
        "window_days": days,
        "n_graded": n_graded,
        "n_hits": n_hits,
        "hit_rate": hit_rate,
        "by_market": by_market_out,
        "coverage_note": (
            "Grading uses football-data.co.uk stats where available; "
            "many leagues only have full-time scores."
        ),
    }
