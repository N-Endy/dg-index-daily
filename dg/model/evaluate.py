"""Backtest harness: rule_v1 vs de-vigged book vs DataGaffer sim; per-market scores."""
from __future__ import annotations

import json
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from dg.model.markets import DEFAULT_MARKET_LINES, extract_market_lines, markets_model_tag

logger = logging.getLogger(__name__)

_CONF_STRENGTH = {"low": 0.40, "medium": 0.50, "high": 0.60}
_CONF_BINARY = {"low": 0.55, "medium": 0.65, "high": 0.75}


def _devig(h: float, d: float, a: float) -> Tuple[float, float, float]:
    ih, id_, ia = 1.0 / h, 1.0 / d, 1.0 / a
    s = ih + id_ + ia
    return ih / s, id_ / s, ia / s


def _devig2(a: float, b: float) -> Tuple[float, float]:
    ia, ib = 1.0 / a, 1.0 / b
    s = ia + ib
    return ia / s, ib / s


def _brier(probs: Tuple[float, float, float], outcome: str) -> float:
    y = {"H": (1.0, 0.0, 0.0), "D": (0.0, 1.0, 0.0), "A": (0.0, 0.0, 1.0)}.get(outcome)
    if y is None:
        return float("nan")
    return sum((p - t) ** 2 for p, t in zip(probs, y)) / 3.0


def _brier_binary(p_pos: float, hit: bool) -> float:
    y = 1.0 if hit else 0.0
    return (p_pos - y) ** 2


def _logloss(probs: Tuple[float, float, float], outcome: str) -> float:
    idx = {"H": 0, "D": 1, "A": 2}.get(outcome)
    if idx is None:
        return float("nan")
    p = max(min(probs[idx], 1.0 - 1e-12), 1e-12)
    return -math.log(p)


def lean_to_probs(lean: str, confidence: str) -> Tuple[float, float, float]:
    """Map directional lean + band to a soft probability triple (for scoring)."""
    strength = _CONF_STRENGTH.get(confidence, 0.45)
    rem = (1.0 - strength) / 2.0
    if lean == "Home":
        return strength, rem, rem
    if lean == "Away":
        return rem, rem, strength
    return rem, strength, rem


def _binary_p(lean: str, confidence: str, pos: str) -> float:
    strength = _CONF_BINARY.get(confidence, 0.60)
    return strength if lean == pos else (1.0 - strength)


def _market_pos_prob(m: Dict[str, Any], pos: str) -> float:
    """Prefer stored market probability; fall back to confidence buckets."""
    lean = str(m.get("lean") or "")
    raw = m.get("prob")
    if raw is not None:
        try:
            p_lean = float(raw)
            return p_lean if lean == pos else (1.0 - p_lean)
        except (TypeError, ValueError):
            pass
    return _binary_p(lean, str(m.get("confidence") or "low"), pos)


def _probs_from_json(probs_json: Optional[str], lean: str, confidence: str) -> Tuple[float, float, float]:
    """Use stored 1X2 probabilities when present; else lean buckets."""
    if probs_json:
        try:
            p = json.loads(probs_json)
            if p.get("home") is not None and p.get("draw") is not None and p.get("away") is not None:
                return float(p["home"]), float(p["draw"]), float(p["away"])
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return lean_to_probs(lean, confidence)


