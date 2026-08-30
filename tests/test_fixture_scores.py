"""Tests for Flashscore parse/match and score sync."""
from __future__ import annotations

from dg.ingest.fixture_scores import sync_flashscore_scores, sync_fixture_scores, upsert_api_result
from dg.report.loaders import enrich_prediction_for_display
from dg.report.results_attach import attach_result_to_prediction, build_result_index
from dg.sources.apifootball import parse_finished_score
from dg.sources.flashscore import parse_score_data_html, teams_match
from dg.storage.db import connect, init_db

SAMPLE_HTML = (
    "<h4>ARGENTINA: Primera</h4>"
    '<span>17:00</span>Kairat Almaty (Kaz) - Sutjeska (Mne) '
    '<a href="/match/a/" class="sched">&nbsp;-&nbsp;</a><br />'
    "<span>20:45</span>Caicara U20 - Comercial PI U20 "
    '<a href="/match/b/" class="fin">0-3</a><br />'
    '<span class="live">83\'</span>GV San Jose - Tomayapo '
    '<a href="/match/c/" class="live">2-2</a><br />'
)


def test_parse_score_data_html_finished_and_skips_sched():
    rows = parse_score_data_html(SAMPLE_HTML, finished_only=True)
    assert len(rows) == 1
    assert rows[0]["home"] == "Caicara U20"
    assert rows[0]["away"] == "Comercial PI U20"
    assert rows[0]["fthg"] == 0 and rows[0]["ftag"] == 3
    assert rows[0]["is_live"] is False


def test_parse_score_data_html_includes_live_when_requested():
    rows = parse_score_data_html(SAMPLE_HTML, finished_only=False)
    assert len(rows) == 2
    live = next(r for r in rows if r["home"] == "GV San Jose")
    assert live["fthg"] == 2 and live["ftag"] == 2
    assert live["is_live"] is True


def test_teams_match_fuzzy():
    assert teams_match("Derby", "Derby County")
    assert teams_match("Swansea", "Swansea City")
    assert teams_match("Man United", "Manchester United")
    assert not teams_match("Arsenal", "Chelsea")


def test_cooldown_blocks_fetch(monkeypatch):
    from dg.sources import flashscore as fs

    fs.reset_cooldown()
    fs._record_cooldown(600)
    try:
        import pytest

        with pytest.raises(fs.FlashscoreCooldownError):
            fs.fetch_score_data_html()
    finally:
        fs.reset_cooldown()


def test_parse_finished_score_ft():
    item = {
        "fixture": {"id": 99, "status": {"short": "FT"}},
        "goals": {"home": 2, "away": 1},
        "score": {
            "halftime": {"home": 1, "away": 0},
            "fulltime": {"home": 2, "away": 1},
        },
    }
    s = parse_finished_score(item)
    assert s is not None
    assert s["fixture_id"] == 99
    assert s["fthg"] == 2 and s["ftag"] == 1 and s["ftr"] == "H"


def test_api_result_attaches_to_prediction(tmp_path):
    conn = init_db(connect(tmp_path / "scores.db"))
    fixture = {
        "fixture_id": 1557377,
        "date_utc": "2026-08-29T14:00:00+00:00",
        "league": "Championship",
        "home_name": "Derby",
        "away_name": "Swansea",
        "home_id": 69,
        "away_id": 76,
    }
    upsert_api_result(
        conn,
        fixture,
        {"fthg": 2, "ftag": 0, "ftr": "H", "hthg": 1, "htag": 0, "status": "FT"},
    )
    conn.commit()
    rows = conn.execute("SELECT * FROM match_result WHERE source='api-football'").fetchall()
    assert len(rows) == 1
    index = build_result_index(list(rows))
    pred = {
        "home_id": 69,
        "away_id": 76,
        "date_utc": "2026-08-29T14:00:00+00:00",
        "lean": "Home",
    }
    attach_result_to_prediction(pred, index)
    assert pred["completed"] is True
    assert pred["ft_score"] == "2–0"
    conn.close()


