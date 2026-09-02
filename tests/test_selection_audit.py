"""Tests for Strongest selection regret audit."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from dg.report.selection_audit import selection_regret_audit
from dg.storage.db import connect, init_db


def _seed_completed_fixture(conn, *, fixture_id: int, ftr: str = "H") -> None:
    now = datetime.now(timezone.utc).isoformat()
    home_id = fixture_id * 10
    away_id = fixture_id * 10 + 1
    home_name = f"Home{fixture_id}"
    away_name = f"Away{fixture_id}"
    kickoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    result_day = kickoff[:10].split("-")
    result_date = f"{result_day[2]}/{result_day[1]}/{result_day[0]}"
    conn.execute(
        """
        INSERT OR REPLACE INTO fixture (
            fixture_id, date_utc, league, league_id, league_country,
            home_id, away_id, home_name, away_name,
            first_seen_at, last_seen_at
        ) VALUES (?, ?, 'EPL', 39, 'England', ?, ?, ?, ?, ?, ?)
        """,
        (fixture_id, kickoff, home_id, away_id, home_name, away_name, now, now),
    )
    markets = {
        "goals_2_5": {
            "lean": "Over",
            "confidence": "high",
            "score": 0.4,
            "prob": 0.72,
            "dg_lean": "Over",
            "book_lean": "Over",
        },
        "btts": {
            "lean": "Yes",
            "confidence": "high",
            "score": 0.35,
            "prob": 0.68,
            "dg_lean": "Yes",
            "book_lean": "Yes",
        },
    }
    conn.execute(
        """
        INSERT INTO prediction (
            fixture_id, predicted_at, model_version, lean, confidence, score,
            probs_json, markets_json, drivers_json
        ) VALUES (?, ?, 'rule_v2', 'Home', 'high', 0.4, ?, ?, '[]')
        """,
        (
            fixture_id,
            now,
            json.dumps({"home": 0.7, "draw": 0.2, "away": 0.1}),
            json.dumps(markets),
        ),
    )
    conn.execute(
        """
        INSERT INTO match_result (
            source, season, league_code, date, home_name, away_name,
            home_team_id, away_team_id, fthg, ftag, ftr
        ) VALUES ('test', '2627', 'E0', ?, ?, ?, ?, ?, 2, 1, ?)
        """,
        (result_date, home_name, away_name, home_id, away_id, ftr),
    )
    conn.commit()


def test_selection_regret_audit_counts_oracle(tmp_path):
    conn = init_db(connect(tmp_path / "audit.db"))
    _seed_completed_fixture(conn, fixture_id=9001, ftr="H")
    stats = selection_regret_audit(conn, days=30)
    assert stats["n_oracle_graded"] >= 1
    assert stats["n_selected_graded"] >= 1
    assert stats["selected_hit_rate"] is not None
    conn.close()