def _market_labels(mr: Any, lines: Optional[Dict[str, float]] = None) -> Dict[str, Optional[str]]:
    """Derive outcome labels from match_result columns when present."""
    line_map = lines or {}
    labels: Dict[str, Optional[str]] = {}

    fthg, ftag = mr["fthg"] if mr["fthg"] is not None else None, mr["ftag"] if mr["ftag"] is not None else None
    if fthg is not None and ftag is not None:
        total = int(fthg) + int(ftag)
        labels["goals_2_5"] = "Over" if total > line_map.get("goals_2_5", DEFAULT_MARKET_LINES["goals_2_5"]) else "Under"
        labels["goals_3_5"] = "Over" if total > line_map.get("goals_3_5", DEFAULT_MARKET_LINES["goals_3_5"]) else "Under"
        labels["btts"] = "Yes" if int(fthg) > 0 and int(ftag) > 0 else "No"
        home_line = line_map.get("team_goals_home_1_5", DEFAULT_MARKET_LINES["team_goals_home_1_5"])
        away_line = line_map.get("team_goals_away_1_5", DEFAULT_MARKET_LINES["team_goals_away_1_5"])
        labels["team_goals_home_1_5"] = "Over" if int(fthg) > home_line else "Under"
        labels["team_goals_away_1_5"] = "Over" if int(ftag) > away_line else "Under"
    else:
        labels["goals_2_5"] = None
        labels["goals_3_5"] = None
        labels["btts"] = None
        labels["team_goals_home_1_5"] = None
        labels["team_goals_away_1_5"] = None

    hthg, htag = mr["hthg"], mr["htag"]
    if hthg is not None and htag is not None:
        ht = int(hthg) + int(htag)
        if int(hthg) > int(htag):
            labels["fh_1x2"] = "Home"
        elif int(htag) > int(hthg):
            labels["fh_1x2"] = "Away"
        else:
            labels["fh_1x2"] = "Draw"
        fh_line = line_map.get("fh_over_0_5", DEFAULT_MARKET_LINES["fh_over_0_5"])
        labels["fh_over_0_5"] = "Over" if ht > fh_line else "Under"
    else:
        labels["fh_1x2"] = None
        labels["fh_over_0_5"] = None

    hc, ac = mr["hc"], mr["ac"]
    if hc is not None and ac is not None:
        corners_line = line_map.get("corners_9_5", DEFAULT_MARKET_LINES["corners_9_5"])
        labels["corners_9_5"] = "Over" if int(hc) + int(ac) > corners_line else "Under"
    else:
        labels["corners_9_5"] = None

    hs, a_sh = mr["hs"], mr["as_shots"]
    if hs is not None and a_sh is not None:
        shots_line = line_map.get("shots_25_5", DEFAULT_MARKET_LINES["shots_25_5"])
        labels["shots_25_5"] = "Over" if int(hs) + int(a_sh) > shots_line else "Under"
    else:
        labels["shots_25_5"] = None

    hst, ast = mr["hst"], mr["ast"]
    if hst is not None and ast is not None:
        sot_line = line_map.get("sot_8_5", DEFAULT_MARKET_LINES["sot_8_5"])
        labels["sot_8_5"] = "Over" if int(hst) + int(ast) > sot_line else "Under"
    else:
        labels["sot_8_5"] = None

    hy, ay, hr, ar = mr["hy"], mr["ay"], mr["hr"], mr["ar"]
    if None not in (hy, ay):
        cards = int(hy) + int(ay) + int(hr or 0) + int(ar or 0)
        cards_line = line_map.get("cards_3_5", DEFAULT_MARKET_LINES["cards_3_5"])
        labels["cards_3_5"] = "Over" if cards > cards_line else "Under"
    else:
        labels["cards_3_5"] = None

    return labels