def test_sync_flashscore_writes_matching_fixture(tmp_path, monkeypatch):
    from dg import config

    monkeypatch.setattr(config, "API_FOOTBALL_KEY", "")
    conn = init_db(connect(tmp_path / "fs_sync.db"))
    conn.execute(
        "INSERT INTO dg_snapshot (generated_at, scraped_at, payload_sha256, n_teams) VALUES (?,?,?,?)",
        ("2026-08-30T00:00:00+00:00", "2026-08-30T00:00:00+00:00", "x", 1),
    )
    conn.execute(
        """
        INSERT INTO fixture (
            fixture_id, date_utc, league, league_id, home_id, away_id,
            home_name, away_name, first_seen_at, last_seen_at, raw_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            1557377,
            "2026-08-28T14:00:00+00:00",
            "Championship",
            40,
            69,
            76,
            "Derby",
            "Swansea",
            "2026-08-28T00:00:00+00:00",
            "2026-08-28T00:00:00+00:00",
            "{}",
        ),
    )
    conn.execute(
        """
        INSERT INTO prediction (
            fixture_id, snapshot_id, model_version, predicted_at,
            lean, confidence, match_character, score, scores_json, drivers_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            1557377,
            1,
            "test",
            "2026-08-28T00:00:00+00:00",
            "Home",
            "high",
            "open",
            0.3,
            "{}",
            "[]",
        ),
    )
    conn.commit()

    scraped = [
        {
            "league": "ENGLAND: Championship",
            "home": "Derby",
            "away": "Swansea",
            "fthg": 2,
            "ftag": 1,
            "is_live": False,
            "kickoff_hint": "14:00",
        }
    ]
    summary = sync_flashscore_scores(conn, scraped_rows=scraped)
    assert summary["written"] == 1
    row = conn.execute(
        "SELECT fthg, ftag, ftr FROM match_result WHERE source='flashscore'"
    ).fetchone()
    assert row is not None
    assert int(row["fthg"]) == 2 and int(row["ftag"]) == 1 and row["ftr"] == "H"
    conn.close()


def test_sync_scores_skips_without_key_still_runs_flashscore(tmp_path, monkeypatch):
    from dg import config

    monkeypatch.setattr(config, "API_FOOTBALL_KEY", "")
    conn = init_db(connect(tmp_path / "nokey.db"))
    summary = sync_fixture_scores(conn)
    # No candidates → flashscore still runs with 0 candidates
    assert summary["flashscore"]["candidates"] == 0
    assert summary.get("skipped_no_key") is True
    conn.close()


def test_enrich_awaiting_score_for_past_kickoff():
    pred = {
        "home_name": "A",
        "away_name": "B",
        "lean": "Home",
        "confidence": "medium",
        "drivers": [],
        "probs": {},
        "markets": {},
        "date_utc": "2026-08-28T12:00:00+00:00",
        "completed": False,
        "ft_score": None,
    }
    out = enrich_prediction_for_display(pred)
    assert out["awaiting_score"] is True
    assert out["completed"] is False


def test_enrich_not_awaiting_when_completed():
    pred = {
        "home_name": "A",
        "away_name": "B",
        "lean": "Home",
        "confidence": "high",
        "drivers": [],
        "probs": {"home": 0.6},
        "markets": {},
        "date_utc": "2026-08-28T12:00:00+00:00",
        "completed": True,
        "ft_score": "1–0",
        "ftr": "H",
        "result_row": {
            "fthg": 1,
            "ftag": 0,
            "ftr": "H",
            "hthg": None,
            "htag": None,
            "hs": None,
            "as_shots": None,
            "hst": None,
            "ast": None,
            "hc": None,
            "ac": None,
            "hy": None,
            "ay": None,
            "hr": None,
            "ar": None,
        },
    }
    out = enrich_prediction_for_display(pred)
    assert out["awaiting_score"] is False
    assert out["lean_result_key"] == "hit"
