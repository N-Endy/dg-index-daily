"""Tests for evaluate hit-rate and market label helpers."""
from __future__ import annotations

import json
from typing import Optional

from dg.model.evaluate import _market_labels, _record_1x2_calibration, evaluate_joined
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
    # match_1x2 calibration cells should be present
    cal_keys = {
        (r["market_key"], r["agreement_tier"], r["prob_band"])
        for r in summary["calibration"]
    }
    match_cells = [k for k in cal_keys if k[0] == "match_1x2"]
    assert len(match_cells) >= 1, f"Expected match_1x2 calibration cells, got {cal_keys}"


def test_record_1x2_calibration_agree2():
    """sim and book both match lean -> agree2."""
    calib = {}
    _record_1x2_calibration(
        calib,
        lean="Home",
        ftr="H",
        probs_json=json.dumps({"home": 0.7, "draw": 0.2, "away": 0.1}),
        home_win_pct=55.0,
        draw_pct=25.0,
        away_win_pct=20.0,
        book_odds={"home_win": 1.5, "draw": 3.5, "away_win": 6.0},
    )
    assert ("match_1x2", "agree2", "lt_75") in calib
    assert calib[("match_1x2", "agree2", "lt_75")] == [1, 1]


def test_record_1x2_calibration_agree1_no_book():
    """sim matches lean but no book odds -> agree1."""
    calib = {}
    _record_1x2_calibration(
        calib,
        lean="Home",
        ftr="A",
        probs_json=json.dumps({"home": 0.7, "draw": 0.2, "away": 0.1}),
        home_win_pct=55.0,
        draw_pct=25.0,
        away_win_pct=20.0,
        book_odds=None,
    )
    assert ("match_1x2", "agree1", "lt_75") in calib
    assert calib[("match_1x2", "agree1", "lt_75")] == [0, 1]


def test_record_1x2_calibration_split():
    """sim and book disagree with lean -> split."""
    calib = {}
    _record_1x2_calibration(
        calib,
        lean="Home",
        ftr="H",
        probs_json=json.dumps({"home": 0.7, "draw": 0.2, "away": 0.1}),
        home_win_pct=20.0,
        draw_pct=50.0,
        away_win_pct=30.0,
        book_odds={"home_win": 6.0, "draw": 3.5, "away_win": 1.5},
    )
    assert ("match_1x2", "split", "lt_75") in calib


def test_record_1x2_calibration_no_prob():
    """No probs_json -> no_prob band, reaches parent."""
    calib = {}
    _record_1x2_calibration(
        calib,
        lean="Home",
        ftr="H",
        probs_json=None,
    )
    assert ("match_1x2", "none", "no_prob") in calib
    assert calib[("match_1x2", "none", "no_prob")] == [1, 1]


def test_1x2_candidate_scores_below_goals_agree2():
    """A match_1x2 candidate at ~0.42 base must score below goals_2_5 agree2 at ~0.62."""
    from dg.ai.vet_strongest import compute_publish_score
    from dg.report.market_reliability import reliability_for

    calib = {
        ("goals_2_5", "agree2", "92_plus"): {"hit_rate": 0.62, "n_graded": 400, "hits": 248},
        ("goals_2_5", "agree2", "all"): {"hit_rate": 0.62, "n_graded": 400, "hits": 248},
        ("match_1x2", "agree2", "lt_75"): {"hit_rate": 0.42, "n_graded": 200, "hits": 84},
        ("match_1x2", "agree2", "all"): {"hit_rate": 0.42, "n_graded": 500, "hits": 210},
        ("all", "agree2", "all"): {"hit_rate": 0.56, "n_graded": 1000, "hits": 560},
        ("all", "all", "all"): {"hit_rate": 0.50, "n_graded": 2000, "hits": 1000},
    }
    rg = reliability_for(calib, "goals_2_5", "agree2", 0.95)
    rm = reliability_for(calib, "match_1x2", "agree2", 0.70)
    sg = compute_publish_score(base_rate=rg["rate"], coherence=2, concerns=[])
    sm = compute_publish_score(base_rate=rm["rate"], coherence=2, concerns=[])
    assert sg > sm, f"goals_2_5 ({sg}) should outscore match_1x2 ({sm})"


