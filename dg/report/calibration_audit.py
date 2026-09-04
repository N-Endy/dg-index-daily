"""Audit whether Est.% scores rank historical candidates by actual hit rate."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from dg.ai.vet_strongest import compute_publish_score
from dg.report.best_leans import (
    collect_gate_passing_candidates,
    grade_candidate_result,
)
from dg.report.loaders import enrich_prediction_for_display
from dg.report.market_reliability import (
    agreement_tier_from_candidate,
    load_market_calibration,
    reliability_for,
)
from dg.report.results_attach import attach_result_to_prediction, load_result_index


def _spearman(xs: List[float], ys: List[float]) -> Optional[float]:
    """Spearman rank correlation; returns None if undefined."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return None

    def _ranks(vals: List[float]) -> List[float]:
        ordered = sorted(range(n), key=lambda i: vals[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[ordered[j + 1]] == vals[ordered[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                ranks[ordered[k]] = avg
            i = j + 1
        return ranks

    rx = _ranks(xs)
    ry = _ranks(ys)
    mean_x = sum(rx) / n
    mean_y = sum(ry) / n
    num = sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry))
    den_x = sum((a - mean_x) ** 2 for a in rx) ** 0.5
    den_y = sum((b - mean_y) ** 2 for b in ry) ** 0.5
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def calibration_ranking_audit(conn, *, days: int = 90) -> Dict[str, Any]:
    """
    Replay gate-passing candidates with results through reliability_for + multiplier,
    then report predicted vs actual by score decile, Spearman, and inversions.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """
        SELECT p.*, f.date_utc, f.league, f.league_id, f.league_country,
               f.home_name, f.away_name, f.home_id, f.away_id,
               f.home_logo, f.away_logo, f.is_neutral
        FROM prediction p
        JOIN fixture f ON f.fixture_id = p.fixture_id
        WHERE p.id IN (SELECT MAX(id) FROM prediction GROUP BY fixture_id)
          AND f.date_utc >= ?
        """,
        (cutoff,),
    ).fetchall()

    calib = load_market_calibration(conn)
    result_index = load_result_index(conn)
    scored: List[Tuple[int, int]] = []  # (est_score, hit)

    from dg.report.best_leans import get_market_aucs
    from dg.report.scoring_env import load_scoring_environment

    scoring_env = load_scoring_environment(conn)
    market_aucs = get_market_aucs(conn)

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
        for cand in collect_gate_passing_candidates(
            enriched, market_aucs=market_aucs, scoring_env=scoring_env
        ):
            rk, _ = grade_candidate_result(enriched, cand)
            if rk not in ("hit", "miss"):
                continue
            tier = agreement_tier_from_candidate(cand)
            reli = reliability_for(
                calib,
                cand.get("market_key"),
                tier,
                cand.get("prob_raw") if cand.get("prob_raw") is not None else cand.get("prob"),
            )
            # Neutral coherence (mid) for historical replay — no LLM screen available.
            est = compute_publish_score(
                base_rate=float(reli["rate"]),
                coherence=2,
                concerns=[],
            )
            scored.append((est, 1 if rk == "hit" else 0))

    n = len(scored)
    if n == 0:
        return {
            "window_days": days,
            "n_graded": 0,
            "message": "No graded gate-passing candidates in window",
            "deciles": [],
            "spearman": None,
            "n_inversions": 0,
        }

    scores = [s for s, _ in scored]
    outcomes = [h for _, h in scored]
    spearman = _spearman([float(s) for s in scores], [float(h) for h in outcomes])

    # Deciles by score rank
    ordered = sorted(scored, key=lambda t: t[0])
    decile_size = max(1, n // 10)
    deciles: List[Dict[str, Any]] = []
    for i in range(10):
        start = i * decile_size
        if i == 9:
            chunk = ordered[start:]
        else:
            chunk = ordered[start : start + decile_size]
        if not chunk:
            continue
        hits = sum(h for _, h in chunk)
        mean_pred = sum(s for s, _ in chunk) / len(chunk)
        actual = hits / len(chunk)
        deciles.append(
            {
                "decile": i + 1,
                "n": len(chunk),
                "score_min": chunk[0][0],
                "score_max": chunk[-1][0],
                "mean_predicted": mean_pred,
                "actual_hit_rate": actual,
                "hits": hits,
            }
        )

    # Inversions: lower mean_predicted decile outhits a higher one
    n_inversions = 0
    for i in range(len(deciles)):
        for j in range(i + 1, len(deciles)):
            if deciles[i]["mean_predicted"] >= deciles[j]["mean_predicted"]:
                continue
            if deciles[i]["actual_hit_rate"] > deciles[j]["actual_hit_rate"] + 1e-9:
                n_inversions += 1

    overall_hits = sum(outcomes)
    return {
        "window_days": days,
        "n_graded": n,
        "overall_hit_rate": overall_hits / n,
        "mean_predicted": sum(scores) / n,
        "spearman": spearman,
        "n_inversions": n_inversions,
        "deciles": deciles,
        "coverage_note": (
            "Scores use measured base rate × neutral coherence=2 (no LLM replay). "
            "Inversions count pairs of deciles where a lower predicted bucket outhits a higher one."
        ),
    }
