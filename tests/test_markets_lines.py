"""Tests for market line selection and grading helpers."""
from __future__ import annotations

from dg.model.evaluate import _market_labels
from dg.model.markets import (
    DEFAULT_MARKET_LINES,
    MARKET_LINE_LADDERS,
    extract_market_lines,
    predict_markets,
    select_line,
)


def _base_matchup(**overrides):
    m = {
        "ok": True,
        "pace_clash": 100.0,
        "nec_sum": 100.0,
        "agix_sum": 100.0,
        "control_sum": 100.0,
        "pressing_mismatch": 0.0,
        "pressing_intensity": 0.0,
        "efficiency_edge": 0.0,
        "away_efficiency_edge": 0.0,
        "form_trend": 0.0,
        "aggression_asymmetry": 0.0,
        "home_nec": 50.0,
        "away_nec": 50.0,
        "history_n_home": 5,
        "history_n_away": 5,
        "attack_vs_control_home": 0.0,
        "attack_vs_control_away": 0.0,
    }
    m.update(overrides)
    return m


def test_select_line_picks_most_decisive_in_band():
    perc = {
        "corners_over_7_5_pct": 96.0,
        "corners_over_8_5_pct": 88.0,
        "corners_over_9_5_pct": 72.0,
        "corners_over_10_5_pct": 58.0,
        "corners_over_11_5_pct": 40.0,
    }
    line, pct = select_line(perc, "corners_9_5")
    assert line == 9.5
    assert pct == 72.0


def test_select_line_rejects_near_certainties_outside_band():
    perc = {
        "corners_over_7_5_pct": 96.0,
        "corners_over_8_5_pct": 92.0,
        "corners_over_9_5_pct": 90.0,
    }
    line, pct = select_line(perc, "corners_9_5")
    assert line == 9.5  # fallback when nothing in band
    assert pct == 90.0


def test_select_line_falls_back_when_ladder_missing():
    line, pct = select_line({}, "corners_9_5")
    assert line == DEFAULT_MARKET_LINES["corners_9_5"]
    assert pct is None


def test_predict_markets_includes_line_field():
    sim = {
        "percents": {
            "corners_over_9_5_pct": 68,
            "shots_over_25_5_pct": 55,
            "sot_over_8_5_pct": 62,
        },
        "cards": {"total": 4.0},
    }
    out = predict_markets(_base_matchup(), sim=sim)
    for key in ("corners_9_5", "shots_25_5", "sot_8_5", "cards_3_5"):
        assert "line" in out[key]
        assert out[key]["line"] == DEFAULT_MARKET_LINES[key]


def test_extract_market_lines_from_stored_markets():
    markets = {
        "corners_9_5": {"line": 10.5, "lean": "Over"},
        "shots_25_5": {"lean": "Under"},
    }
    lines = extract_market_lines(markets)
    assert lines["corners_9_5"] == 10.5
    assert lines["shots_25_5"] == DEFAULT_MARKET_LINES["shots_25_5"]


def test_market_labels_honours_passed_line():
    class R(dict):
        def __getitem__(self, k):
            return dict.get(self, k)

    mr = R(hc=6, ac=5, hs=14, as_shots=12, hst=5, ast=4, hy=2, ay=1, hr=0, ar=0)
    labels_default = _market_labels(mr)
    labels_custom = _market_labels(mr, {"corners_9_5": 12.5, "shots_25_5": 30.5, "sot_8_5": 10.5})
    assert labels_default["corners_9_5"] == "Over"  # 11 > 9.5
    assert labels_custom["corners_9_5"] == "Under"  # 11 <= 12.5
    assert labels_custom["shots_25_5"] == "Under"
    assert labels_custom["sot_8_5"] == "Under"
