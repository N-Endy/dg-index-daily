"""Build rich match scripts and graded similar-case summaries for Luna vet."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def load_fixture_sim(conn, fixture_id: int) -> Dict[str, Any]:
    row = conn.execute(
        """
        SELECT sim_stats_json, book_odds_json, projected_meta_json,
               xgot_total, sot_total, value_score, value_over_2_5,
               congestion_home, congestion_away, regression_home, regression_away
        FROM fixture_projection
        WHERE fixture_id = ?
        ORDER BY observed_at DESC LIMIT 1
        """,
        (fixture_id,),
    ).fetchone()
    if not row:
        return {}
    sim: Dict[str, Any] = {}
    if row["sim_stats_json"]:
        try:
            sim = json.loads(row["sim_stats_json"]) or {}
        except (json.JSONDecodeError, TypeError):
            sim = {}
    meta: Dict[str, Any] = {}
    if row["projected_meta_json"]:
        try:
            meta = json.loads(row["projected_meta_json"]) or {}
        except (json.JSONDecodeError, TypeError):
            meta = {}
    from dg.model.sim_prior import extract_sim_fields

    fields = extract_sim_fields(sim if isinstance(sim, dict) else {})
    return {
        "sim": sim if isinstance(sim, dict) else {},
        "meta": meta if isinstance(meta, dict) else {},
        "fields": fields,
        "xgot_total": row["xgot_total"] if "xgot_total" in row.keys() else fields.get("xgot_total"),
        "sot_total": row["sot_total"] if "sot_total" in row.keys() else fields.get("sot_total"),
        "value_score": row["value_score"] if "value_score" in row.keys() else fields.get("value_score"),
        "value_over_2_5": (
            row["value_over_2_5"] if "value_over_2_5" in row.keys() else fields.get("value_over_2_5")
        ),
        "congestion_home": bool(row["congestion_home"]) if "congestion_home" in row.keys() else False,
        "congestion_away": bool(row["congestion_away"]) if "congestion_away" in row.keys() else False,
        "regression_home": (
            row["regression_home"] if "regression_home" in row.keys() else fields.get("regression_home")
        ),
        "regression_away": (
            row["regression_away"] if "regression_away" in row.keys() else fields.get("regression_away")
        ),
    }


def match_script_from_sim(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Compact structured brief for Luna — numbers only, no invented injuries."""
    fields = ctx.get("fields") or {}
    meta = ctx.get("meta") or {}
    top = meta.get("top_scores") or fields.get("top_scores") or []
    top3 = []
    for row in top[:3]:
        if isinstance(row, dict):
            top3.append(
                {
                    "score": row.get("score"),
                    "pct": row.get("probability_pct"),
                }
            )
    return {
        "topScores": top3,
        "correctScoreModel": fields.get("correct_score_model") or meta.get("correct_score_model"),
        "xgotTotal": fields.get("xgot_total") if fields.get("xgot_total") is not None else ctx.get("xgot_total"),
        "sotTotal": fields.get("sot_total") if fields.get("sot_total") is not None else ctx.get("sot_total"),
        "shotAccuracy": fields.get("shot_accuracy_total"),
        "sotConversion": fields.get("sot_conversion_total"),
        "valueScore": ctx.get("value_score") if ctx.get("value_score") is not None else fields.get("value_score"),
        "valueOver25": ctx.get("value_over_2_5"),
        "congestion": {
            "home": bool(ctx.get("congestion_home") or fields.get("congestion_home")),
            "away": bool(ctx.get("congestion_away") or fields.get("congestion_away")),
        },
        "regression": {
            "home": ctx.get("regression_home") if ctx.get("regression_home") is not None else fields.get("regression_home"),
            "away": ctx.get("regression_away") if ctx.get("regression_away") is not None else fields.get("regression_away"),
        },
        "fhXgTotal": fields.get("fh_xg_total"),
        "fhSotTotal": fields.get("fh_sot_total"),
        "scoreFirstHomePct": fields.get("score_first_home_pct"),
        "over25Pct": fields.get("over_2_5_pct"),
        "bttsPct": fields.get("btts_pct"),
        "sotOver85Pct": fields.get("sot_over_8_5_pct"),
    }


def similar_case_summary(
    conn,
    *,
    market_key: str,
    lean: str,
    agreement_key: Optional[str] = None,
    league_id: Optional[int] = None,
    limit: int = 12,
) -> Dict[str, Any]:
    """
    Empirical record for similar Strongest/AI setups.
    Uses market_calibration when available.
    """
    from dg.report.market_reliability import (
        load_market_calibration,
        reliability_for,
    )

    calib = load_market_calibration(conn)
    # Map Strongest agreement_key → calibration tier
    key = str(agreement_key or "").lower()
    if key in ("aligned", "agree2"):
        tier_key = "agree2"
    elif key in ("partial", "agree1"):
        tier_key = "agree1"
    elif key == "split":
        tier_key = "split"
    else:
        tier_key = "none"
    reli = reliability_for(calib, market_key, tier_key, None)
    n = int(reli.get("n") or 0)
    rate = float(reli.get("rate") or 0.5)

    league_note = None
    if league_id is not None and n >= 5:
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM prediction p
                JOIN fixture f ON f.fixture_id = p.fixture_id
                WHERE f.league_id = ?
                  AND p.markets_json LIKE ?
                """,
                (int(league_id), f'%"{market_key}"%'),
            ).fetchone()
            if row and int(row["n"] or 0) > 0:
                league_note = f"{int(row['n'])} recent predictions in this league for {market_key}"
        except Exception:  # noqa: BLE001
            league_note = None

    sample_n = min(n, limit) if n else 0
    if sample_n:
        sample_hits = int(round(rate * sample_n))
        record = f"~{sample_hits}-{sample_n - sample_hits} on similar {sample_n} (tier={tier_key})"
    else:
        record = "insufficient graded history"

    return {
        "marketKey": market_key,
        "lean": lean,
        "agreementTier": tier_key,
        "n": n,
        "hitRate": round(rate, 3) if n else None,
        "record": record,
        "leagueNote": league_note,
        "source": reli.get("source"),
    }
