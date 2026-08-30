"""Tests for API-Football score sync and awaiting-score UI state."""
from __future__ import annotations

from dg.ingest.fixture_scores import sync_fixture_scores, upsert_api_result
from dg.report.loaders import enrich_prediction_for_display
from dg.report.results_attach import attach_result_to_prediction, build_result_index
from dg.sources.apifootball import parse_finished_score
from dg.storage.db import connect, init_db


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
    assert s["hthg"] == 1 and s["htag"] == 0


def test_parse_finished_score_skips_live():
    item = {
        "fixture": {"id": 1, "status": {"short": "1H"}},
        "goals": {"home": 0, "away": 0},
        "score": {"fulltime": {"home": None, "away": None}},
    }
    assert parse_finished_score(item) is None


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


def test_sync_by_date_writes_matching_finished(tmp_path, monkeypatch):
    from dg import config
    from dg.ingest import fixture_scores as fs
    from dg.storage.db import connect, init_db

    monkeypatch.setattr(config, "API_FOOTBALL_KEY", "test-key")
    conn = init_db(connect(tmp_path / "sync_date.db"))
    # Minimal snapshot + fixture + prediction so fixtures_needing_scores finds it
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

    api_item = {
        "fixture": {"id": 1557377, "status": {"short": "FT"}},
        "goals": {"home": 2, "away": 1},
        "score": {
            "halftime": {"home": 1, "away": 0},
            "fulltime": {"home": 2, "away": 1},
        },
    }

    def fake_by_date(day: str):
        assert day == "2026-08-28"
        return [api_item]

    def fake_by_id(_fid: int):
        return []

    monkeypatch.setattr(fs, "fetch_fixtures_by_date", fake_by_date)
    monkeypatch.setattr(fs, "fetch_fixture_by_id", fake_by_id)

    summary = fs.sync_fixture_scores(conn)
    assert summary["skipped_no_key"] is False
    assert summary["days_fetched"] == 1
    assert summary["written"] == 1
    assert summary["fallback_tried"] == 0
    row = conn.execute(
        "SELECT fthg, ftag, ftr FROM match_result WHERE source='api-football'"
    ).fetchone()
    assert row is not None
    assert int(row["fthg"]) == 2 and int(row["ftag"]) == 1 and row["ftr"] == "H"
    conn.close()


def test_sync_scores_skips_without_key(tmp_path, monkeypatch):
    from dg import config

    monkeypatch.setattr(config, "API_FOOTBALL_KEY", "")
    conn = init_db(connect(tmp_path / "nokey.db"))
    summary = sync_fixture_scores(conn)
    assert summary["skipped_no_key"] is True
    assert summary["written"] == 0
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
