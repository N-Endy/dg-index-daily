"""Tests for DG sim prior extraction and Dixon–Coles probability derivation."""
from __future__ import annotations

from dg.model.goals import predict_goals
from dg.model.markets import predict_markets
from dg.model.sim_prior import (
    derive_sim_probabilities,
    extract_sim_fields,
    sot_over_probability,
)


def _sample_sim():
    return {
        "xg": {"home": "1.64", "away": "1.30", "total": "2.94"},
        "projected_xg": {
            "xg": {"home": "0.84", "away": "0.96", "total": "1.80"},
            "xgot": {"home": "0.65", "away": "0.97", "total": "1.62"},
        },
        "shots_on_target": {"home": "5.8", "away": "4.0", "total": "9.8"},
        "shot_quality": {
            "shot_accuracy": {"total": "36.7"},
            "sot_conversion": {"total": "26.7"},
        },
        "value_score": {"score": "-16.0", "over_2_5": "-3.6", "btts": "-5.9"},
        "regression_score": {"home": "0.0", "away": "0.0", "score": "0.0"},
        "recent_match_alert": {"home": False, "away": True},
        "correct_score": {
            "model": "poisson_dixon_coles",
            "top_5": [
                {"home": 1, "away": 1, "score": "1-1", "probability_pct": 11.6},
                {"home": 2, "away": 1, "score": "2-1", "probability_pct": 9.3},
            ],
            "matrix": [
                [5.64, 6.493, 4.418, 1.903],
                [8.37, 11.608, 7.275, 3.133],
                [7.177, 9.273, 5.99, 2.58],
                [3.94, 5.09, 3.288, 1.416],
            ],
        },
        "percents": {
            "home_win_pct": 44.8,
            "draw_pct": 26.0,
            "away_win_pct": 29.2,
            "over_2_5_pct": 56.3,
            "over_3_5_pct": 34.1,
            "btts_pct": 59.4,
            "sot_over_8_5_pct": 64.4,
            "fh_home_win_pct": 34.6,
            "fh_draw_pct": 45.8,
            "fh_away_win_pct": 19.7,
        },
        "first_half": {"xg": {"home": "0.68", "away": "0.44", "total": "1.11"}},
    }


def test_extract_sim_fields_xgot_sot_congestion():
    fields = extract_sim_fields(_sample_sim())
    assert fields["xgot_total"] == 1.62
    assert fields["sot_total"] == 9.8
    assert fields["congestion_away"] is True
    assert fields["congestion_any"] is True
    assert fields["has_score_matrix"] is True
    assert fields["value_score"] == -16.0
    assert len(fields["top_scores"]) == 2


def test_derive_sim_probabilities_from_matrix():
    probs = derive_sim_probabilities(_sample_sim())
    assert probs is not None
    assert probs["prior_source"] == "dg_dixon_coles"
    s = probs["home"] + probs["draw"] + probs["away"]
    assert abs(s - 1.0) < 0.02
    assert 0.02 < probs["over_2_5"] < 0.98
    assert probs.get("fh_home") is not None


def test_derive_sim_probabilities_percents_fallback():
    sim = {
        "percents": {
            "home_win_pct": 50.0,
            "draw_pct": 25.0,
            "away_win_pct": 25.0,
            "over_2_5_pct": 60.0,
            "btts_pct": 55.0,
        },
        "xg": {"home": 1.5, "away": 1.2},
    }
    probs = derive_sim_probabilities(sim)
    assert probs is not None
    assert probs["prior_source"] == "dg_percents"
    assert abs(probs["home"] - 0.5) < 0.01


def test_predict_goals_prefers_sim_prior():
    matchup = {
        "home_ortg": 1.5,
        "home_drtg": 1.1,
        "away_ortg": 1.2,
        "away_drtg": 1.3,
        "home_coef": 1.0,
        "away_coef": 1.0,
    }
    out = predict_goals(matchup, sim=_sample_sim())
    assert out["prior_source"] == "dg_dixon_coles"
    assert out["version"].startswith("goals_v2")


def test_predict_goals_fallback_without_sim():
    out = predict_goals(
        {
            "home_ortg": 1.5,
            "home_drtg": 1.1,
            "away_ortg": 1.2,
            "away_drtg": 1.3,
            "home_coef": 1.0,
            "away_coef": 1.0,
        }
    )
    assert out.get("prior_source") == "homemade_poisson"


def test_sot_over_probability_xgot_led():
    # No congestion so volume signals dominate
    sim = _sample_sim()
    sim["recent_match_alert"] = {"home": False, "away": False}
    p, drivers = sot_over_probability(sim, line=8.5, sim_pct=64.4)
    assert p is not None
    assert 0.55 < p < 0.95  # projected SOT 9.8 + ladder 64% → Over
    assert any("SOT" in d or "sim" in d for d in drivers)


def test_markets_sot_uses_xgot():
    matchup = {
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
        "consistency_mean": 0.6,
        "attack_vs_control_home": 0.0,
        "attack_vs_control_away": 0.0,
    }
    sim = _sample_sim()
    sim["recent_match_alert"] = {"home": False, "away": False}
    gp = predict_goals(matchup, sim=sim)
    markets = predict_markets(matchup, sim=sim, goal_probs=gp)
    assert markets["sot_8_5"]["lean"] == "Over"
    assert markets["sot_8_5"]["prob"] is not None
    assert markets["sot_8_5"]["prob"] > 0.5
    assert markets["version"].startswith("markets_v4")
