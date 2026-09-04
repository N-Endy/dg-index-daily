"""Unit tests for multi-market rule engines."""
from __future__ import annotations

from dg.model.evaluate import _market_labels
from dg.model.markets import MARKET_ORDER, predict_markets


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


def test_predict_markets_keys():
    out = predict_markets(_base_matchup())
    for key in MARKET_ORDER:
        assert key in out
        assert out[key]["lean"]
        assert out[key]["confidence"] in ("low", "medium", "high")


def test_high_pace_favours_over_goals():
    quiet = predict_markets(_base_matchup(pace_clash=70.0, nec_sum=70.0))
    wild = predict_markets(
        _base_matchup(pace_clash=140.0, nec_sum=140.0),
        sim={"percents": {"over_2_5_pct": 70}, "xg": {"home": 1.8, "away": 1.5}},
    )
    assert wild["goals_2_5"]["lean"] == "Over"
    assert wild["goals_2_5"]["score"] > quiet["goals_2_5"]["score"]


def test_btts_yes_with_open_game():
    out = predict_markets(
        _base_matchup(nec_sum=130.0, pace_clash=120.0, control_sum=70.0),
        sim={"percents": {"btts_pct": 65}},
    )
    assert out["btts"]["lean"] == "Yes"


def test_cards_lean_with_agix():
    calm = predict_markets(_base_matchup(agix_sum=60.0, pressing_intensity=0.1))
    fiery = predict_markets(
        _base_matchup(agix_sum=150.0, pressing_intensity=2.0, aggression_asymmetry=30),
        sim={"cards": {"total": 5.0}},
    )
    assert fiery["cards_3_5"]["lean"] == "Over"
    assert fiery["cards_3_5"]["score"] > calm["cards_3_5"]["score"]


def test_book_and_sim_leans_attached():
    out = predict_markets(
        _base_matchup(),
        book={"over_2_5": 1.8, "under_2_5": 2.1, "btts_yes": 1.7, "btts_no": 2.2},
        sim={"percents": {"over_2_5_pct": 55, "btts_pct": 48}},
    )
    assert out["goals_2_5"]["book_lean"] == "Over"
    assert out["goals_2_5"]["dg_lean"] == "Over"
    assert out["btts"]["book_lean"] == "Yes"


def test_market_labels_from_result():
    class R(dict):
        def __getitem__(self, k):
            return dict.get(self, k)

    mr = R(
        fthg=2,
        ftag=1,
        hthg=1,
        htag=0,
        hs=14,
        as_shots=12,
        hst=5,
        ast=4,
        hc=6,
        ac=5,
        hy=2,
        ay=1,
        hr=0,
        ar=0,
    )
    labels = _market_labels(mr)
    assert labels["goals_2_5"] == "Over"
    assert labels["goals_3_5"] == "Under"  # 2+1 = 3, not over 3.5
    assert labels["btts"] == "Yes"
    assert labels["team_goals_home_1_5"] == "Over"
    assert labels["team_goals_away_1_5"] == "Under"
    assert labels["fh_1x2"] == "Home"
    assert labels["fh_over_0_5"] == "Over"
    assert labels["corners_9_5"] == "Over"
    assert labels["shots_25_5"] == "Over"
    assert labels["sot_8_5"] == "Over"
    assert labels["cards_3_5"] == "Under"


def test_lean_side_prob_flips_negative_side():
    from dg.model.markets import _lean_side_prob

    assert abs(_lean_side_prob(0.34, "Under", "Over") - 0.66) < 1e-9
    assert _lean_side_prob(0.72, "Over", "Over") == 0.72
    assert _lean_side_prob(None, "Under", "Over") is None


def test_heuristic_under_lean_stores_p_lean_not_p_over():
    out = predict_markets(_base_matchup(pace_clash=40.0, nec_sum=40.0))
    for key in ("corners_9_5", "shots_25_5", "sot_8_5"):
        assert out[key]["lean"] == "Under"
        assert out[key]["prob"] is not None
        assert out[key]["prob"] > 0.5


def test_heuristic_over_lean_prob_above_half():
    out = predict_markets(
        _base_matchup(pace_clash=160.0, nec_sum=160.0, agix_sum=160.0, pressing_intensity=2.0)
    )
    for key in ("corners_9_5", "shots_25_5", "sot_8_5", "cards_3_5"):
        assert out[key]["lean"] == "Over"
        assert 0.15 <= out[key]["prob"] <= 0.85


def test_goals_fallback_clamped_and_lean_side():
    quiet = predict_markets(_base_matchup(pace_clash=40.0, nec_sum=40.0))
    assert quiet["goals_2_5"]["prob"] <= 0.85
    assert quiet["goals_2_5"]["prob"] >= 0.15
    if quiet["goals_2_5"]["lean"] == "Under":
        assert quiet["goals_2_5"]["prob"] >= 0.5
    wild = predict_markets(_base_matchup(pace_clash=180.0, nec_sum=180.0))
    assert wild["goals_2_5"]["prob"] <= 0.85
    assert wild["goals_3_5"]["prob"] <= 0.85


def test_poisson_under_prob_is_complement():
    gp = {
        "over_2_5": 0.35,
        "under_2_5": 0.65,
        "over_3_5": 0.20,
        "btts_yes": 0.40,
        "home_over_1_5": 0.30,
        "away_over_1_5": 0.25,
        "fh_over_0_5": 0.40,
        "fh_home": 0.3,
        "fh_draw": 0.4,
        "fh_away": 0.3,
    }
    out = predict_markets(_base_matchup(), goal_probs=gp)
    assert out["goals_2_5"]["lean"] == "Under"
    assert abs(out["goals_2_5"]["prob"] - 0.65) < 1e-6
    assert out["btts"]["lean"] == "No"
    assert abs(out["btts"]["prob"] - 0.60) < 1e-6


def test_corners_shots_sot_use_sim_pct_as_prob():
    out = predict_markets(
        _base_matchup(pace_clash=90.0),
        sim={
            "percents": {
                "corners_over_9_5_pct": 72,
                "shots_over_25_5_pct": 61,
                "sot_over_8_5_pct": 58,
            }
        },
    )
    # Style lean may be Under on quiet pace; P(lean) still comes from sim %.
    assert abs(out["corners_9_5"]["prob"] - (
        0.72 if out["corners_9_5"]["lean"] == "Over" else 0.28
    )) < 1e-6
    assert abs(out["shots_25_5"]["prob"] - (
        0.61 if out["shots_25_5"]["lean"] == "Over" else 0.39
    )) < 1e-6
    assert abs(out["sot_8_5"]["prob"] - (
        0.58 if out["sot_8_5"]["lean"] == "Over" else 0.42
    )) < 1e-6


def test_fh_over_wires_sim_fh_bias():
    from dg.model.markets import _fh_sim_over_p

    assert _fh_sim_over_p(0.0) is None
    p = _fh_sim_over_p(1.2)
    assert p is not None and 0.5 < p < 0.98

    quiet = predict_markets(_base_matchup())
    hot = predict_markets(
        _base_matchup(),
        sim={"first_half": {"xg": {"home": 0.7, "away": 0.6}}},
        goal_probs={"fh_over_0_5": 0.55},
    )
    assert hot["fh_over_0_5"]["score"] > quiet["fh_over_0_5"]["score"]
    assert hot["fh_over_0_5"]["dg_lean"] == "Over"
