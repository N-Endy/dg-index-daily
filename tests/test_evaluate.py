"""Tests for evaluate hit-rate and market label helpers."""
from __future__ import annotations

from dg.model.evaluate import _market_labels, evaluate_joined
from dg.storage.db import connect, init_db


def test_market_labels_legacy_fallback():
    class R(dict):
        def __getitem__(self, k):
            return dict.get(self, k)

    mr = R(hc=4, ac=4, hs=10, as_shots=10, hst=3, ast=3)
    labels = _market_labels(mr)
    assert labels["corners_9_5"] == "Under"
    assert labels["shots_25_5"] == "Under"
    assert labels["sot_8_5"] == "Under"


def test_evaluate_hit_rate_arithmetic(tmp_path):
    db = tmp_path / "eval.db"
    conn = connect(db)
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
        ) VALUES (1, '2026-01-01T12:00:00+00:00', 'Test', 'A', 'B', 10, 20, ?, ?)
        """,
        ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    conn.execute(
        """
        INSERT INTO prediction (
            fixture_id, snapshot_id, predicted_at, model_version,
            lean, confidence, score, drivers_json, markets_json, probs_json
        ) VALUES (
            1, 1, '2026-01-01T00:00:00+00:00', 'test_v1',
            'Home', 'high', 0.4, '[]',
            '{"goals_2_5":{"lean":"Over","confidence":"high","prob":0.7}}',
            '{"home":0.7,"draw":0.2,"away":0.1}'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO match_result (
            source, season, league_code, date, home_name, away_name,
            home_team_id, away_team_id, fthg, ftag, ftr
        ) VALUES ('football-data.co.uk', '2627', 'T1', '01/01/2026', 'A', 'B', 10, 20, 2, 1, 'H')
        """
    )
    conn.commit()
    summary = evaluate_joined(conn)
    conn.close()
    assert summary["n"] == 1
    rule = summary["models"]["rule"]
    assert rule["hits"] == 1
    assert rule["n_graded"] == 1
    assert rule["hit_rate"] == 1.0
    goals = summary["markets"]["goals_2_5"]["rule"]
    assert goals["hits"] == 1
    assert goals["hit_rate"] == 1.0
