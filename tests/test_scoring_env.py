"""Tests for scoring-environment mean reversion metric and Over gate bump."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from dg import config
from dg.report.best_leans import select_strongest_lean
from dg.report.scoring_env import compute_scoring_environment, over_market_prob_bump


def test_compute_scoring_environment_hot_window():
    as_of = date(2026, 9, 3)
    # 40 baseline matches at ~2.7 GPM; 35 recent at ~3.5 GPM
    rows = []
    for i in range(40):
        age = 20 + (i % 60)  # mostly outside 14d, within 90d
        rows.append(
            {
                "date": (as_of - timedelta(days=age)).isoformat(),
                "fthg": 1,
                "ftag": 2,  # 3 goals — wait want 2.7 avg: use 1+2=3 and 1+1=2
            }
        )
    # Fix baseline to exactly 2 goals most of the time
    rows = []
    for i in range(50):
        age = 15 + i  # 15..64 → outside recent 14d, inside 90d
        rows.append(
            {
                "date": (as_of - timedelta(days=age)).isoformat(),
                "fthg": 1,
                "ftag": 1,  # 2.0 GPM baseline-ish
            }
        )
    for i in range(35):
        age = i % 14  # within 14d
        rows.append(
            {
                "date": (as_of - timedelta(days=age)).isoformat(),
                "fthg": 2,
                "ftag": 2,  # 4.0 GPM recent
            }
        )
    env = compute_scoring_environment(
        rows,
        as_of=as_of,
        recent_days=14,
        baseline_days=90,
        min_matches=30,
        stretch_ratio=1.12,
        stretch_delta=0.35,
    )
    assert env["powered"] is True
    assert env["stretched"] is True
    assert env["gpm_recent"] is not None and env["gpm_recent"] > 3.5
    assert env["gpm_baseline"] is not None and env["gpm_baseline"] < 3.0
    assert env["caution"]


def test_compute_scoring_environment_cool_window():
    as_of = date(2026, 9, 3)
    rows = []
    for i in range(40):
        age = i % 14  # recent
        rows.append(
            {
                "date": (as_of - timedelta(days=age)).isoformat(),
                "fthg": 1,
                "ftag": 1,
            }
        )
    for i in range(40):
        age = 20 + i  # baseline-only
        rows.append(
            {
                "date": (as_of - timedelta(days=age)).isoformat(),
                "fthg": 1,
                "ftag": 1,
            }
        )
    env = compute_scoring_environment(
        rows,
        as_of=as_of,
        min_matches=30,
        stretch_ratio=1.12,
        stretch_delta=0.35,
    )
    assert env["powered"] is True
    assert env["stretched"] is False
    assert env["caution"] is None


def test_underpowered_skips_stretch():
    as_of = date(2026, 9, 3)
    rows = [
        {
            "date": (as_of - timedelta(days=1)).isoformat(),
            "fthg": 5,
            "ftag": 5,
        }
    ]
    env = compute_scoring_environment(rows, as_of=as_of, min_matches=30)
    assert env["powered"] is False
    assert env["stretched"] is False


def _base_pred(market_key: str, *, lean: str, prob: float):
    return {
        "fixture_id": 1,
        "home_name": "Home FC",
        "away_name": "Away FC",
        "league": "EPL",
        "date_utc": "2026-09-03T15:00:00+00:00",
        "lean": "Home",
        "confidence": "medium",
        "score": 0.2,
        "drivers": [],
        "dg_sim_lean": "Away",
        "book_lean": "Away",
        "probs": {"home": 0.55, "draw": 0.25, "away": 0.20},
        "markets": {
            market_key: {
                "lean": lean,
                "confidence": "high",
                "score": 0.4,
                "prob": prob,
                "dg_lean": lean,
                "book_lean": lean,
            }
        },
    }


def test_over_gate_bump_when_stretched(monkeypatch):
    monkeypatch.setattr(config, "STRONGEST_MIN_PROB", 0.65)
    monkeypatch.setattr(config, "STRONGEST_MIN_PROB_BY_MARKET", {"fh_over_0_5": 0.80})
    monkeypatch.setattr(config, "SCORING_ENV_OVER_PROB_BUMP", 0.05)

    hot = {
        "stretched": True,
        "over_prob_bump": 0.05,
        "caution": "hot",
    }
    assert over_market_prob_bump("goals_2_5", "Over", hot) == 0.05
    assert over_market_prob_bump("goals_2_5", "Under", hot) == 0.0
    assert over_market_prob_bump("corners_9_5", "Over", hot) == 0.0

    # 0.66 clears global 0.65 but fails 0.70 under dampener
    pred = _base_pred("goals_2_5", lean="Over", prob=0.66)
    assert select_strongest_lean(pred, scoring_env=hot) is None
    assert select_strongest_lean(pred, scoring_env=None) is not None

    pred72 = _base_pred("goals_2_5", lean="Over", prob=0.72)
    pick = select_strongest_lean(pred72, scoring_env=hot)
    assert pick is not None
    assert pick["market_key"] == "goals_2_5"

    # FH 0.82 clears 0.80 but fails 0.85 under dampener
    fh = _base_pred("fh_over_0_5", lean="Over", prob=0.82)
    assert select_strongest_lean(fh, scoring_env=hot) is None
    assert select_strongest_lean(fh, scoring_env=None) is not None

    fh86 = _base_pred("fh_over_0_5", lean="Over", prob=0.86)
    assert select_strongest_lean(fh86, scoring_env=hot) is not None


def test_cool_env_keeps_normal_floors(monkeypatch):
    monkeypatch.setattr(config, "STRONGEST_MIN_PROB", 0.65)
    cool = {"stretched": False, "over_prob_bump": 0.0, "caution": None}
    pred = _base_pred("goals_2_5", lean="Over", prob=0.66)
    assert select_strongest_lean(pred, scoring_env=cool) is not None


def test_apply_scoring_env_dampens_over_probs(monkeypatch):
    from dg.report.scoring_env import apply_scoring_env_to_markets

    monkeypatch.setattr(config, "SCORING_ENV_DAMPEN_ENABLED", True)
    monkeypatch.setattr(config, "SCORING_ENV_OVER_PROB_DAMPEN", 0.90)
    markets = {
        "version": "t",
        "goals_2_5": {"lean": "Over", "prob": 0.70, "confidence": "high"},
        "goals_3_5": {"lean": "Under", "prob": 0.60, "confidence": "high"},
        "corners_9_5": {"lean": "Over", "prob": 0.70, "confidence": "high"},
    }
    hot = {"stretched": True, "over_prob_dampen": 0.90}
    out = apply_scoring_env_to_markets(markets, hot)
    assert out["goals_2_5"]["prob"] == pytest.approx(0.63)
    assert out["goals_2_5"]["prob_raw"] == pytest.approx(0.70)
    assert out["goals_2_5"]["scoring_env_dampened"] is True
    assert out["goals_3_5"]["prob"] == 0.60
    assert out["corners_9_5"]["prob"] == 0.70


def test_dampen_after_calib_survives_in_pipeline(monkeypatch):
    """Calibration must not undo scoring-env dampen (order: calib then dampen)."""
    from dg.model.supervised import apply_market_prob_calibration
    from dg.report.scoring_env import apply_scoring_env_to_markets

    monkeypatch.setattr(config, "SCORING_ENV_DAMPEN_ENABLED", True)
    monkeypatch.setattr(config, "MARKET_PROB_CALIBRATION_ENABLED", True)
    markets = {
        "version": "t",
        "goals_2_5": {"lean": "Over", "prob": 0.80, "confidence": "high"},
    }
    # Identity calib (empty params) still sets prob from prob_raw.
    calibrated = apply_market_prob_calibration(markets, {})
    assert calibrated["goals_2_5"]["prob"] == pytest.approx(0.80)
    hot = {"stretched": True, "over_prob_dampen": 0.90}
    final = apply_scoring_env_to_markets(calibrated, hot)
    assert final["goals_2_5"]["prob"] == pytest.approx(0.72)
    assert final["goals_2_5"]["prob_raw"] == pytest.approx(0.80)
    assert final["goals_2_5"]["scoring_env_dampened"] is True
    # Re-running calib after dampen would wipe — ensure callers do not; simulate wrong order.
    wiped = apply_market_prob_calibration(final, {})
    assert wiped["goals_2_5"]["prob"] == pytest.approx(0.80)
    # Correct order leaves dampen intact when dampen is last:
    correct = apply_scoring_env_to_markets(
        apply_market_prob_calibration(markets, {}), hot
    )
    assert correct["goals_2_5"]["prob"] == pytest.approx(0.72)
