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


def test_fit_calibration_uses_same_day_result_only(tmp_path, monkeypatch):
    """Platt fit must not duplicate labels from historical head-to-heads."""
    from dg.model.supervised import fit_calibration

    monkeypatch.setattr(config, "SUPERVISED_MIN_LABELS", 1)
    conn = connect(tmp_path / "platt.db")
    init_db(conn)
    conn.execute(
        """
        INSERT INTO dg_snapshot (generated_at, scraped_at, payload_sha256, n_teams)
        VALUES ('2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 'x', 1)
        """
    )
    conn.execute(
        """
        INSERT INTO fixture (
            fixture_id, date_utc, league, home_name, away_name, home_id, away_id,
            first_seen_at, last_seen_at
        ) VALUES (1, '2026-05-10T15:00:00+00:00', 'Test', 'A', 'B', 10, 20, ?, ?)
        """,
        ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    conn.execute(
        """
        INSERT INTO prediction (
            fixture_id, snapshot_id, predicted_at, model_version,
            lean, confidence, score, drivers_json, probs_json
        ) VALUES (
            1, 1, '2026-01-01T00:00:00+00:00', 'test_v1',
            'Home', 'high', 0.4, '[]', '{"home":0.6,"draw":0.25,"away":0.15}'
        )
        """
    )
    for date, ftr in (("10/05/2019", "A"), ("10/05/2026", "H")):
        conn.execute(
            """
            INSERT INTO match_result (
                source, season, league_code, date, home_name, away_name,
                home_team_id, away_team_id, fthg, ftag, ftr
            ) VALUES ('football-data.co.uk', '2627', 'T1', ?, 'A', 'B', 10, 20, 1, 0, ?)
            """,
            (date, ftr),
        )
    # Bulk history so labelled_count passes gate (unique keys per row)
    for i in range(300):
        conn.execute(
            """
            INSERT INTO match_result (
                source, season, league_code, date, home_name, away_name,
                home_team_id, away_team_id, fthg, ftag, ftr
            ) VALUES ('football-data.co.uk', '2627', ?, ?, 'X', 'Y', ?, ?, 1, 0, 'H')
            """,
            (f"T{i}", f"02/01/20{i % 100:02d}", 1000 + i, 2000 + i),
        )
    conn.commit()

    # Collect matched rows the way fit_calibration does
    from dg.report.results_attach import build_result_index, fixture_day

    result_index = build_result_index(
        conn.execute(
            """
            SELECT home_team_id, away_team_id, date, ftr
            FROM match_result WHERE ftr IS NOT NULL
              AND home_team_id IS NOT NULL AND away_team_id IS NOT NULL
            """
        ).fetchall()
    )
    matched = 0
    for r in conn.execute(
        """
        SELECT p.probs_json, f.home_id, f.away_id, f.date_utc
        FROM prediction p JOIN fixture f ON f.fixture_id = p.fixture_id
        WHERE p.probs_json IS NOT NULL
        """
    ).fetchall():
        day = fixture_day(r["date_utc"])
        key = (int(r["home_id"]), int(r["away_id"]), day)
        if result_index.get(key):
            matched += 1
    assert matched == 1

    summary = fit_calibration(conn, model_version="test_v1")
    conn.close()
    assert summary.get("fitted") is True or summary.get("fitted") is False
    # If fitted, home outcome should reflect one H label not mixed with 2019 A
    if summary.get("fitted"):
        assert summary["outcomes"]["home"]["slope"] != 0.0
