"""Regression: existing Railway volumes must ALTER additive columns on init_db."""
from __future__ import annotations

import json
from pathlib import Path

from dg.ingest.fixtures import ingest_fixtures
from dg.storage.db import connect, init_db
from dg.storage.migrations import backfill_projection_typed

FIXTURES = Path(__file__).parent / "fixtures"

# Pre-change shape that still exists on deployed volumes.
_OLD_FIXTURE_PROJECTION_DDL = """
CREATE TABLE fixture_projection (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    snapshot_id INTEGER,
    sim_xg_home REAL,
    sim_xg_away REAL,
    home_win_pct REAL,
    draw_pct REAL,
    away_win_pct REAL,
    over_2_5_pct REAL,
    btts_pct REAL,
    matchup_pace_score REAL,
    book_odds_json TEXT,
    sim_stats_json TEXT,
    UNIQUE (fixture_id, observed_at)
)
"""

_REQUIRED_NEW_COLS = (
    "xgot_home",
    "xgot_away",
    "xgot_total",
    "sot_home",
    "sot_away",
    "sot_total",
    "value_score",
    "value_over_2_5",
    "value_btts",
    "regression_home",
    "regression_away",
    "congestion_home",
    "congestion_away",
    "over_3_5_pct",
    "sot_over_8_5_pct",
    "projected_meta_json",
)


def _table_cols(conn, table: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


# Pre-fixture_id shape that still exists on deployed Railway volumes.
_OLD_MATCH_RESULT_DDL = """
CREATE TABLE match_result (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    season TEXT,
    league_code TEXT,
    date TEXT NOT NULL,
    home_name TEXT NOT NULL,
    away_name TEXT NOT NULL,
    home_team_id INTEGER,
    away_team_id INTEGER,
    fthg INTEGER,
    ftag INTEGER,
    ftr TEXT,
    hthg INTEGER,
    htag INTEGER,
    hs INTEGER,
    as_shots INTEGER,
    hst INTEGER,
    ast INTEGER,
    hc INTEGER,
    ac INTEGER,
    hy INTEGER,
    ay INTEGER,
    hr INTEGER,
    ar INTEGER,
    closing_home REAL,
    closing_draw REAL,
    closing_away REAL,
    raw_json TEXT,
    UNIQUE (source, season, league_code, date, home_name, away_name)
)
"""


def test_init_db_adds_projection_columns_on_legacy_table(tmp_path):
    """Reproduce deploy crash: old fixture_projection + new ingest INSERT."""
    db_path = tmp_path / "legacy.db"
    conn = connect(db_path)
    # Bootstrap full schema first, then replace projection with the old shape.
    init_db(conn)
    conn.execute("DROP TABLE fixture_projection")
    conn.execute(_OLD_FIXTURE_PROJECTION_DDL)
    conn.commit()
    assert "xgot_home" not in _table_cols(conn, "fixture_projection")

    # Boot path used by run_daily / db_session
    init_db(conn)
    cols = _table_cols(conn, "fixture_projection")
    for col in _REQUIRED_NEW_COLS:
        assert col in cols, f"missing additive column {col}"

    fixtures = json.loads((FIXTURES / "fixtures_sample.json").read_text())
    n_up, n_proj, warns = ingest_fixtures(conn, fixtures, snapshot_id=1)
    conn.commit()
    assert n_up >= 1
    assert n_proj >= 1
    row = conn.execute(
        """
        SELECT xgot_home, sot_total, projected_meta_json
        FROM fixture_projection
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    assert row is not None
    # Sample fixtures have shots_on_target / projected_xg when present
    assert row["projected_meta_json"] is not None
    conn.close()


def test_init_db_adds_match_result_fixture_id_on_legacy_table(tmp_path):
    """Reproduce Railway crash: schema index on fixture_id before ALTER."""
    db_path = tmp_path / "legacy_mr.db"
    conn = connect(db_path)
    init_db(conn)
    conn.execute("DROP TABLE match_result")
    conn.execute(_OLD_MATCH_RESULT_DDL)
    conn.execute("DROP INDEX IF EXISTS idx_match_result_fixture")
    conn.commit()
    assert "fixture_id" not in _table_cols(conn, "match_result")

    init_db(conn)  # must not raise OperationalError: no such column: fixture_id
    cols = _table_cols(conn, "match_result")
    assert "fixture_id" in cols
    idx = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'index' AND name = 'idx_match_result_fixture'
        """
    ).fetchone()
    assert idx is not None

    conn.execute(
        """
        INSERT INTO match_result (
            source, date, home_name, away_name, fthg, ftag, ftr, fixture_id
        ) VALUES ('test', '2026-09-16', 'Home', 'Away', 1, 0, 'H', 4242)
        """
    )
    conn.commit()
    row = conn.execute(
        "SELECT fixture_id FROM match_result WHERE fixture_id = 4242"
    ).fetchone()
    assert row is not None
    assert int(row["fixture_id"]) == 4242
    conn.close()


def test_backfill_projection_typed_from_sim_stats(tmp_path):
    db_path = tmp_path / "bf.db"
    conn = connect(db_path)
    init_db(conn)
    conn.execute(
        """
        INSERT INTO fixture (
            fixture_id, date_utc, home_id, away_id, home_name, away_name,
            first_seen_at, last_seen_at
        ) VALUES (999001, '2026-09-17T12:00:00+00:00', 1, 2, 'Home', 'Away', 't', 't')
        """
    )
    sim = {
        "xg": {"home": "1.5", "away": "1.2", "total": "2.7"},
        "projected_xg": {"xgot": {"home": "0.8", "away": "0.7", "total": "1.5"}},
        "shots_on_target": {"home": "5.0", "away": "4.5", "total": "9.5"},
        "value_score": {"score": "2.0", "over_2_5": "1.0", "btts": "0.5"},
        "percents": {"over_3_5_pct": 40.0, "sot_over_8_5_pct": 62.0},
        "recent_match_alert": {"home": True, "away": False},
    }
    conn.execute(
        """
        INSERT INTO fixture_projection (
            fixture_id, observed_at, sim_xg_home, sim_xg_away, sim_stats_json
        ) VALUES (999001, '2026-09-17T12:00:00+00:00', 1.5, 1.2, ?)
        """,
        (json.dumps(sim),),
    )
    conn.commit()
    # Ensure typed cols exist but are NULL
    conn.execute(
        """
        UPDATE fixture_projection
        SET xgot_total = NULL, sot_total = NULL, value_score = NULL
        WHERE fixture_id = 999001
        """
    )
    conn.commit()

    n = backfill_projection_typed(conn)
    conn.commit()
    assert n >= 1
    row = conn.execute(
        """
        SELECT xgot_total, sot_total, value_score, congestion_home, sot_over_8_5_pct
        FROM fixture_projection WHERE fixture_id = 999001
        """
    ).fetchone()
    assert row["xgot_total"] == 1.5
    assert row["sot_total"] == 9.5
    assert row["value_score"] == 2.0
    assert row["congestion_home"] == 1
    assert row["sot_over_8_5_pct"] == 62.0
    conn.close()
