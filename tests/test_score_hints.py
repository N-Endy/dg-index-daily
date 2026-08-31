"""Tests for Flashscore near-miss hints and confirm."""
from __future__ import annotations

from dg.report.score_hints import (
    confirm_score_link,
    find_score_near_misses,
    persist_flashscore_rows,
)
from dg.sources.flashscore import league_match_score, team_match_score, teams_match
from dg.storage.db import connect, init_db


def test_team_match_score_exact_and_fuzzy():
    assert team_match_score("Derby", "Derby County") == 100
    assert team_match_score("Man United", "Manchester Utd") >= 80
    assert team_match_score("Arsenal", "Chelsea") < 80


def test_team_match_rejects_u18_and_short_subset():
    assert team_match_score("Aston Villa", "Aston Villa U18") == 0
    assert team_match_score("Aston Villa", "Villa") < 80
    assert team_match_score("Aston Villa", "Aston Villa") == 100


def test_team_match_prefix_shorthand():
    assert team_match_score("Rayo Vallecano", "Rayo") >= 80
    assert teams_match("Rayo Vallecano", "Rayo")


def test_league_match_score_token_overlap_and_empty():
    assert league_match_score("Championship", "ENGLAND: Championship") == 1.0
    assert league_match_score("Premier League", "ENGLAND: Premier League") == 1.0
    assert league_match_score("Premier League", "UGANDA: Premier League") < 0.5
    assert league_match_score("Premier League", "FIJI: Premier League") < 0.5
    assert league_match_score("ENGLAND: Premier League", "UGANDA: Premier League") < 0.5
    assert league_match_score("Premier League", "ENGLAND: Premier League U18") < 0.5
    assert league_match_score("La Liga", "SPAIN: LaLiga") >= 0.55
    assert league_match_score("", "ENGLAND: Championship") == 0.0
    assert league_match_score("Championship", "") == 0.0


def test_find_near_misses_soft_band():
    fx = {
        "home_name": "Man City",
        "away_name": "Ipswich",
        "league": "Premier League",
        "date_utc": "2026-08-30T15:00:00+00:00",
    }
    rows = [
        {
            "id": 1,
            "home": "Manchester City",
            "away": "Ipswich Town",
            "fthg": 2,
            "ftag": 0,
            "league": "ENGLAND: Premier League",
        },
        {
            "id": 2,
            "home": "Totally Unrelated",
            "away": "Also Random",
            "fthg": 1,
            "ftag": 1,
            "league": "X",
        },
    ]
    hits = find_score_near_misses(fx, rows, limit=5)
    assert len(hits) == 1
    assert hits[0]["id"] == 1
    assert hits[0]["score"] == "2–0"


def test_find_near_misses_league_gate():
    fx = {
        "home_name": "Derby",
        "away_name": "Swansea",
        "league": "Championship",
        "date_utc": "2026-08-29T14:00:00+00:00",
    }
    rows = [
        {
            "id": 1,
            "home": "Derby County",
            "away": "Swansea City",
            "fthg": 2,
            "ftag": 1,
            "league": "ENGLAND: Championship",
        },
        {
            "id": 2,
            "home": "Derby",
            "away": "Swansea",
            "fthg": 0,
            "ftag": 0,
            "league": "BRAZIL: Serie A",
        },
    ]
    hits = find_score_near_misses(fx, rows, limit=5)
    assert len(hits) == 1
    assert hits[0]["id"] == 1
    assert hits[0]["league"] == "ENGLAND: Championship"
    assert "league" in hits[0]["reason"]


def test_find_near_misses_excludes_villa_garbage():
    fx = {
        "home_name": "Aston Villa",
        "away_name": "Arsenal",
        "league": "Premier League",
        "date_utc": "2026-08-31T15:00:00+00:00",
    }
    rows = [
        {
            "id": 1,
            "home": "Aston Villa",
            "away": "Arsenal",
            "fthg": 0,
            "ftag": 2,
            "league": "ENGLAND: Premier League",
        },
        {
            "id": 2,
            "home": "Aston Villa U18",
            "away": "Crystal Palace U18",
            "fthg": 1,
            "ftag": 0,
            "league": "ENGLAND: Premier League U18",
        },
        {
            "id": 3,
            "home": "Villa",
            "away": "Kitara",
            "fthg": 1,
            "ftag": 1,
            "league": "UGANDA: Premier League",
        },
        {
            "id": 4,
            "home": "Ba",
            "away": "Rewa",
            "fthg": 1,
            "ftag": 0,
            "league": "FIJI: Premier League",
        },
    ]
    hits = find_score_near_misses(fx, rows, limit=5)
    assert len(hits) == 1
    assert hits[0]["id"] == 1
    assert hits[0]["score"] == "0–2"


def test_persist_and_confirm(tmp_path, monkeypatch):
    from dg import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "hints.db")
    config.ensure_dirs()
    conn = init_db(connect(config.DB_PATH))
    conn.execute(
        """
        INSERT INTO fixture (
            fixture_id, date_utc, league, league_id, home_id, away_id,
            home_name, away_name, first_seen_at, last_seen_at, raw_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            99,
            "2026-08-29T14:00:00+00:00",
            "Championship",
            40,
            1,
            2,
            "Derby",
            "Swansea",
            "2026-08-29T00:00:00+00:00",
            "2026-08-29T00:00:00+00:00",
            "{}",
        ),
    )
    conn.commit()
    n = persist_flashscore_rows(
        conn,
        [
            {
                "home": "Derby County",
                "away": "Swansea City",
                "fthg": 3,
                "ftag": 1,
                "league": "ENGLAND: Championship",
                "kickoff_hint": "15:00",
            }
        ],
    )
    assert n == 1
    row = conn.execute("SELECT id FROM flashscore_row").fetchone()
    assert row is not None
    result = confirm_score_link(conn, 99, int(row["id"]))
    assert result["ft_score"] == "3–1"
    mr = conn.execute(
        "SELECT fthg, ftag, source FROM match_result WHERE home_team_id=1 AND away_team_id=2"
    ).fetchone()
    assert mr is not None
    assert int(mr["fthg"]) == 3 and int(mr["ftag"]) == 1
    assert mr["source"] == "flashscore-manual"
    conn.close()