def _record_1x2_calibration(
    calibration: Optional[Dict[Tuple[str, str, str], List[int]]],
    lean: Optional[str],
    ftr: Optional[str],
    probs_json: Optional[str],
    *,
    home_win_pct: Optional[float] = None,
    draw_pct: Optional[float] = None,
    away_win_pct: Optional[float] = None,
    book_odds: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a match_1x2 calibration cell from prediction lean vs result."""
    if calibration is None:
        return
    from dg.features.matchup import book_lean as _book_lean
    from dg.features.matchup import sim_lean as _sim_lean
    from dg.report.market_reliability import agreement_tier_from_market, prob_band_for

    if not lean or lean not in ("Home", "Draw", "Away"):
        return
    outcome = (ftr or "").upper()
    if outcome not in ("H", "D", "A"):
        return
    hit = {"Home": "H", "Draw": "D", "Away": "A"}[lean] == outcome
    probs: Dict[str, Any] = {}
    if probs_json:
        try:
            probs = json.loads(probs_json) or {}
        except (json.JSONDecodeError, TypeError):
            probs = {}
    p = probs.get({"Home": "home", "Draw": "draw", "Away": "away"}[lean])
    try:
        p = float(p) if p is not None else None
    except (TypeError, ValueError):
        p = None
    band = prob_band_for(p) if p is not None else "no_prob"
    if not band:
        band = "no_prob"
    tier = agreement_tier_from_market(
        {
            "lean": lean,
            "dg_lean": _sim_lean(home_win_pct, draw_pct, away_win_pct),
            "book_lean": _book_lean(book_odds) if book_odds else None,
        }
    )
    bucket = calibration.setdefault(("match_1x2", tier, band), [0, 0])
    bucket[1] += 1
    if hit:
        bucket[0] += 1


def _score_market_row(
    market_metrics: Dict[str, Dict[str, Any]],
    *,
    markets: Dict[str, Any],
    labels: Dict[str, Optional[str]],
    book_odds: Optional[Dict[str, Any]],
    calibration: Optional[Dict[Tuple[str, str, str], List[int]]] = None,
) -> None:
    """Accumulate Brier and hit-rate for each market that has a label and a stored lean."""
    from dg.report.market_reliability import agreement_tier_from_market, prob_band_for

    book_odds = book_odds or {}

    def _record_calibration(market_key: str, m: Dict[str, Any], hit: bool) -> None:
        if calibration is None:
            return
        raw = m.get("prob")
        try:
            p = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            p = None
        band_src = m.get("prob_raw")
        try:
            p_band = float(band_src) if band_src is not None else p
        except (TypeError, ValueError):
            p_band = p
        band = prob_band_for(p_band) if p_band is not None else "no_prob"
        if not band:
            band = "no_prob"
        tier = agreement_tier_from_market(m)
        bucket = calibration.setdefault((str(market_key), tier, band), [0, 0])
        bucket[1] += 1
        if hit:
            bucket[0] += 1

    binary_pos = {
        "goals_2_5": "Over",
        "goals_3_5": "Over",
        "btts": "Yes",
        "team_goals_home_1_5": "Over",
        "team_goals_away_1_5": "Over",
        "fh_over_0_5": "Over",
        "corners_9_5": "Over",
        "shots_25_5": "Over",
        "sot_8_5": "Over",
        "cards_3_5": "Over",
    }

    for key, pos in binary_pos.items():
        label = labels.get(key)
        m = markets.get(key)
        if not label or not isinstance(m, dict) or not m.get("lean"):
            continue
        bucket = market_metrics.setdefault(
            key, {"rule": [], "book": [], "dg_sim": [], "rule_hits": []}
        )
        p = _market_pos_prob(m, pos)
        bucket["rule"].append(_brier_binary(p, label == pos))
        hit = 1 if label == m.get("lean") else 0
        bucket["rule_hits"].append(hit)
        _record_calibration(key, m, bool(hit))

        if key == "goals_2_5" and book_odds.get("over_2_5") and book_odds.get("under_2_5"):
            try:
                po, _pu = _devig2(float(book_odds["over_2_5"]), float(book_odds["under_2_5"]))
                bucket["book"].append(_brier_binary(po, label == "Over"))
            except (TypeError, ValueError):
                pass
        elif key == "goals_3_5" and book_odds.get("over_3_5") and book_odds.get("under_3_5"):
            try:
                po, _pu = _devig2(float(book_odds["over_3_5"]), float(book_odds["under_3_5"]))
                bucket["book"].append(_brier_binary(po, label == "Over"))
            except (TypeError, ValueError):
                pass
        elif key == "btts" and book_odds.get("btts_yes") and book_odds.get("btts_no"):
            try:
                py, _pn = _devig2(float(book_odds["btts_yes"]), float(book_odds["btts_no"]))
                bucket["book"].append(_brier_binary(py, label == "Yes"))
            except (TypeError, ValueError):
                pass

        dg = m.get("dg_lean")
        if dg in (pos, "Under", "No", "Over", "Yes"):
            p_dg = 0.62 if dg == pos else 0.38
            bucket["dg_sim"].append(_brier_binary(p_dg, label == pos))

    fh_label = labels.get("fh_1x2")
    fh_m = markets.get("fh_1x2")
    if fh_label and isinstance(fh_m, dict) and fh_m.get("lean"):
        bucket = market_metrics.setdefault(
            "fh_1x2", {"rule": [], "book": [], "dg_sim": [], "rule_hits": []}
        )
        outcome = {"Home": "H", "Draw": "D", "Away": "A"}[fh_label]
        if fh_m.get("prob") is not None and fh_m.get("lean"):
            try:
                p_win = float(fh_m["prob"])
                rem = max(0.02, (1.0 - p_win) / 2.0)
                lean = str(fh_m["lean"])
                if lean == "Home":
                    rule_p = (p_win, rem, rem)
                elif lean == "Away":
                    rule_p = (rem, rem, p_win)
                else:
                    rule_p = (rem, p_win, rem)
                s = sum(rule_p)
                rule_p = (rule_p[0] / s, rule_p[1] / s, rule_p[2] / s)
            except (TypeError, ValueError):
                rule_p = lean_to_probs(str(fh_m["lean"]), str(fh_m.get("confidence") or "low"))
        else:
            rule_p = lean_to_probs(str(fh_m["lean"]), str(fh_m.get("confidence") or "low"))
        bucket["rule"].append(_brier(rule_p, outcome))
        hit = 1 if fh_label == fh_m.get("lean") else 0
        bucket["rule_hits"].append(hit)
        _record_calibration("fh_1x2", fh_m, bool(hit))
        if book_odds.get("fh_home_win") and book_odds.get("fh_draw") and book_odds.get("fh_away_win"):
            try:
                book_p = _devig(
                    float(book_odds["fh_home_win"]),
                    float(book_odds["fh_draw"]),
                    float(book_odds["fh_away_win"]),
                )
                bucket["book"].append(_brier(book_p, outcome))
            except (TypeError, ValueError):
                pass
        dg = fh_m.get("dg_lean")
        if dg in ("Home", "Draw", "Away"):
            bucket["dg_sim"].append(_brier(lean_to_probs(dg, "medium"), outcome))


def _accumulate_row(
    metrics: Dict[str, Dict[str, List[float]]],
    *,
    outcome: str,
    lean: str,
    confidence: str,
    closing_home: Optional[float],
    closing_draw: Optional[float],
    closing_away: Optional[float],
    book_odds_json: Optional[str],
    home_win_pct: Optional[float],
    draw_pct: Optional[float],
    away_win_pct: Optional[float],
    probs_json: Optional[str] = None,
) -> bool:
    if outcome not in ("H", "D", "A"):
        return False
    rule_p = _probs_from_json(probs_json, lean, confidence)
    metrics["rule"]["brier"].append(_brier(rule_p, outcome))
    metrics["rule"]["logloss"].append(_logloss(rule_p, outcome))
    lean_map = {"H": "Home", "D": "Draw", "A": "Away"}
    metrics["rule"].setdefault("hits", []).append(1 if lean == lean_map.get(outcome) else 0)

    if closing_home and closing_draw and closing_away:
        book_p = _devig(float(closing_home), float(closing_draw), float(closing_away))
        metrics["book"]["brier"].append(_brier(book_p, outcome))
        metrics["book"]["logloss"].append(_logloss(book_p, outcome))
    elif book_odds_json:
        try:
            bo = json.loads(book_odds_json)
            if bo.get("home_win") and bo.get("draw") and bo.get("away_win"):
                book_p = _devig(float(bo["home_win"]), float(bo["draw"]), float(bo["away_win"]))
                metrics["book"]["brier"].append(_brier(book_p, outcome))
                metrics["book"]["logloss"].append(_logloss(book_p, outcome))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    if home_win_pct is not None and draw_pct is not None and away_win_pct is not None:
        sim_p = (
            float(home_win_pct) / 100.0,
            float(draw_pct) / 100.0,
            float(away_win_pct) / 100.0,
        )
        s = sum(sim_p)
        if s > 0:
            sim_p = (sim_p[0] / s, sim_p[1] / s, sim_p[2] / s)
        metrics["dg_sim"]["brier"].append(_brier(sim_p, outcome))
        metrics["dg_sim"]["logloss"].append(_logloss(sim_p, outcome))
    return True


def evaluate_joined(conn) -> Dict[str, Any]:
    """
    Score stored predictions that have matched results, and (when that set is
    empty) retrospectively score resolved historical match_result rows using
    the latest DG snapshot + rule_v1. Also scores markets_json where labels exist.
    """
    from dg.report.results_attach import build_result_index, fixture_day

    result_index = build_result_index(
        conn.execute(
            """
            SELECT home_team_id, away_team_id, date, ftr, fthg, ftag, hthg, htag,
                   hs, as_shots, hst, ast, hc, ac, hy, ay, hr, ar,
                   closing_home, closing_draw, closing_away
            FROM match_result
            WHERE ftr IS NOT NULL
              AND home_team_id IS NOT NULL AND away_team_id IS NOT NULL
            """
        ).fetchall()
    )

    tag = markets_model_tag()
    rows = conn.execute(
        """
        SELECT
            p.lean, p.confidence, p.score, p.model_version, p.markets_json, p.probs_json,
            f.home_id, f.away_id, f.date_utc, f.home_name, f.away_name,
            fp.home_win_pct, fp.draw_pct, fp.away_win_pct, fp.book_odds_json
        FROM prediction p
        JOIN fixture f ON f.fixture_id = p.fixture_id
        LEFT JOIN fixture_projection fp ON fp.id = (
            SELECT id FROM fixture_projection
            WHERE fixture_id = f.fixture_id
            ORDER BY observed_at DESC LIMIT 1
        )
        WHERE p.id IN (SELECT MAX(id) FROM prediction GROUP BY fixture_id)
          AND p.model_version LIKE ?
        """,
        (f"%+{tag}",),
    ).fetchall()

    metrics: Dict[str, Dict[str, List[float]]] = {
        "rule": {"brier": [], "logloss": []},
        "book": {"brier": [], "logloss": []},
        "dg_sim": {"brier": [], "logloss": []},
    }
    market_metrics: Dict[str, Dict[str, List[float]]] = {}
    calibration_raw: Dict[Tuple[str, str, str], List[int]] = {}
    n = 0
    mode = "joined_predictions"

    for r in rows:
        day = fixture_day(r["date_utc"])
        try:
            hid = int(r["home_id"]) if r["home_id"] is not None else None
            aid = int(r["away_id"]) if r["away_id"] is not None else None
        except (TypeError, ValueError):
            hid = aid = None
        if hid is None or aid is None or not day:
            continue
        mr = result_index.get((hid, aid, day))
        if mr is None:
            continue

        outcome = (mr["ftr"] or "").upper()
        if _accumulate_row(
            metrics,
            outcome=outcome,
            lean=r["lean"],
            confidence=r["confidence"],
            closing_home=mr["closing_home"] if "closing_home" in mr.keys() else None,
            closing_draw=mr["closing_draw"] if "closing_draw" in mr.keys() else None,
            closing_away=mr["closing_away"] if "closing_away" in mr.keys() else None,
            book_odds_json=r["book_odds_json"],
            home_win_pct=r["home_win_pct"],
            draw_pct=r["draw_pct"],
            away_win_pct=r["away_win_pct"],
            probs_json=r["probs_json"] if "probs_json" in r.keys() else None,
        ):
            n += 1

        book_odds: Dict[str, Any] = {}
        if r["book_odds_json"]:
            try:
                book_odds = json.loads(r["book_odds_json"]) or {}
            except (json.JSONDecodeError, TypeError):
                book_odds = {}
        _record_1x2_calibration(
            calibration_raw,
            r["lean"],
            mr["ftr"],
            r["probs_json"] if "probs_json" in r.keys() else None,
            home_win_pct=r["home_win_pct"],
            draw_pct=r["draw_pct"],
            away_win_pct=r["away_win_pct"],
            book_odds=book_odds,
        )
        markets: Dict[str, Any] = {}
        if r["markets_json"]:
            try:
                markets = json.loads(r["markets_json"]) or {}
            except (json.JSONDecodeError, TypeError):
                markets = {}
        if markets:
            _score_market_row(
                market_metrics,
                markets=markets,
                labels=_market_labels(mr, extract_market_lines(markets)),
                book_odds=book_odds,
                calibration=calibration_raw,
            )

    if n == 0:
        from dg.features.matchup import build_matchup
        from dg.features.team import build_team_features
        from dg.model.goals import league_avg_ortg, predict_goals
        from dg.model.markets import predict_markets
        from dg.model.registry import load_config
        from dg.model.rules import (
            _blend_1x2,
            _character,
            _confidence,
            _lean_from_probs,
            _score_matchup,
        )
        from dg.storage.db import latest_snapshot

        snap = latest_snapshot(conn)
        if snap is None:
            return {"n": 0, "message": "No snapshot and no joined predictions"}
        snapshot_id = int(snap["id"])
        _, cfg = load_config()
        hist = conn.execute(
            """
            SELECT * FROM match_result
            WHERE ftr IS NOT NULL
              AND home_team_id IS NOT NULL AND away_team_id IS NOT NULL
            """
        ).fetchall()
        mode = "retrospective_rule_v2"
        for mr in hist:
            home = build_team_features(conn, int(mr["home_team_id"]), snapshot_id)
            away = build_team_features(conn, int(mr["away_team_id"]), snapshot_id)
            matchup = build_matchup(home, away)
            if not matchup.get("ok"):
                continue
            score, _weighted, _drivers = _score_matchup(matchup, cfg)
            league_avg = league_avg_ortg(conn, snapshot_id, matchup.get("league_id"))
            goal_probs = predict_goals(matchup, league_avg=league_avg)
            blended = _blend_1x2(goal_probs, score, cfg)
            lean = _lean_from_probs(blended)
            confidence = _confidence(score, matchup, cfg, probs=blended)
            _ = _character(matchup, cfg)
            probs_json = json.dumps(
                {
                    "home": blended["home"],
                    "draw": blended["draw"],
                    "away": blended["away"],
                }
            )
            if _accumulate_row(
                metrics,
                outcome=(mr["ftr"] or "").upper(),
                lean=lean,
                confidence=confidence,
                closing_home=mr["closing_home"],
                closing_draw=mr["closing_draw"],
                closing_away=mr["closing_away"],
                book_odds_json=None,
                home_win_pct=None,
                draw_pct=None,
                away_win_pct=None,
                probs_json=probs_json,
            ):
                n += 1
            _record_1x2_calibration(
                calibration_raw,
                lean,
                mr["ftr"],
                probs_json,
            )
            markets = predict_markets(matchup, goal_probs=goal_probs)
            _score_market_row(
                market_metrics,
                markets=markets,
                labels=_market_labels(mr, extract_market_lines(markets)),
                book_odds={},
                calibration=calibration_raw,
            )

    if n == 0:
        return {
            "n": 0,
            "message": "No evaluable rows yet (need joined predictions or resolved results)",
        }

    def _avg(xs: List[float]) -> Optional[float]:
        xs = [x for x in xs if x == x]
        return sum(xs) / len(xs) if xs else None

    def _hit_rate(hits: List[int]) -> Optional[float]:
        return sum(hits) / len(hits) if hits else None

    summary: Dict[str, Any] = {"n": n, "mode": mode, "models": {}, "markets": {}}
    for name, m in metrics.items():
        hits = m.get("hits", [])
        summary["models"][name] = {
            "brier": _avg(m["brier"]),
            "logloss": _avg(m["logloss"]),
            "n": len(m["brier"]),
            "hits": sum(hits) if hits else 0,
            "n_graded": len(hits),
            "hit_rate": _hit_rate(hits),
        }
    for mkey, buckets in market_metrics.items():
        entry: Dict[str, Any] = {}
        for src, scores in buckets.items():
            if src == "rule_hits":
                if scores:
                    entry.setdefault("rule", {})["hits"] = sum(scores)
                    entry["rule"]["n_graded"] = len(scores)
                    entry["rule"]["hit_rate"] = _hit_rate(scores)
                continue
            if scores and src in ("rule", "book", "dg_sim"):
                entry[src] = {"brier": _avg(scores), "n": len(scores)}
        if entry:
            summary["markets"][mkey] = entry

    from dg.report.market_reliability import finalize_calibration_rows

    summary["calibration"] = finalize_calibration_rows(calibration_raw)

    logger.info(
        "Backtest n=%d mode=%s models=%s markets=%s calibration_rows=%d",
        n,
        mode,
        summary["models"],
        {k: {s: v.get("n") for s, v in mv.items()} for k, mv in summary["markets"].items()},
        len(summary["calibration"]),
    )
    return summary
