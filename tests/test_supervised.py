"""Tests for Platt calibration."""
from __future__ import annotations

import json

from dg import config
from dg.model.supervised import _fit_platt, apply_calibration, fit_calibration
from dg.storage.db import connect, init_db


def test_platt_fit_converges_on_synthetic():
    probs = [0.2 + 0.6 * (i / 99) for i in range(100)]
    labels = [1 if p > 0.55 else 0 for p in probs]
    a, b = _fit_platt(probs, labels, lr=0.1, max_iter=300)
    assert a != 0.0


def test_apply_calibration_renormalises():
    params = {"home": (2.0, -0.5), "draw": (1.5, 0.0), "away": (1.8, 0.1)}
    out = apply_calibration({"home": 0.5, "draw": 0.3, "away": 0.2}, params)
    assert abs(sum(out.values()) - 1.0) < 1e-9


def test_fit_calibration_gated_below_min_labels(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUPERVISED_MIN_LABELS", 300)
    conn = connect(tmp_path / "cal.db")
    init_db(conn)
    summary = fit_calibration(conn, model_version="test_v1")
    conn.close()
    assert summary["fitted"] is False
    assert summary["n_labels"] < 300


def test_disabled_calibration_is_noop():
    out = apply_calibration({"home": 0.5, "draw": 0.3, "away": 0.2}, None)
    assert out == {"home": 0.5, "draw": 0.3, "away": 0.2}
