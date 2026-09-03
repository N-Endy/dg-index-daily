"""Read-only audit of stated market probabilities vs actual lean-hit rates."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from dg import config
from dg.model.supervised import (
    _auc,
    _auc_se,
    _fit_logit_platt,
    _logit,
    _samples_for_market_calibration,
    _sigmoid,
)

_BANDS: Tuple[Tuple[float, float], ...] = (
    (0.50, 0.60),
    (0.60, 0.70),
    (0.70, 0.80),
    (0.80, 0.90),
    (0.90, 1.01),
)


def _band_label(lo: float, hi: float) -> str:
    return f"{int(lo * 100)}-{int(min(hi, 1.0) * 100)}"


def _band_for(p: float) -> Optional[str]:
    for lo, hi in _BANDS:
        if lo <= p < hi:
            return _band_label(lo, hi)
    return None


def _logloss(probs: List[float], labels: List[int]) -> Optional[float]:
    if not probs:
        return None
    total = 0.0
    for p, y in zip(probs, labels):
        pc = min(1.0 - 1e-9, max(1e-9, float(p)))
        total += -(y * math.log(pc) + (1 - y) * math.log(1.0 - pc))
    return total / len(probs)


def _cv_logloss(
    probs: List[float],
    labels: List[int],
    *,
    folds: int = 5,
    seed: int = 0,
) -> Dict[str, Optional[float]]:
    n = len(probs)
    if n < folds * 4 or len(set(labels)) < 2:
        return {"raw": _logloss(probs, labels), "calib": None, "const": None}
    import numpy as np

    rng = np.random.default_rng(seed)
    order = rng.permutation(n)
    xs = [probs[i] for i in order]
    ys = [labels[i] for i in order]
    idx = np.array_split(np.arange(n), folds)
    raw_ll: List[float] = []
    cal_ll: List[float] = []
    const_ll: List[float] = []
    for i in range(folds):
        te = [int(j) for j in idx[i]]
        tr = [int(j) for f in range(folds) if f != i for j in idx[f]]
        x_tr = [xs[j] for j in tr]
        y_tr = [ys[j] for j in tr]
        x_te = [xs[j] for j in te]
        y_te = [ys[j] for j in te]
        if len(set(y_tr)) < 2 or not x_te:
            continue
        a, b = _fit_logit_platt(x_tr, y_tr)
        p_cal = [_sigmoid(a * _logit(p) + b) for p in x_te]
        base = sum(y_tr) / len(y_tr)
        p_const = [base] * len(x_te)
        r = _logloss(x_te, y_te)
        c = _logloss(p_cal, y_te)
        k = _logloss(p_const, y_te)
        if r is not None:
            raw_ll.append(r)
        if c is not None:
            cal_ll.append(c)
        if k is not None:
            const_ll.append(k)

    def _mean(vals: List[float]) -> Optional[float]:
        return sum(vals) / len(vals) if vals else None

    return {"raw": _mean(raw_ll), "calib": _mean(cal_ll), "const": _mean(const_ll)}


def _reliability(probs: List[float], labels: List[int]) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[int]] = { _band_label(lo, hi): [0, 0] for lo, hi in _BANDS }
    for p, y in zip(probs, labels):
        b = _band_for(float(p))
        if not b:
            continue
        buckets[b][1] += 1
        buckets[b][0] += int(y)
    rows: List[Dict[str, Any]] = []
    for lo, hi in _BANDS:
        b = _band_label(lo, hi)
        hits, n = buckets[b]
        if n <= 0:
            continue
        rows.append(
            {
                "band": b,
                "n": n,
                "hit_rate": hits / n,
                "stated_mid": (lo + min(hi, 1.0)) / 2.0,
            }
        )
    return rows


def market_probability_audit(conn) -> Dict[str, Any]:
    """
    Per-market reliability, AUC, CV log-loss vs a constant base rate, and
    matchweek coverage. Read-only.
    """
    samples = _samples_for_market_calibration(conn)
    min_auc = float(config.STRONGEST_MIN_AUC)
    min_labels = int(config.STRONGEST_AUC_MIN_LABELS)
    min_weeks = int(config.STRONGEST_AUC_MIN_WEEKS)
    markets: List[Dict[str, Any]] = []
    for mkey, (probs, labels, weeks) in sorted(samples.items()):
        n = len(probs)
        if n == 0:
            continue
        n_weeks = len(set(weeks))
        base = sum(labels) / n
        auc = _auc(probs, labels)
        n_pos = sum(labels)
        n_neg = n - n_pos
        auc_se = _auc_se(auc, n_pos, n_neg) if auc is not None else None
        auc_upper = None
        if auc is not None:
            auc_upper = auc + 1.64 * auc_se if auc_se is not None else auc
        powered = n >= min_labels and n_weeks >= min_weeks
        cv = _cv_logloss(probs, labels)
        calib_ll = cv.get("calib")
        const_ll = cv.get("const")
        if not powered:
            verdict = "gate-dormant"
        elif auc_upper is not None and auc_upper < min_auc:
            verdict = "show-only"
        elif auc is not None and auc >= min_auc:
            verdict = "rank-worthy"
        elif calib_ll is not None and const_ll is not None and calib_ll < const_ll - 0.005:
            verdict = "calibrate-display"
        else:
            verdict = "show-only"
        markets.append(
            {
                "market_key": mkey,
                "n": n,
                "n_weeks": n_weeks,
                "base_rate": round(base, 4),
                "auc": None if auc is None else round(auc, 4),
                "auc_se": None if auc_se is None else round(auc_se, 4),
                "auc_upper": None if auc_upper is None else round(auc_upper, 4),
                "gate_powered": powered,
                "cv_logloss_raw": None if cv["raw"] is None else round(cv["raw"], 4),
                "cv_logloss_calib": None if calib_ll is None else round(calib_ll, 4),
                "cv_logloss_const": None if const_ll is None else round(const_ll, 4),
                "reliability": _reliability(probs, labels),
                "verdict": verdict,
            }
        )
    return {
        "n_markets": len(markets),
        "strongest_min_auc": min_auc,
        "strongest_auc_min_labels": min_labels,
        "strongest_auc_min_weeks": min_weeks,
        "markets": markets,
    }
