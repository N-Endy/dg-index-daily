"""Tests for attaching FT scores to predictions."""
from __future__ import annotations

from dg.report.loaders import enrich_prediction_for_display
from dg.report.results_attach import (
    attach_result_to_prediction,
    build_result_index,
    ft_score_display,
    lean_result,
    market_lean_result,
    normalize_result_day,
)


def test_normalize_result_day_formats():
    assert normalize_result_day("29/08/2026") == "2026-08-29"
    assert normalize_result_day("5/1/26") == "2026-01-05"
    assert normalize_result_day("2026-08-29") == "2026-08-29"
    assert normalize_result_day("2026-08-29T11:00:00+00:00") == "2026-08-29"
    assert normalize_result_day("") is None
    assert normalize_result_day(None) is None


def test_ft_score_display():
    assert ft_score_display(2, 1) == "2–1"
    assert ft_score_display(0, 0) == "0–0"
    assert ft_score_display(None, 1) is None


def test_lean_result_hit_miss():
    assert lean_result("Home", "H") == ("hit", "Lean hit")
    assert lean_result("Away", "H") == ("miss", "Lean miss")
    assert lean_result("Draw", "D") == ("hit", "Lean hit")
    assert lean_result("Home", None) == ("pending", "")


def test_attach_by_team_and_day_ignores_rematch():
    rows = [
        {
            "home_team_id": 10,
            "away_team_id": 20,
            "date": "01/09/2025",
            "fthg": 1,
            "ftag": 0,
            "ftr": "H",
            "hthg": 0,
            "htag": 0,
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
        {
            "home_team_id": 10,
            "away_team_id": 20,
            "date": "29/08/2026",
            "fthg": 2,
            "ftag": 1,
            "ftr": "H",
            "hthg": 1,
            "htag": 0,
            "hs": 12,
            "as_shots": 8,
            "hst": 5,
            "ast": 3,
            "hc": 6,
            "ac": 4,
            "hy": 2,
            "ay": 1,
            "hr": 0,
            "ar": 0,
        },
    ]
    index = build_result_index(rows)
    pred = {
        "home_id": 10,
        "away_id": 20,
        "date_utc": "2026-08-29T11:00:00+00:00",
        "lean": "Home",
    }
    attach_result_to_prediction(pred, index)
    assert pred["completed"] is True
    assert pred["ft_score"] == "2–1"
    assert pred["ft_home"] == 2
    assert pred["ft_away"] == 1
    assert pred["ftr"] == "H"

    wrong_day = {
        "home_id": 10,
        "away_id": 20,
        "date_utc": "2025-12-01T15:00:00+00:00",
        "lean": "Home",
    }
    attach_result_to_prediction(wrong_day, index)
    assert wrong_day["completed"] is False
    assert wrong_day["ft_score"] is None


def test_enrich_lean_and_market_hit_miss():
    pred = {
        "home_name": "Home FC",
        "away_name": "Away FC",
        "lean": "Home",
        "confidence": "high",
        "drivers": [],
        "probs": {"home": 0.6, "draw": 0.2, "away": 0.2},
        "completed": True,
        "ft_score": "2–1",
        "ft_home": 2,
        "ft_away": 1,
        "ftr": "H",
        "result_row": {
            "fthg": 2,
            "ftag": 1,
            "ftr": "H",
            "hthg": 1,
            "htag": 0,
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
        "markets": {
            "goals_2_5": {
                "key": "goals_2_5",
                "lean": "Over",
                "confidence": "high",
                "prob": 0.7,
                "drivers": [],
            },
            "btts": {
                "key": "btts",
                "lean": "No",
                "confidence": "medium",
                "prob": 0.55,
                "drivers": [],
            },
        },
    }
    out = enrich_prediction_for_display(pred)
    assert out["completed"] is True
    assert out["ft_score"] == "2–1"
    assert out["lean_result_key"] == "hit"
    chips = {c["key"]: c for c in out["market_chips"]}
    assert chips["goals_2_5"]["result_key"] == "hit"  # 3 goals → Over 2.5
    assert chips["btts"]["result_key"] == "miss"  # 2-1 both scored → Yes, lean was No


def test_market_lean_result_helper():
    assert market_lean_result("Over", "Over") == ("hit", "Hit")
    assert market_lean_result("Over", "Under") == ("miss", "Miss")
    assert market_lean_result("Over", None) == ("pending", "")
