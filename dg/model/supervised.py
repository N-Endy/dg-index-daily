"""Supervised calibration — Platt scaling on 1X2 probabilities (gated by config)."""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from dg import config

logger = logging.getLogger(__name__)

_OUTCOMES = ("home", "draw", "away")
_OUTCOME_TO_FTR = {"home": "H", "draw": "D", "away": "A"}


def labelled_count(conn) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM match_result
        WHERE ftr IS NOT NULL AND home_team_id IS NOT NULL AND away_team_id IS NOT NULL
        """
    ).fetchone()
    return int(row["n"]) if row else 0


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


def _fit_platt(probs: List[float], labels: List[int], *, lr: float = 0.05, max_iter: int = 200) -> Tuple[float, float]:
    """Fit Platt scaling: p_cal = sigmoid(A * p + B)."""
    a, b = 1.0, 0.0
    if len(probs) < 10:
        return a, b
    for _ in range(max_iter):
        grad_a = grad_b = 0.0
        for p, y in zip(probs, labels):
            z = a * p + b
            pred = _sigmoid(z)
            err = pred - y
            grad_a += err * p
            grad_b += err
        a -= lr * grad_a / len(probs)
        b -= lr * grad_b / len(probs)
    return a, b


def fit_calibration(conn, *, model_version: str) -> Dict[str, Any]:
    """Fit per-outcome Platt scaling from joined predictions and persist."""
    n = labelled_count(conn)
    if n < config.SUPERVISED_MIN_LABELS:
        msg = f"Calibration gated: {n}/{config.SUPERVISED_MIN_LABELS} labelled matches"
        logger.info(msg)
        return {"fitted": False, "n_labels": n, "message": msg}

    rows = conn.execute(
        """
        SELECT p.probs_json, mr.ftr
        FROM prediction p
        JOIN fixture f ON f.fixture_id = p.fixture_id
        JOIN match_result mr ON mr.home_team_id = f.home_id
            AND mr.away_team_id = f.away_id AND mr.ftr IS NOT NULL
        WHERE p.probs_json IS NOT NULL
        """
    ).fetchall()

    by_outcome: Dict[str, Tuple[List[float], List[int]]] = {
        o: ([], []) for o in _OUTCOMES
    }
    for r in rows:
        try:
            probs = json.loads(r["probs_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        ftr = (r["ftr"] or "").upper()
        for outcome in _OUTCOMES:
            p = probs.get(outcome)
            if p is None:
                continue
            try:
                pf = float(p)
            except (TypeError, ValueError):
                continue
            by_outcome[outcome][0].append(pf)
            by_outcome[outcome][1].append(1 if ftr == _OUTCOME_TO_FTR[outcome] else 0)

    now = datetime.now(timezone.utc).isoformat()
    fitted: Dict[str, Tuple[float, float]] = {}
    for outcome in _OUTCOMES:
        probs, labels = by_outcome[outcome]
        if len(probs) < 50:
            continue
        a, b = _fit_platt(probs, labels)
        fitted[outcome] = (a, b)
        conn.execute(
            """
            INSERT INTO model_calibration (fitted_at, model_version, outcome, slope, intercept, n_labels)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(model_version, outcome) DO UPDATE SET
                fitted_at=excluded.fitted_at,
                slope=excluded.slope,
                intercept=excluded.intercept,
                n_labels=excluded.n_labels
            """,
            (now, model_version, outcome, a, b, len(probs)),
        )
    conn.commit()
    if not fitted:
        return {"fitted": False, "n_labels": n, "message": "Insufficient per-outcome samples"}
    return {
        "fitted": True,
        "n_labels": n,
        "model_version": model_version,
        "outcomes": {k: {"slope": v[0], "intercept": v[1]} for k, v in fitted.items()},
    }


def load_calibration(conn, *, model_version: str) -> Optional[Dict[str, Tuple[float, float]]]:
    rows = conn.execute(
        """
        SELECT outcome, slope, intercept FROM model_calibration
        WHERE model_version = ?
        """,
        (model_version,),
    ).fetchall()
    if not rows:
        return None
    return {str(r["outcome"]): (float(r["slope"]), float(r["intercept"])) for r in rows}


def apply_calibration(
    probs: Dict[str, float],
    params: Optional[Dict[str, Tuple[float, float]]],
) -> Dict[str, float]:
    """Apply Platt scaling and renormalise to sum to 1."""
    if not params:
        return probs
    out: Dict[str, float] = {}
    for key in ("home", "draw", "away"):
        p = float(probs.get(key, 0.0))
        if key in params:
            a, b = params[key]
            out[key] = _sigmoid(a * p + b)
        else:
            out[key] = p
    s = sum(out.values())
    if s <= 0:
        return probs
    return {k: v / s for k, v in out.items()}


def train_if_ready(conn) -> Dict[str, Any]:
    n = labelled_count(conn)
    if n < config.SUPERVISED_MIN_LABELS:
        msg = f"Supervised training gated: {n}/{config.SUPERVISED_MIN_LABELS} labelled matches"
        logger.info(msg)
        return {"trained": False, "n_labels": n, "message": msg}
    if not config.SUPERVISED_ENABLED:
        return {
            "trained": False,
            "n_labels": n,
            "message": (
                f"{n} labels available — set SUPERVISED_ENABLED=1 to apply calibration in predictions"
            ),
        }
    from dg.model.registry import model_version

    return fit_calibration(conn, model_version=model_version())
