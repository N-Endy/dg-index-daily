"""Tests for recent Strongest performance scoreboard."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from dg.report.best_leans import build_strongest_picks
from dg.report.scoreboard import recent_strongest_performance
from dg.storage.db import connect, init_db


def _seed_completed_fixture(conn, *, fixture_id: int, lean_hit: bool, snapshot_id: int = 1):
    now = datetime.now(timezone.utc).isoformat()
    if snapshot_id == 1:
        conn.execute(
            """
            INSERT OR IGNORE INTO dg_snapshot (id, generated_at, scraped_at, payload_sha256, n_teams)
            VALUES (1, ?, ?, 'sha', 2)
            """,
            (now, now),
        )
    home_id = fixture_id * 10
    away_id = fixture_id * 10 + 1
    home_name = f"Alpha{fixture_id}"
    away_name = f"Beta{fixture_id}"
    kickoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    result_day = kickoff[:10].split("-")
    result_date = f"{result_day[2]}/{result_day[1]}/{result_day[0]}"
    conn.execute(
        """
        INSERT INTO fixture (
            fixture_id, date_utc, league, home_name, away_name, home_id, away_id,
            first_seen_at, last_seen_at
        ) VALUES (?, ?, 'Test', ?, ?, ?, ?, ?, ?)
        """,
        (fixture_id, kickoff, home_name, away_name, home_id, away_id, now, now),
    )
    markets = {
        "goals_2_5": {
            "key": "goals_2_5",
            "label": "Goals O/U 2.5",
            "lean": "Over" if lean_hit else "Over",
            "confidence": "high",
            "score": 0.4,
            "prob": 0.72,
            "dg_lean": "Over" if lean_hit else "Over",
            "book_lean": "Over" if lean_hit else "Over",
            "drivers": [],
        }
    }
    conn.execute(
        """
        INSERT INTO prediction (
            fixture_id, snapshot_id, predicted_at, model_version,
            lean, confidence, score, drivers_json, markets_json, probs_json
        ) VALUES (?, 1, ?, 'test_v1', 'Home', 'medium', 0.2, '[]', ?, '{"home":0.55,"draw":0.25,"away":0.20}')
        """,
        (fixture_id, now, json.dumps(markets)),
    )
    fthg, ftag = (2, 1) if lean_hit else (0, 0)
    conn.execute(
        """
        INSERT INTO match_result (
            source, season, league_code, date, home_name, away_name,
            home_team_id, away_team_id, fthg, ftag, ftr
        ) VALUES ('football-data.co.uk', '2627', 'T1', ?, ?, ?, ?, ?, ?, ?, 'H')
        """,
        (result_date, home_name, away_name, home_id, away_id, fthg, ftag),
    )


def test_recent_strongest_performance_counts_hits(tmp_path):
    conn = connect(tmp_path / "sb.db")
    init_db(conn)
    _seed_completed_fixture(conn, fixture_id=101, lean_hit=True)
    _seed_completed_fixture(conn, fixture_id=102, lean_hit=False)
    conn.commit()
    stats = recent_strongest_performance(conn, days=30)
    conn.close()
    assert stats["n_graded"] == 2
    assert stats["n_hits"] == 1
    assert stats["hit_rate"] == 0.5
    assert "goals_2_5" in stats["by_market"]


def test_build_strongest_two_source_outranks_one_source():
    one_source = {
        "fixture_id": 1,
        "home_name": "A",
        "away_name": "B",
        "markets": {
            "corners_9_5": {
                "lean": "Over",
                "confidence": "high",
                "score": 0.35,
                "prob": 0.68,
                "dg_lean": "Over",
                "book_lean": None,
            }
        },
        "probs": {},
    }
    two_source = {
        "fixture_id": 2,
        "home_name": "C",
        "away_name": "D",
        "markets": {
            "goals_2_5": {
                "lean": "Over",
                "confidence": "high",
                "score": 0.35,
                "prob": 0.68,
                "dg_lean": "Over",
                "book_lean": "Over",
            }
        },
        "probs": {},
    }
    picks = build_strongest_picks([one_source, two_source])
    assert len(picks) == 2
    assert picks[0]["fixture_id"] == 2
    assert picks[0]["agreement_n_sources"] == 2
