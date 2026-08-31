"""Tests for Flashscore parse/match and score sync."""
from __future__ import annotations

from dg.ingest.fixture_scores import sync_flashscore_scores, sync_fixture_scores, upsert_api_result
from dg.report.loaders import enrich_prediction_for_display
from dg.report.results_attach import attach_result_to_prediction, build_result_index
from dg.sources.apifootball import parse_finished_score
from dg.sources.flashscore import parse_score_data_html, teams_match, team_match_score
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
    assert teams_match("Man United", "Manchester Utd")
    assert teams_match("Man City", "Manchester City")
    assert not teams_match("Arsenal", "Chelsea")
    assert not teams_match("Aston Villa", "Aston Villa U18")
    assert not teams_match("Aston Villa", "Villa")
    assert team_match_score("Rayo Vallecano", "Rayo") >= 80


def test_match_flashscore_row_villa_arsenal(monkeypatch):
    from datetime import datetime, timezone

    from dg.ingest import fixture_scores as fs

    monkeypatch.setattr(
        fs,
        "_utcnow",
        lambda: datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc),
    )
    candidates = [
        {
            "fixture_id": 1,
            "date_utc": "2026-08-31T15:00:00+00:00",
            "league": "Premier League",
            "home_name": "Aston Villa",
            "away_name": "Arsenal",
        }
    ]
    row = {
        "home": "Aston Villa",
        "away": "Arsenal",
        "league": "ENGLAND: Premier League",
        "fthg": 0,
        "ftag": 2,
        "day_offset": 0,
    }
    matched = fs.match_flashscore_row_to_fixture(row, candidates, set())
    assert matched is not None
    fx, flipped = matched
    assert fx["fixture_id"] == 1
    assert flipped is False

    flipped_row = {
        "home": "Arsenal",
        "away": "Aston Villa",
        "league": "ENGLAND: Premier League",
        "fthg": 2,
        "ftag": 0,
        "day_offset": 0,
    }
    matched_f = fs.match_flashscore_row_to_fixture(flipped_row, candidates, set())
    assert matched_f is not None
    assert matched_f[0]["fixture_id"] == 1
    assert matched_f[1] is True

    u18 = {
        "home": "Aston Villa U18",
        "away": "Crystal Palace U18",
        "league": "ENGLAND: Premier League U18",
        "fthg": 1,
        "ftag": 0,
        "day_offset": 0,
    }
    assert fs.match_flashscore_row_to_fixture(u18, candidates, set()) is None


def test_flashscore_url_day_offsets():
    from dg.sources.flashscore import flashscore_url

    assert flashscore_url(0).endswith("flashscore.mobi") or "flashscore.mobi" in flashscore_url(0)
    assert flashscore_url(0).rstrip("/").endswith("flashscore.mobi")
    assert flashscore_url(-1).endswith("?d=-1")
    assert flashscore_url(-2).endswith("?d=-2")


def test_dedupe_score_rows_across_days():
    from dg.sources.flashscore import dedupe_score_rows

    rows = [
        {
            "league": "ENGLAND: Premier League",
            "home": "Chelsea",
            "away": "Brighton",
            "fthg": 4,
            "ftag": 3,
        },
        {
            "league": "ENGLAND: Premier League",
            "home": "Chelsea",
            "away": "Brighton",
            "fthg": 4,
            "ftag": 3,
        },
        {
            "league": "ENGLAND: Championship",
            "home": "Derby",
            "away": "Swansea",
            "fthg": 2,
            "ftag": 1,
        },
    ]
    out = dedupe_score_rows(rows)
    assert len(out) == 2


def test_day_offsets_for_candidates(monkeypatch):
    from datetime import datetime, timezone

    from dg.ingest import fixture_scores as fs

    monkeypatch.setattr(
        fs,
        "_utcnow",
        lambda: datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
    )
    cands = [
        {"date_utc": "2026-08-30T15:00:00+00:00"},
        {"date_utc": "2026-08-29T15:00:00+00:00"},
        {"date_utc": "2026-08-28T15:00:00+00:00"},
        {"date_utc": "2026-08-20T15:00:00+00:00"},  # clamps to -3
    ]
    assert fs.day_offsets_for_candidates(cands) == [0, -1, -2, -3]


