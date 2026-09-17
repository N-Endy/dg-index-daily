"""Display timezone formatting and board sort order."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from dg.report.loaders import (
    DISPLAY_TZ,
    _format_kickoff,
    board_date_bounds,
    board_dates_in_window,
    format_generated_at,
    group_predictions_by_date,
    kickoff_date_wat,
    today_wat,
)


def test_format_kickoff_wat():
    # 15:00 UTC → 16:00 WAT
    assert _format_kickoff("2026-08-30T15:00:00+00:00") == "Sun 30 Aug · 16:00 WAT"


def test_kickoff_date_wat_crosses_midnight():
    # 23:30 UTC on Aug 31 → 00:30 WAT on Sep 1
    assert kickoff_date_wat("2026-08-31T23:30:00+00:00") == "2026-09-01"


def test_today_wat_at_midnight_wat():
    wat_now = datetime(2026, 9, 1, 0, 0, 0, tzinfo=DISPLAY_TZ)
    with patch("dg.report.loaders.datetime") as mock_dt:
        mock_dt.now.return_value = wat_now
        assert today_wat() == "2026-09-01"


def test_board_date_bounds_seven_day_window(monkeypatch):
    from dg import config

    monkeypatch.setattr(config, "BOARD_DATE_LOOKBACK_DAYS", 3)
    monkeypatch.setattr(config, "BOARD_DATE_LOOKAHEAD_DAYS", 3)
    start, end = board_date_bounds(today="2026-09-17")
    assert start == "2026-09-14"
    assert end == "2026-09-20"
    days = board_dates_in_window(today="2026-09-17")
    assert days[0] == "2026-09-14"
    assert days[-1] == "2026-09-20"
    assert len(days) == 7


def test_dashboard_context_clamps_old_date_filter(tmp_path, monkeypatch):
    """Date dropdown never lists history outside the WAT window; old ?date= is ignored."""
    import json
    from pathlib import Path

    from dg import config
    from dg.ingest.fixtures import ingest_fixtures
    from dg.ingest.ratings import ingest_ratings
    from dg.model.rules import predict_fixture
    from dg.report.loaders import load_dashboard_context
    from dg.storage.db import connect, init_db

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(config, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "ALIASES_DIR", tmp_path / "aliases")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dg.db")
    monkeypatch.setattr(config, "BOARD_DATE_LOOKBACK_DAYS", 3)
    monkeypatch.setattr(config, "BOARD_DATE_LOOKAHEAD_DAYS", 3)
    monkeypatch.setattr("dg.report.loaders.today_wat", lambda: "2026-09-17")
    config.ensure_dirs()

    fixtures_path = Path(__file__).parent / "fixtures"
    meta = json.loads((fixtures_path / "dg_meta_sample.json").read_text())
    ratings = json.loads((fixtures_path / "dg_ratings_sample.json").read_text())
    fixtures = json.loads((fixtures_path / "fixtures_sample.json").read_text())

    conn = connect(config.DB_PATH)
    init_db(conn)
    sid, _ = ingest_ratings(
        conn,
        ratings,
        generated_at=meta["generated_at"],
        payload_sha256="windowtest",
        meta=meta,
    )
    known = {int(t["team_id"]) for t in ratings}
    ingest_fixtures(conn, fixtures, snapshot_id=sid, known_team_ids=known)
    rows = conn.execute("SELECT * FROM fixture ORDER BY fixture_id").fetchall()
    assert len(rows) >= 2
    # First fixture inside window, rest far in the past.
    for i, row in enumerate(rows):
        fx = dict(row)
        day = "2026-09-16T15:00:00+00:00" if i == 0 else "2026-08-01T15:00:00+00:00"
        conn.execute(
            "UPDATE fixture SET date_utc = ? WHERE fixture_id = ?",
            (day, fx["fixture_id"]),
        )
        predict_fixture(conn, {**fx, "date_utc": day}, sid)
    conn.commit()
    conn.close()

    ctx = load_dashboard_context(date_filter="2026-08-01")
    assert ctx["date_filter"] is None  # clamped out of window
    assert "2026-08-01" not in ctx["dates"]
    assert ctx["dates"] == ["2026-09-16"]
    assert all(
        "2026-09-14" <= kickoff_date_wat(p.get("date_utc")) <= "2026-09-20"
        for p in ctx["predictions"]
    )
    assert all(kickoff_date_wat(p.get("date_utc")) == "2026-09-16" for p in ctx["predictions"])


def test_format_generated_at_wat_drops_micros():
    assert (
        format_generated_at("2026-08-30T05:28:10.032815+00:00")
        == "Sun 30 Aug 2026 · 06:28 WAT"
    )


def test_group_predictions_sorts_by_league_then_time():
    preds = [
        {
            "league": "Premier League",
            "date_utc": "2026-08-30T17:00:00+00:00",
            "home_name": "B",
        },
        {
            "league": "Championship",
            "date_utc": "2026-08-30T16:00:00+00:00",
            "home_name": "A",
        },
        {
            "league": "Premier League",
            "date_utc": "2026-08-30T14:00:00+00:00",
            "home_name": "C",
        },
        {
            "league": "Championship",
            "date_utc": "2026-08-30T12:00:00+00:00",
            "home_name": "D",
        },
    ]
    grouped = group_predictions_by_date(preds)
    assert len(grouped) == 1
    day, items = grouped[0]
    assert day == "2026-08-30"
    assert [p["home_name"] for p in items] == ["D", "A", "C", "B"]


def test_group_predictions_uses_wat_day():
    preds = [
        {
            "league": "Test",
            "date_utc": "2026-08-31T23:30:00+00:00",
            "home_name": "Late",
        },
        {
            "league": "Test",
            "date_utc": "2026-08-31T14:00:00+00:00",
            "home_name": "Early",
        },
    ]
    grouped = group_predictions_by_date(preds)
    assert len(grouped) == 2
    days = [d for d, _ in grouped]
    assert "2026-08-31" in days
    assert "2026-09-01" in days