def _seed_fixture_prediction(
    conn,
    *,
    fixture_id: int = 1,
    home_id: int = 10,
    away_id: int = 20,
    date_utc: str = "2026-01-15T15:00:00+00:00",
    lean: str = "Home",
    markets_json: Optional[str] = '{"goals_2_5":{"lean":"Over","confidence":"high","prob":0.7}}',
) -> None:
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
        ) VALUES (?, ?, 'Test', 'A', 'B', ?, ?, ?, ?)
        """,
        (fixture_id, date_utc, home_id, away_id, date_utc, date_utc),
    )
    conn.execute(
        """
        INSERT INTO prediction (
            fixture_id, snapshot_id, predicted_at, model_version,
            lean, confidence, score, drivers_json, markets_json, probs_json
        ) VALUES (
            ?, 1, '2026-01-01T00:00:00+00:00', 'test_v1',
            ?, 'high', 0.4, '[]', ?, '{"home":0.7,"draw":0.2,"away":0.1}'
        )
        """,
        (fixture_id, lean, markets_json),
    )


def test_evaluate_ignores_historical_head_to_head(tmp_path):
    """Same team pair on different dates must grade only the same-day result."""
    conn = connect(tmp_path / "h2h.db")
    init_db(conn)
    _seed_fixture_prediction(conn, date_utc="2026-01-15T15:00:00+00:00")
    # Historical meeting (wrong day) — home win
    conn.execute(
        """
        INSERT INTO match_result (
            source, season, league_code, date, home_name, away_name,
            home_team_id, away_team_id, fthg, ftag, ftr
        ) VALUES ('football-data.co.uk', '2024/2025', 'T1', '15/01/2020', 'A', 'B', 10, 20, 3, 0, 'H')
        """
    )
    # Actual fixture day — away win (lean Home misses)
    conn.execute(
        """
        INSERT INTO match_result (
            source, season, league_code, date, home_name, away_name,
            home_team_id, away_team_id, fthg, ftag, ftr
        ) VALUES ('football-data.co.uk', '2627', 'T1', '15/01/2026', 'A', 'B', 10, 20, 0, 2, 'A')
        """
    )
    conn.commit()
    summary = evaluate_joined(conn)
    conn.close()
    assert summary["n"] == 1
    assert summary["models"]["rule"]["hits"] == 0
    assert summary["models"]["rule"]["n_graded"] == 1
    goals = summary["markets"]["goals_2_5"]["rule"]
    assert goals["n_graded"] == 1


def test_evaluate_skips_when_no_same_day_result(tmp_path):
    conn = connect(tmp_path / "nomatch.db")
    init_db(conn)
    _seed_fixture_prediction(conn, date_utc="2026-06-01T12:00:00+00:00")
    conn.execute(
        """
        INSERT INTO match_result (
            source, season, league_code, date, home_name, away_name,
            home_team_id, away_team_id, fthg, ftag, ftr
        ) VALUES ('football-data.co.uk', '2627', 'T1', '01/06/2025', 'A', 'B', 10, 20, 1, 0, 'H')
        """
    )
    conn.commit()
    summary = evaluate_joined(conn)
    conn.close()
    assert summary["n"] == 0


def test_evaluate_dd_mm_yyyy_matches_iso_fixture_day(tmp_path):
    """FD-style DD/MM/YYYY result dates match ISO fixture date_utc."""
    conn = connect(tmp_path / "iso.db")
    init_db(conn)
    _seed_fixture_prediction(conn, date_utc="2026-03-20T18:00:00+00:00")
    conn.execute(
        """
        INSERT INTO match_result (
            source, season, league_code, date, home_name, away_name,
            home_team_id, away_team_id, fthg, ftag, ftr
        ) VALUES ('football-data.co.uk', '2627', 'T1', '20/03/2026', 'A', 'B', 10, 20, 2, 1, 'H')
        """
    )
    conn.commit()
    summary = evaluate_joined(conn)
    conn.close()
    assert summary["n"] == 1
    assert summary["models"]["rule"]["hits"] == 1
