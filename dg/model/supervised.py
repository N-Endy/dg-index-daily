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
    from dg.report.results_attach import build_result_index, fixture_day

    n = labelled_count(conn)
    if n < config.SUPERVISED_MIN_LABELS:
        msg = f"Calibration gated: {n}/{config.SUPERVISED_MIN_LABELS} labelled matches"
        logger.info(msg)
        return {"fitted": False, "n_labels": n, "message": msg}

    result_index = build_result_index(
        conn.execute(
            """
            SELECT home_team_id, away_team_id, date, ftr
            FROM match_result
            WHERE ftr IS NOT NULL
              AND home_team_id IS NOT NULL AND away_team_id IS NOT NULL
            """
        ).fetchall()
    )

    rows = conn.execute(
        """
        SELECT p.probs_json, f.home_id, f.away_id, f.date_utc
        FROM prediction p
        JOIN fixture f ON f.fixture_id = p.fixture_id
        WHERE p.probs_json IS NOT NULL
        """
    ).fetchall()

    by_outcome: Dict[str, Tuple[List[float], List[int]]] = {
        o: ([], []) for o in _OUTCOMES
    }
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
        try:
            probs = json.loads(r["probs_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        ftr = (mr["ftr"] or "").upper()
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


def _logit(p: float, *, eps: float = 1e-4) -> float:
    clamped = min(1.0 - eps, max(eps, float(p)))
    return math.log(clamped / (1.0 - clamped))


def _fit_logit_platt(probs: List[float], labels: List[int]) -> Tuple[float, float]:
    """Fit p_cal = sigmoid(a * logit(p) + b) with Newton/IRLS. Identity if underpowered."""
    n = len(probs)
    if n < 10 or len(set(labels)) < 2:
        return 1.0, 0.0
    import numpy as np

    x = np.array([_logit(p) for p in probs], dtype=float)
    y = np.array(labels, dtype=float)
    a_mat = np.column_stack([x, np.ones(n)])
    w = np.zeros(2, dtype=float)
    for _ in range(40):
        z = a_mat @ w
        pred = np.clip(1.0 / (1.0 + np.exp(-np.clip(z, -40.0, 40.0))), 1e-9, 1.0 - 1e-9)
        grad = a_mat.T @ (pred - y)
        weights = pred * (1.0 - pred)
        hess = a_mat.T @ (a_mat * weights[:, None]) + 1e-6 * np.eye(2)
        try:
            step = np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError:
            break
        w -= step
        if float(np.max(np.abs(step))) < 1e-8:
            break
    return float(w[0]), float(w[1])


def _auc(probs: List[float], labels: List[int]) -> Optional[float]:
    """Mann–Whitney AUC of stated probability vs lean-hit. None if a class is empty."""
    pos = [p for p, y in zip(probs, labels) if y == 1]
    neg = [p for p, y in zip(probs, labels) if y == 0]
    if not pos or not neg:
        return None
    import numpy as np

    p = np.asarray(pos, dtype=float)
    n = np.asarray(neg, dtype=float)
    gt = (p[:, None] > n[None, :]).astype(float)
    eq = (p[:, None] == n[None, :]).astype(float)
    return float(np.mean(gt + 0.5 * eq))


def _auc_se(auc: float, n_pos: int, n_neg: int) -> Optional[float]:
    """Hanley–McNeil standard error of AUC. None when a class is empty."""
    if n_pos < 1 or n_neg < 1:
        return None
    q1 = auc / (2.0 - auc)
    q2 = 2.0 * auc * auc / (1.0 + auc)
    var = (
        auc * (1.0 - auc)
        + (n_pos - 1) * (q1 - auc * auc)
        + (n_neg - 1) * (q2 - auc * auc)
    ) / (n_pos * n_neg)
    return var**0.5 if var > 0 else None


def _iso_week_key(day: Optional[str]) -> Optional[str]:
    if not day:
        return None
    try:
        dt = datetime.fromisoformat(str(day)[:10])
    except ValueError:
        return None
    iso = dt.isocalendar()
    return f"{iso[0]:04d}-W{iso[1]:02d}"


def _lean_prob_from_probs(lean: Optional[str], probs: Dict[str, Any]) -> Optional[float]:
    if lean == "Home":
        raw = probs.get("home")
    elif lean == "Away":
        raw = probs.get("away")
    elif lean == "Draw":
        raw = probs.get("draw")
    else:
        return None
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _samples_for_market_calibration(
    conn,
) -> Dict[str, Tuple[List[float], List[int], List[str]]]:
    """Collect (p_lean_raw, hit, iso_week) per market from date-aware joined predictions."""
    from dg.model.evaluate import _market_labels
    from dg.model.markets import MARKET_ORDER, extract_market_lines, markets_model_tag
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
        SELECT p.lean, p.probs_json, p.markets_json, f.home_id, f.away_id, f.date_utc
        FROM prediction p
        JOIN fixture f ON f.fixture_id = p.fixture_id
        WHERE p.id IN (SELECT MAX(id) FROM prediction GROUP BY fixture_id)
          AND p.model_version LIKE ?
        """,
        (f"%+{tag}",),
    ).fetchall()

    out: Dict[str, Tuple[List[float], List[int], List[str]]] = {}

    def _add(key: str, p_raw: Optional[float], hit: bool, week: Optional[str]) -> None:
        if p_raw is None or week is None:
            return
        try:
            pf = float(p_raw)
        except (TypeError, ValueError):
            return
        bucket = out.setdefault(key, ([], [], []))
        bucket[0].append(pf)
        bucket[1].append(1 if hit else 0)
        bucket[2].append(week)

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
        week = _iso_week_key(day)
        try:
            markets = json.loads(r["markets_json"] or "{}") if r["markets_json"] else {}
        except (json.JSONDecodeError, TypeError):
            markets = {}
        labels = _market_labels(mr, extract_market_lines(markets)) if markets else {}
        for key in MARKET_ORDER:
            m = markets.get(key)
            if not isinstance(m, dict) or not m.get("lean"):
                continue
            lab = labels.get(key)
            if lab is None:
                continue
            raw = m.get("prob_raw")
            if raw is None:
                raw = m.get("prob")
            _add(key, raw, lab == m.get("lean"), week)

        try:
            probs = json.loads(r["probs_json"] or "{}") if r["probs_json"] else {}
        except (json.JSONDecodeError, TypeError):
            probs = {}
        lean = r["lean"]
        p_1x2 = _lean_prob_from_probs(lean, probs)
        ftr = (mr["ftr"] or "").upper()
        hit_1x2 = {"H": "Home", "D": "Draw", "A": "Away"}.get(ftr) == lean
        _add("match_1x2", p_1x2, hit_1x2, week)

    return out


def fit_market_prob_calibration(conn, *, model_version: str) -> Dict[str, Any]:
    """Fit per-market logit-space calibration of P(lean) vs lean-hit."""
    samples = _samples_for_market_calibration(conn)
    now = datetime.now(timezone.utc).isoformat()
    min_fit = int(config.MARKET_PROB_CALIBRATION_MIN_FIT)
    min_weeks = int(config.MARKET_PROB_CALIBRATION_MIN_WEEKS)
    fitted: Dict[str, Any] = {}

    conn.execute(
        "DELETE FROM market_prob_calibration WHERE model_version = ?",
        (model_version,),
    )
    for mkey, (probs, labels, weeks) in sorted(samples.items()):
        n = len(probs)
        if n <= 0:
            continue
        n_weeks = len(set(weeks))
        base_rate = sum(labels) / n
        auc = _auc(probs, labels)
        n_pos = sum(labels)
        n_neg = n - n_pos
        auc_se = _auc_se(auc, n_pos, n_neg) if auc is not None else None
        if n >= min_fit and n_weeks >= min_weeks and len(set(labels)) >= 2:
            slope, intercept = _fit_logit_platt(probs, labels)
        else:
            slope, intercept = 1.0, 0.0
        conn.execute(
            """
            INSERT INTO market_prob_calibration (
                model_version, market_key, slope, intercept, base_rate,
                auc, auc_se, n_labels, n_weeks, fitted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model_version,
                mkey,
                slope,
                intercept,
                base_rate,
                auc,
                auc_se,
                n,
                n_weeks,
                now,
            ),
        )
        fitted[mkey] = {
            "slope": slope,
            "intercept": intercept,
            "base_rate": base_rate,
            "auc": auc,
            "auc_se": auc_se,
            "n_labels": n,
            "n_weeks": n_weeks,
            "powered": n >= min_fit and n_weeks >= min_weeks,
        }
    conn.commit()
    return {
        "fitted": bool(fitted),
        "model_version": model_version,
        "markets": fitted,
        "n_markets": len(fitted),
    }


def load_market_prob_calibration(
    conn, *, model_version: str
) -> Dict[str, Dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT market_key, slope, intercept, base_rate, auc, auc_se, n_labels, n_weeks
            FROM market_prob_calibration
            WHERE model_version = ?
            """,
            (model_version,),
        ).fetchall()
    except Exception:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        d = dict(r)
        out[str(d["market_key"])] = {
            "slope": float(d["slope"]),
            "intercept": float(d["intercept"]),
            "base_rate": float(d["base_rate"]),
            "auc": float(d["auc"]) if d["auc"] is not None else None,
            "auc_se": float(d["auc_se"]) if d.get("auc_se") is not None else None,
            "n_labels": int(d["n_labels"]),
            "n_weeks": int(d["n_weeks"]),
        }
    return out


def load_market_aucs(
    conn, *, model_version: Optional[str] = None
) -> Dict[str, Dict[str, Any]]:
    """
    market_key -> {auc, auc_se, n_labels, n_weeks} for Strongest discrimination gating.
    """
    sql = """
        SELECT market_key, auc, auc_se, n_labels, n_weeks
        FROM market_prob_calibration
        WHERE auc IS NOT NULL
    """
    params: Tuple[Any, ...] = ()
    if model_version:
        sql += " AND model_version = ?"
        params = (model_version,)
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        try:
            out[str(r["market_key"])] = {
                "auc": float(r["auc"]),
                "auc_se": float(r["auc_se"]) if r["auc_se"] is not None else None,
                "n_labels": int(r["n_labels"]),
                "n_weeks": int(r["n_weeks"]),
            }
        except (TypeError, ValueError, KeyError):
            continue
    return out


def apply_market_prob_calibration(
    markets: Dict[str, Any],
    params: Optional[Dict[str, Dict[str, Any]]],
) -> Dict[str, Any]:
    """
    Write prob_raw from the model output and, when enabled and fitted, set
    prob to the calibrated expected hit rate of the lean side.
    """
    params = params or {}
    enabled = bool(config.MARKET_PROB_CALIBRATION_ENABLED)
    k = float(config.MARKET_CALIBRATION_SHRINKAGE)
    min_fit = int(config.MARKET_PROB_CALIBRATION_MIN_FIT)
    min_weeks = int(config.MARKET_PROB_CALIBRATION_MIN_WEEKS)
    out: Dict[str, Any] = {}
    for key, value in markets.items():
        if not isinstance(value, dict):
            out[key] = value
            continue
        m = dict(value)
        raw = m.get("prob_raw")
        if raw is None:
            raw = m.get("prob")
        try:
            p_raw = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            p_raw = None
        if p_raw is None:
            out[key] = m
            continue
        m["prob_raw"] = round(p_raw, 4)
        entry = params.get(key) if enabled else None
        if not entry:
            m["prob"] = round(p_raw, 4)
            out[key] = m
            continue
        n = int(entry.get("n_labels") or 0)
        n_weeks = int(entry.get("n_weeks") or 0)
        base = float(entry.get("base_rate") or 0.5)
        if n < min_fit or n_weeks < min_weeks:
            w = n / (n + k) if (n + k) else 0.0
            p_cal = w * p_raw + (1.0 - w) * base
        else:
            a = float(entry.get("slope") or 1.0)
            b = float(entry.get("intercept") or 0.0)
            p_cal = _sigmoid(a * _logit(p_raw) + b)
        m["prob"] = round(min(0.98, max(0.02, float(p_cal))), 4)
        out[key] = m
    return out


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
