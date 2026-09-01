"""Tests for league country display labels."""
from __future__ import annotations

from dg.ingest.fixtures import ingest_fixtures
from dg.leagues import (
    country_for_league_id,
    format_league_label,
    league_display_for_row,
    load_league_countries,
)
from dg.storage.db import connect, init_db


def test_load_league_countries_has_english_leagues():
    m = load_league_countries()
    assert m.get(39) == "England"
    assert m.get(40) == "England"
    assert m.get(218) == "Austria"


def test_format_league_label_uppercase_both():
    assert format_league_label("Championship", country="England") == "ENGLAND – CHAMPIONSHIP"
    assert format_league_label("Bundesliga", country="Austria") == "AUSTRIA – BUNDESLIGA"


def test_format_league_label_without_country():
    assert format_league_label("Serie A", country=None) == "Serie A"
    assert format_league_label("", country="Italy") == ""


def test_country_for_league_id_unknown():
    assert country_for_league_id(39) == "England"
    assert country_for_league_id(999999) is None
    assert country_for_league_id(None) is None


def test_league_display_for_row_from_stored_country():
    row = {"league": "Championship", "league_id": 40, "league_country": "England"}
    assert league_display_for_row(row) == "ENGLAND – CHAMPIONSHIP"


def test_league_display_for_row_from_map_fallback():
    row = {"league": "2. Bundesliga", "league_id": 79}
    assert league_display_for_row(row) == "GERMANY – 2. BUNDESLIGA"


def test_ingest_stores_league_country(tmp_path):
    conn = connect(tmp_path / "league.db")
    init_db(conn)
    conn.execute(
        """
        INSERT INTO dg_snapshot (id, generated_at, scraped_at, payload_sha256, n_teams)
        VALUES (1, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 'sha', 2)
        """
    )
    fixtures = [
        {
            "fixture_id": 9001,
            "league": {"id": 40, "name": "Championship"},
            "league_id": 40,
            "home": {"id": 1, "name": "Home"},
            "away": {"id": 2, "name": "Away"},
            "date": "2026-09-01T15:00:00+00:00",
        }
    ]
    ingest_fixtures(conn, fixtures, snapshot_id=1, known_team_ids={1, 2})
    conn.commit()
    row = conn.execute(
        "SELECT league, league_id, league_country FROM fixture WHERE fixture_id = 9001"
    ).fetchone()
    conn.close()
    assert row["league"] == "Championship"
    assert row["league_id"] == 40
    assert row["league_country"] == "England"