def test_scrape_finished_scores_merges_offsets(monkeypatch):
    from dg.sources import flashscore as fs

    html_a = (
        "<h4>ENGLAND: Premier League</h4>"
        "<span>15:00</span>Chelsea - Brighton "
        '<a class="fin">4-3</a>'
    )
    html_b = (
        "<h4>ENGLAND: Championship</h4>"
        "<span>15:00</span>Derby - Swansea "
        '<a class="fin">2-1</a><br />'
        "<h4>ENGLAND: Premier League</h4>"
        "<span>15:00</span>Chelsea - Brighton "
        '<a class="fin">4-3</a>'
    )
    pages = {0: html_a, -1: html_b}

    def fake_fetch(page, day_offset, timeout_ms):
        return pages[day_offset]

    monkeypatch.setattr(fs, "_check_cooldown", lambda: None)
    monkeypatch.setattr(fs, "_fetch_score_data_html_on_page", fake_fetch)

    class _FakePage:
        def add_init_script(self, *_a, **_k):
            return None

    class _FakeContext:
        def new_page(self):
            return _FakePage()

    class _FakeBrowser:
        def new_context(self, **_k):
            return _FakeContext()

        def close(self):
            return None

    class _FakeChromium:
        def launch(self, **_k):
            return _FakeBrowser()

    class _FakePW:
        chromium = _FakeChromium()

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    import sys
    from types import ModuleType

    fake_sync = ModuleType("playwright.sync_api")
    fake_sync.sync_playwright = lambda: _FakePW()
    fake_pw = ModuleType("playwright")
    monkeypatch.setitem(sys.modules, "playwright", fake_pw)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync)

    rows = fs.scrape_finished_scores(day_offsets=[0, -1])
    assert len(rows) == 2
    homes = {r["home"] for r in rows}
    assert homes == {"Chelsea", "Derby"}
    offsets = {r["home"]: r.get("day_offset") for r in rows}
    assert offsets["Chelsea"] == 0
    assert offsets["Derby"] == -1


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


def _seed_fixture(
    conn,
    *,
    fixture_id: int,
    date_utc: str,
    league: str,
    home_name: str,
    away_name: str,
    home_id: int,
    away_id: int,
) -> None:
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
            fixture_id,
            date_utc,
            league,
            40,
            home_id,
            away_id,
            home_name,
            away_name,
            date_utc,
            date_utc,
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
            fixture_id,
            1,
            "test",
            date_utc,
            "Home",
            "high",
            "open",
            0.3,
            "{}",
            "[]",
        ),
    )
    conn.commit()


def test_sync_flashscore_fixture_first_picks_senior_villa(monkeypatch):
    from datetime import datetime, timezone

    from dg import config
    from dg.ingest import fixture_scores as fs

    monkeypatch.setattr(config, "API_FOOTBALL_KEY", "")
    monkeypatch.setattr(
        fs,
        "_utcnow",
        lambda: datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc),
    )
    conn = init_db(connect(":memory:"))
    _seed_fixture(
        conn,
        fixture_id=1,
        date_utc="2026-08-31T15:00:00+00:00",
        league="Premier League",
        home_name="Aston Villa",
        away_name="Arsenal",
        home_id=10,
        away_id=11,
    )
    scraped = [
        {
            "league": "ENGLAND: Premier League U18",
            "home": "Aston Villa U18",
            "away": "Crystal Palace U18",
            "fthg": 1,
            "ftag": 0,
            "day_offset": 0,
        },
        {
            "league": "ENGLAND: Premier League",
            "home": "Aston Villa",
            "away": "Arsenal",
            "fthg": 0,
            "ftag": 2,
            "day_offset": 0,
        },
    ]
    summary = sync_flashscore_scores(conn, scraped_rows=scraped)
    assert summary["written"] == 1
    row = conn.execute(
        "SELECT fthg, ftag FROM match_result WHERE source='flashscore'"
    ).fetchone()
    assert int(row["fthg"]) == 0 and int(row["ftag"]) == 2
    conn.close()


def test_sync_flashscore_barca_rayo_shorthand(monkeypatch):
    from datetime import datetime, timezone

    from dg import config
    from dg.ingest import fixture_scores as fs

    monkeypatch.setattr(config, "API_FOOTBALL_KEY", "")
    monkeypatch.setattr(
        fs,
        "_utcnow",
        lambda: datetime(2026, 8, 31, 22, 0, tzinfo=timezone.utc),
    )
    conn = init_db(connect(":memory:"))
    _seed_fixture(
        conn,
        fixture_id=2,
        date_utc="2026-08-31T19:00:00+00:00",
        league="La Liga",
        home_name="Barcelona",
        away_name="Rayo Vallecano",
        home_id=20,
        away_id=21,
    )
    scraped = [
        {
            "league": "SPAIN: LaLiga",
            "home": "Barcelona",
            "away": "Rayo",
            "fthg": 3,
            "ftag": 1,
            "day_offset": 0,
        },
    ]
    summary = sync_flashscore_scores(conn, scraped_rows=scraped)
    assert summary["written"] == 1
    row = conn.execute(
        "SELECT fthg, ftag FROM match_result WHERE source='flashscore'"
    ).fetchone()
    assert int(row["fthg"]) == 3 and int(row["ftag"]) == 1
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


def test_connect_uses_wal_journal(tmp_path):
    conn = connect(tmp_path / "wal_test.db")
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()


def test_persist_flashscore_rows_batches(tmp_path, monkeypatch):
    from dg import config
    from dg.report.score_hints import persist_flashscore_rows

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "persist_batch.db")
    monkeypatch.setattr(config, "FLASHSCORE_PERSIST_BATCH", 50)
    config.ensure_dirs()
    conn = init_db(connect(config.DB_PATH))
    rows = [
        {
            "home": f"Home{i}",
            "away": f"Away{i}",
            "fthg": 1,
            "ftag": 0,
            "league": "TEST",
            "day_offset": 0,
        }
        for i in range(300)
    ]
    n = persist_flashscore_rows(conn, rows)
    assert n == 300
    count = conn.execute("SELECT COUNT(*) FROM flashscore_row").fetchone()[0]
    assert int(count) == 300
    conn.close()
