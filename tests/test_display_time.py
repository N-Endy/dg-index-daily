"""Display timezone formatting and board sort order."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from dg.report.loaders import (
    DISPLAY_TZ,
    _format_kickoff,
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
