"""
Time-split residual logistic models on DG sim + style features.

Two heads:
  - accuracy: no book features (true skill)
  - value: includes book implied probs (residual vs market)

Models are only enabled when holdout Brier beats the DG-sim / book baseline.
Pure-Python logistic regression (no LightGBM dependency) for portability.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dg import config

logger = logging.getLogger(__name__)

RESIDUAL_MODEL_KEY = "residual_v1"
RESIDUAL_MIN_TRAIN = int(getattr(config, "RESIDUAL_MIN_TRAIN", 200) or 200)
RESIDUAL_MIN_HOLDOUT = int(getattr(config, "RESIDUAL_MIN_HOLDOUT", 80) or 80)
RESIDUAL_HOLDOUT_FRAC = float(getattr(config, "RESIDUAL_HOLDOUT_FRAC", 0.25) or 0.25)
RESIDUAL_APPLY = bool(getattr(config, "RESIDUAL_ENABLED", True))

ACCURACY_FEATURES = (
    "sim_home",
    "sim_draw",
    "sim_away",
    "rating_gap",
    "pace_clash_n",
    "nec_sum_n",
    "efficiency_edge_n",
    "form_trend_n",
    "luck_gap_n",
    "xgot_total_n",
    "sot_total_n",
    "over_2_5_pct_n",
    "btts_pct_n",
    "value_score_n",
    "congestion",
    "regression_avg",
)

VALUE_FEATURES = ACCURACY_FEATURES + (
    "book_home",
    "book_draw",
    "book_away",
)


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


def _num(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _devig(h: float, d: float, a: float) -> Tuple[float, float, float]:
    ih, id_, ia = 1.0 / h, 1.0 / d, 1.0 / a
    s = ih + id_ + ia
    return ih / s, id_ / s, ia / s


def build_feature_vector(
    *,
    matchup: Dict[str, Any],
    sim: Optional[Dict[str, Any]],
    book: Optional[Dict[str, Any]],
    prior_probs: Optional[Dict[str, float]] = None,
    head: str = "accuracy",
) -> Tuple[List[str], List[float]]:
    from dg.model.sim_prior import extract_sim_fields

    fields = extract_sim_fields(sim)
    prior = prior_probs or {}
    ph = _num(prior.get("home"), _num(fields.get("home_win_pct"), 33.3) / 100.0)
    pd = _num(prior.get("draw"), _num(fields.get("draw_pct"), 33.3) / 100.0)
    pa = _num(prior.get("away"), _num(fields.get("away_win_pct"), 33.3) / 100.0)
    # If percents were used as 0-100 without prior
    if ph > 1.5:
        ph, pd, pa = ph / 100.0, pd / 100.0, pa / 100.0
    s = ph + pd + pa
    if s > 0:
        ph, pd, pa = ph / s, pd / s, pa / s

    feats: Dict[str, float] = {
        "sim_home": ph,
        "sim_draw": pd,
        "sim_away": pa,
        "rating_gap": _num(matchup.get("rating_gap")) / 1.5,
        "pace_clash_n": (_num(matchup.get("pace_clash"), 100.0) - 100.0) / 100.0,
        "nec_sum_n": (_num(matchup.get("nec_sum"), 100.0) - 100.0) / 100.0,
        "efficiency_edge_n": _num(matchup.get("efficiency_edge")) / 50.0,
        "form_trend_n": _num(matchup.get("form_trend")) / 5.0,
        "luck_gap_n": _num(matchup.get("luck_gap")) / 2.0,
        "xgot_total_n": (_num(fields.get("xgot_total")) - 2.0) / 2.0,
        "sot_total_n": (_num(fields.get("sot_total")) - 8.5) / 4.0,
        "over_2_5_pct_n": (_num(fields.get("over_2_5_pct"), 50.0) - 50.0) / 50.0,
        "btts_pct_n": (_num(fields.get("btts_pct"), 50.0) - 50.0) / 50.0,
        "value_score_n": _num(fields.get("value_score")) / 20.0,
        "congestion": 1.0 if fields.get("congestion_any") else 0.0,
        "regression_avg": (
            (_num(fields.get("regression_home")) + _num(fields.get("regression_away"))) / 2.0
        ),
    }

    names = list(ACCURACY_FEATURES)
    if head == "value":
        names = list(VALUE_FEATURES)
        book = book or {}
        try:
            if book.get("home_win") and book.get("draw") and book.get("away_win"):
                bh, bd, ba = _devig(
                    float(book["home_win"]),
                    float(book["draw"]),
                    float(book["away_win"]),
                )
            else:
                bh = bd = ba = 1.0 / 3.0
        except (TypeError, ValueError):
            bh = bd = ba = 1.0 / 3.0
        feats["book_home"] = bh
        feats["book_draw"] = bd
        feats["book_away"] = ba

    return names, [feats[n] for n in names]


def _fit_logistic(
    X: List[List[float]],
    y: List[int],
    *,
    lr: float = 0.08,
    max_iter: int = 250,
    l2: float = 0.02,
) -> Tuple[List[float], float]:
    """Fit binary logistic with L2; returns (weights, bias)."""
    n_feat = len(X[0]) if X else 0
    w = [0.0] * n_feat
    b = 0.0
    n = len(X)
    if n < 10 or n_feat == 0:
        return w, b
    for _ in range(max_iter):
        grad_w = [0.0] * n_feat
        grad_b = 0.0
        for xi, yi in zip(X, y):
            z = b + sum(wj * xj for wj, xj in zip(w, xi))
            pred = _sigmoid(z)
            err = pred - yi
            for j in range(n_feat):
                grad_w[j] += err * xi[j]
            grad_b += err
        for j in range(n_feat):
            grad_w[j] = grad_w[j] / n + l2 * w[j]
            w[j] -= lr * grad_w[j]
        b -= lr * (grad_b / n)
    return w, b


def _predict_proba(w: Sequence[float], b: float, x: Sequence[float]) -> float:
    z = b + sum(wj * xj for wj, xj in zip(w, x))
    return _sigmoid(z)


def _brier(probs: List[float], labels: List[int]) -> float:
    if not probs:
        return 1.0
    return sum((p - y) ** 2 for p, y in zip(probs, labels)) / len(probs)


def _one_vs_rest_fit(
    X: List[List[float]],
    outcomes: List[str],
    class_label: str,
) -> Tuple[List[float], float]:
    y = [1 if o == class_label else 0 for o in outcomes]
    return _fit_logistic(X, y)


def collect_training_rows(conn) -> List[Dict[str, Any]]:
    """Joined prediction+result rows with pre-kickoff features only."""
    from dg.report.results_attach import build_result_index, fixture_day

    result_index = build_result_index(
        conn.execute(
            """
            SELECT home_team_id, away_team_id, date, ftr, fthg, ftag
            FROM match_result
            WHERE ftr IS NOT NULL
              AND home_team_id IS NOT NULL AND away_team_id IS NOT NULL
            """
        ).fetchall()
    )
    rows = conn.execute(
        """
        SELECT
            p.probs_json, p.markets_json, p.lean,
            f.home_id, f.away_id, f.date_utc, f.league_id,
            fp.book_odds_json, fp.sim_stats_json,
            fp.home_win_pct, fp.draw_pct, fp.away_win_pct
        FROM prediction p
        JOIN fixture f ON f.fixture_id = p.fixture_id
        LEFT JOIN fixture_projection fp ON fp.id = (
            SELECT id FROM fixture_projection
            WHERE fixture_id = f.fixture_id
            ORDER BY observed_at DESC LIMIT 1
        )
        WHERE p.id IN (SELECT MAX(id) FROM prediction GROUP BY fixture_id)
        ORDER BY f.date_utc
        """
    ).fetchall()

    out: List[Dict[str, Any]] = []
    for r in rows:
        day = fixture_day(r["date_utc"])
        try:
            hid = int(r["home_id"])
            aid = int(r["away_id"])
        except (TypeError, ValueError):
            continue
        if not day:
            continue
        mr = result_index.get((hid, aid, day))
        if mr is None:
            continue
        ftr = (mr["ftr"] or "").upper()
        if ftr not in ("H", "D", "A"):
            continue
        sim: Dict[str, Any] = {}
        if r["sim_stats_json"]:
            try:
                sim = json.loads(r["sim_stats_json"]) or {}
            except (json.JSONDecodeError, TypeError):
                sim = {}
        book: Dict[str, Any] = {}
        if r["book_odds_json"]:
            try:
                book = json.loads(r["book_odds_json"]) or {}
            except (json.JSONDecodeError, TypeError):
                book = {}
        probs: Dict[str, Any] = {}
        if r["probs_json"]:
            try:
                probs = json.loads(r["probs_json"]) or {}
            except (json.JSONDecodeError, TypeError):
                probs = {}
        # Reconstruct a thin matchup from stored probs / defaults
        matchup = {
            "rating_gap": _num(probs.get("rating_gap")),
            "pace_clash": 100.0,
            "nec_sum": 100.0,
            "efficiency_edge": 0.0,
            "form_trend": 0.0,
            "luck_gap": 0.0,
        }
        outcome = {"H": "home", "D": "draw", "A": "away"}[ftr]
        out.append(
            {
                "date_utc": r["date_utc"],
                "outcome": outcome,
                "ftr": ftr,
                "matchup": matchup,
                "sim": sim,
                "book": book,
                "prior": {
                    "home": probs.get("home")
                    or (
                        float(r["home_win_pct"]) / 100.0
                        if r["home_win_pct"] is not None
                        else None
                    ),
                    "draw": probs.get("draw")
                    or (
                        float(r["draw_pct"]) / 100.0 if r["draw_pct"] is not None else None
                    ),
                    "away": probs.get("away")
                    or (
                        float(r["away_win_pct"]) / 100.0
                        if r["away_win_pct"] is not None
                        else None
                    ),
                },
                "league_id": r["league_id"],
            }
        )
    return out


def _time_split(
    rows: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if len(rows) < RESIDUAL_MIN_TRAIN + RESIDUAL_MIN_HOLDOUT:
        return rows, []
    cut = max(RESIDUAL_MIN_HOLDOUT, int(len(rows) * RESIDUAL_HOLDOUT_FRAC))
    cut = min(cut, len(rows) - RESIDUAL_MIN_TRAIN)
    if cut <= 0:
        return rows, []
    return rows[:-cut], rows[-cut:]


def _baseline_brier(rows: List[Dict[str, Any]], head: str) -> float:
    scores: List[float] = []
    for r in rows:
        prior = r["prior"]
        ph = _num(prior.get("home"), 1 / 3)
        pd = _num(prior.get("draw"), 1 / 3)
        pa = _num(prior.get("away"), 1 / 3)
        if head == "value":
            book = r.get("book") or {}
            try:
                if book.get("home_win") and book.get("draw") and book.get("away_win"):
                    ph, pd, pa = _devig(
                        float(book["home_win"]),
                        float(book["draw"]),
                        float(book["away_win"]),
                    )
            except (TypeError, ValueError):
                pass
        s = ph + pd + pa
        if s > 0:
            ph, pd, pa = ph / s, pd / s, pa / s
        y = r["outcome"]
        target = {"home": (1, 0, 0), "draw": (0, 1, 0), "away": (0, 0, 1)}[y]
        scores.append(sum((p - t) ** 2 for p, t in zip((ph, pd, pa), target)) / 3.0)
    return sum(scores) / len(scores) if scores else 1.0


def _model_brier(
    rows: List[Dict[str, Any]],
    *,
    feature_names: List[str],
    models: Dict[str, Tuple[List[float], float]],
    head: str,
) -> float:
    scores: List[float] = []
    for r in rows:
        names, x = build_feature_vector(
            matchup=r["matchup"],
            sim=r["sim"],
            book=r["book"],
            prior_probs=r["prior"],
            head=head,
        )
        # Align to trained feature order
        feat_map = dict(zip(names, x))
        xv = [feat_map.get(n, 0.0) for n in feature_names]
        raw = {
            c: _predict_proba(models[c][0], models[c][1], xv)
            for c in ("home", "draw", "away")
        }
        s = sum(raw.values()) or 1.0
        probs = (raw["home"] / s, raw["draw"] / s, raw["away"] / s)
        y = r["outcome"]
        target = {"home": (1, 0, 0), "draw": (0, 1, 0), "away": (0, 0, 1)}[y]
        scores.append(sum((p - t) ** 2 for p, t in zip(probs, target)) / 3.0)
    return sum(scores) / len(scores) if scores else 1.0


def train_residual_models(conn) -> Dict[str, Any]:
    """
    Fit accuracy + value residual heads. Enable only when holdout beats baseline.
    """
    rows = collect_training_rows(conn)
    summary: Dict[str, Any] = {
        "n_rows": len(rows),
        "heads": {},
        "message": None,
    }
    if len(rows) < RESIDUAL_MIN_TRAIN:
        summary["message"] = (
            f"Residual gated: {len(rows)}/{RESIDUAL_MIN_TRAIN} labelled joins"
        )
        logger.info(summary["message"])
        return summary

    train, holdout = _time_split(rows)
    if len(holdout) < RESIDUAL_MIN_HOLDOUT:
        # Use last 20% anyway if close
        if len(holdout) < 40:
            summary["message"] = (
                f"Residual holdout too small: {len(holdout)}/{RESIDUAL_MIN_HOLDOUT}"
            )
            logger.info(summary["message"])
            return summary

    now = datetime.now(timezone.utc).isoformat()
    for head in ("accuracy", "value"):
        feature_names = list(ACCURACY_FEATURES if head == "accuracy" else VALUE_FEATURES)
        X_train: List[List[float]] = []
        outcomes_train: List[str] = []
        for r in train:
            names, x = build_feature_vector(
                matchup=r["matchup"],
                sim=r["sim"],
                book=r["book"],
                prior_probs=r["prior"],
                head=head,
            )
            feat_map = dict(zip(names, x))
            X_train.append([feat_map.get(n, 0.0) for n in feature_names])
            outcomes_train.append(r["outcome"])

        models: Dict[str, Tuple[List[float], float]] = {}
        for cls in ("home", "draw", "away"):
            models[cls] = _one_vs_rest_fit(X_train, outcomes_train, cls)

        base_brier = _baseline_brier(holdout, head)
        hold_brier = _model_brier(
            holdout, feature_names=feature_names, models=models, head=head
        )
        beat = hold_brier < base_brier - 0.001
        enabled = 1 if beat and RESIDUAL_APPLY else 0

        weights_payload = {
            "classes": {
                c: {"w": models[c][0], "b": models[c][1]} for c in ("home", "draw", "away")
            },
            "feature_names": feature_names,
        }
        conn.execute(
            """
            INSERT INTO residual_model (
                fitted_at, model_key, market_key, head,
                n_train, n_holdout, holdout_brier, baseline_brier,
                beat_baseline, enabled, weights_json, feature_names_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(model_key, market_key, head) DO UPDATE SET
                fitted_at=excluded.fitted_at,
                n_train=excluded.n_train,
                n_holdout=excluded.n_holdout,
                holdout_brier=excluded.holdout_brier,
                baseline_brier=excluded.baseline_brier,
                beat_baseline=excluded.beat_baseline,
                enabled=excluded.enabled,
                weights_json=excluded.weights_json,
                feature_names_json=excluded.feature_names_json
            """,
            (
                now,
                RESIDUAL_MODEL_KEY,
                "match_1x2",
                head,
                len(train),
                len(holdout),
                hold_brier,
                base_brier,
                1 if beat else 0,
                enabled,
                json.dumps(weights_payload),
                json.dumps(feature_names),
            ),
        )
        summary["heads"][head] = {
            "n_train": len(train),
            "n_holdout": len(holdout),
            "holdout_brier": round(hold_brier, 5),
            "baseline_brier": round(base_brier, 5),
            "beat_baseline": beat,
            "enabled": bool(enabled),
        }
        logger.info(
            "Residual %s: holdout=%.4f baseline=%.4f beat=%s enabled=%s",
            head,
            hold_brier,
            base_brier,
            beat,
            bool(enabled),
        )

    conn.commit()
    summary["message"] = "Residual models fitted"
    return summary


def load_enabled_residual(conn, *, head: str = "accuracy") -> Optional[Dict[str, Any]]:
    if not RESIDUAL_APPLY:
        return None
    row = conn.execute(
        """
        SELECT * FROM residual_model
        WHERE model_key = ? AND market_key = 'match_1x2' AND head = ?
          AND enabled = 1
        ORDER BY fitted_at DESC LIMIT 1
        """,
        (RESIDUAL_MODEL_KEY, head),
    ).fetchone()
    if not row:
        return None
    try:
        weights = json.loads(row["weights_json"])
        names = json.loads(row["feature_names_json"])
    except (json.JSONDecodeError, TypeError):
        return None
    return {
        "weights": weights,
        "feature_names": names,
        "head": head,
        "holdout_brier": row["holdout_brier"],
    }


def apply_residual_1x2(
    conn,
    blended: Dict[str, float],
    *,
    matchup: Dict[str, Any],
    sim: Optional[Dict[str, Any]],
    book: Optional[Dict[str, Any]],
    head: str = "accuracy",
    blend_weight: float = 0.35,
) -> Dict[str, float]:
    """
    Soft-blend residual one-vs-rest probs into the sim-prior / style blend.
    No-op when no enabled model.
    """
    model = load_enabled_residual(conn, head=head)
    if not model:
        return blended
    names, x = build_feature_vector(
        matchup=matchup,
        sim=sim,
        book=book,
        prior_probs=blended,
        head=head,
    )
    feat_map = dict(zip(names, x))
    feature_names = model["feature_names"]
    xv = [feat_map.get(n, 0.0) for n in feature_names]
    classes = model["weights"].get("classes") or {}
    raw: Dict[str, float] = {}
    for c in ("home", "draw", "away"):
        entry = classes.get(c) or {}
        w = entry.get("w") or []
        b = float(entry.get("b") or 0.0)
        if len(w) != len(xv):
            return blended
        raw[c] = _predict_proba(w, b, xv)
    s = sum(raw.values()) or 1.0
    residual = {c: raw[c] / s for c in ("home", "draw", "away")}
    w = max(0.0, min(0.6, float(blend_weight)))
    out = {
        c: (1.0 - w) * float(blended.get(c, 1 / 3)) + w * residual[c]
        for c in ("home", "draw", "away")
    }
    s2 = sum(out.values()) or 1.0
    return {c: out[c] / s2 for c in ("home", "draw", "away")}
