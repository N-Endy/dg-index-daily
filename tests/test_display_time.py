"""Display timezone formatting and board sort order."""
from __future__ import annotations

from dg.report.loaders import (
    _format_kickoff,
    format_generated_at,
    group_predictions_by_date,
)


def test_format_kickoff_wat():
    # 15:00 UTC → 16:00 WAT
    assert _format_kickoff("2026-08-30T15:00:00+00:00") == "Sun 30 Aug · 16:00 WAT"


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
