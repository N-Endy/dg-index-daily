"""Unit tests for strongest-lean selection."""
from __future__ import annotations

from dg import config
from dg.report.best_leans import (
    MIN_PROB,
    build_strongest_picks,
    collect_ai_pool_candidates,
    collect_gate_passing_candidates,
    select_strongest_lean,
    select_top_n_candidates,
)


def _base_pred(**overrides):
    pred = {
        "fixture_id": 1,
        "home_name": "Home FC",
        "away_name": "Away FC",
        "league": "EPL",
        "date_utc": "2026-08-30T15:00:00+00:00",
        "kickoff_display": "Sun 30 Aug · 16:00 WAT",
        "lean": "Home",
        "confidence": "medium",
        "score": 0.2,
        "drivers": ["rating gap (+0.20)"],
        "dg_sim_lean": "Away",
        "book_lean": "Away",
        "probs": {"home": 0.55, "draw": 0.25, "away": 0.20},
        "markets": {},
    }
    pred.update(overrides)
    return pred


def test_picks_aligned_high_prob_market():
    pred = _base_pred(
        markets={
            "goals_2_5": {
                "key": "goals_2_5",
                "label": "Goals O/U 2.5",
                "lean": "Over",
                "confidence": "high",
                "score": 0.4,
                "prob": 0.72,
                "dg_lean": "Over",
                "book_lean": "Over",
                "drivers": ["poisson over bias (+0.40)"],
            }
        }
    )
    pick = select_strongest_lean(pred)
    assert pick is not None
    assert pick["market_key"] == "goals_2_5"
    assert pick["lean"] == "Over"
    assert pick["prob"] == 0.72
    assert pick["agreement_key"] == "aligned"


def test_skips_low_confidence():
    pred = _base_pred(
        markets={
            "goals_2_5": {
                "lean": "Over",
                "confidence": "low",
                "score": 0.5,
                "prob": 0.8,
                "dg_lean": "Over",
                "book_lean": "Over",
            }
        }
    )
    assert select_strongest_lean(pred) is None


def test_medium_confidence_clears_strongest_when_prob_ok():
    pred = _base_pred(
        markets={
            "goals_2_5": {
                "lean": "Over",
                "confidence": "medium",
                "score": 0.5,
                "prob": 0.8,
                "dg_lean": "Over",
                "book_lean": "Over",
            }
        }
    )
    pick = select_strongest_lean(pred)
    assert pick is not None
    assert pick["market_key"] == "goals_2_5"
    assert pick["confidence"] == "medium"


def test_ai_pool_includes_mid_prob_and_disagreement_excluded_from_strongest():
    """AI dashboard pool is wider than Strongest: mid-prob + split sources still enter."""
    pred = _base_pred(
        lean="Home",
        confidence="medium",
        score=0.2,
        dg_sim_lean="Away",
        book_lean="Away",
        probs={"home": 0.58, "draw": 0.22, "away": 0.20},
        markets={
            "goals_2_5": {
                "lean": "Over",
                "confidence": "medium",
                "score": 0.3,
                "prob": 0.60,
                "dg_lean": "Under",
                "book_lean": "Under",
            }
        },
    )
    assert select_strongest_lean(pred) is None
    assert collect_gate_passing_candidates(pred) == []
    ai = collect_ai_pool_candidates(pred)
    keys = {c["market_key"] for c in ai}
    assert "goals_2_5" in keys
    assert "match_1x2" in keys
    goals = next(c for c in ai if c["market_key"] == "goals_2_5")
    assert goals["prob"] == 0.60
    assert goals["agreement_key"] == "split"


def test_ai_pool_rejects_low_confidence_and_below_floor():
    low_conf = _base_pred(
        confidence="low",
        probs={"home": 0.80, "draw": 0.10, "away": 0.10},
        markets={
            "goals_2_5": {
                "lean": "Over",
                "confidence": "low",
                "score": 0.5,
                "prob": 0.80,
                "dg_lean": "Over",
                "book_lean": "Over",
            }
        },
    )
    assert collect_ai_pool_candidates(low_conf) == []

    low_prob = _base_pred(
        confidence="high",
        dg_sim_lean="Home",
        book_lean="Home",
        probs={"home": 0.50, "draw": 0.30, "away": 0.20},
        markets={
            "goals_2_5": {
                "lean": "Over",
                "confidence": "high",
                "score": 0.5,
                "prob": 0.50,
                "dg_lean": "Over",
                "book_lean": "Over",
            }
        },
    )
    assert collect_ai_pool_candidates(low_prob) == []


def test_skips_low_prob_and_missing_prob():
    pred = _base_pred(
        markets={
            "goals_2_5": {
                "lean": "Over",
                "confidence": "high",
                "score": 0.5,
                "prob": MIN_PROB - 0.01,
                "dg_lean": "Over",
                "book_lean": "Over",
            }
        }
    )
    assert select_strongest_lean(pred) is None

    pred2 = _base_pred(
        markets={
            "goals_2_5": {
                "lean": "Over",
                "confidence": "high",
                "score": 0.5,
                "prob": None,
                "dg_lean": "Over",
                "book_lean": "Over",
            }
        }
    )
    assert select_strongest_lean(pred2) is None


def test_skips_split_agreement():
    pred = _base_pred(
        markets={
            "btts": {
                "lean": "Yes",
                "confidence": "high",
                "score": 0.4,
                "prob": 0.7,
                "dg_lean": "No",
                "book_lean": "No",
            }
        }
    )
    assert select_strongest_lean(pred) is None


def test_requires_both_when_both_exist():
    """Partial agreement (only one of two signals) must fail the gate."""
    pred = _base_pred(
        markets={
            "btts": {
                "lean": "Yes",
                "confidence": "high",
                "score": 0.4,
                "prob": 0.7,
                "dg_lean": "Yes",
                "book_lean": "No",
            }
        }
    )
    assert select_strongest_lean(pred) is None


def test_allows_single_signal_agreement():
    pred = _base_pred(
        markets={
            "corners_9_5": {
                "lean": "Over",
                "confidence": "high",
                "score": 0.35,
                "prob": 0.68,
                "dg_lean": "Over",
                "book_lean": None,
                "drivers": [],
            }
        }
    )
    pick = select_strongest_lean(pred)
    assert pick is not None
    assert pick["market_key"] == "corners_9_5"
    # One present signal that matches → agreement_hint treats as aligned
    assert pick["agreement_key"] == "aligned"


def test_prefers_higher_prob_when_gap_exceeds_epsilon():
    pred = _base_pred(
        markets={
            "corners_9_5": {
                "lean": "Over",
                "confidence": "high",
                "score": 0.55,
                "prob": 0.75,
                "dg_lean": "Over",
                "book_lean": "Over",
            },
            "goals_2_5": {
                "lean": "Under",
                "confidence": "high",
                "score": 0.35,
                "prob": 0.7,
                "dg_lean": "Under",
                "book_lean": "Under",
            },
        }
    )
    pick = select_strongest_lean(pred)
    assert pick is not None
    assert pick["market_key"] == "corners_9_5"


def test_poisson_tiebreaker_when_probs_close():
    pred = _base_pred(
        markets={
            "corners_9_5": {
                "lean": "Over",
                "confidence": "high",
                "score": 0.55,
                "prob": 0.71,
                "dg_lean": "Over",
                "book_lean": "Over",
            },
            "goals_2_5": {
                "lean": "Under",
                "confidence": "high",
                "score": 0.35,
                "prob": 0.70,
                "dg_lean": "Under",
                "book_lean": "Under",
            },
        }
    )
    pick = select_strongest_lean(pred)
    assert pick is not None
    assert pick["market_key"] == "goals_2_5"


def test_prefers_aligned_over_unknown():
    pred = _base_pred(
        markets={
            "cards_3_5": {
                "lean": "Over",
                "confidence": "high",
                "score": 0.5,
                "prob": 0.8,
                "dg_lean": None,
                "book_lean": None,
            },
            "goals_2_5": {
                "lean": "Over",
                "confidence": "high",
                "score": 0.3,
                "prob": 0.7,
                "dg_lean": "Over",
                "book_lean": "Over",
            },
        }
    )
    pick = select_strongest_lean(pred)
    assert pick is not None
    assert pick["market_key"] == "goals_2_5"
    assert pick["agreement_key"] == "aligned"


def test_match_1x2_can_win():
    pred = _base_pred(
        lean="Home",
        confidence="high",
        score=0.45,
        dg_sim_lean="Home",
        book_lean="Home",
        probs={"home": 0.74, "draw": 0.16, "away": 0.10},
        markets={
            "goals_2_5": {
                "lean": "Over",
                "confidence": "high",
                "score": 0.2,
                "prob": 0.66,
                "dg_lean": "Over",
                "book_lean": "Over",
            }
        },
    )
    pick = select_strongest_lean(pred)
    assert pick is not None
    assert pick["market_key"] == "match_1x2"
    assert pick["lean"] == "Home"


def test_omits_empty_markets():
    assert select_strongest_lean(_base_pred()) is None
    assert build_strongest_picks([_base_pred(), _base_pred(fixture_id=2)]) == []


def test_prefers_two_source_agreement_at_equal_prob():
    """Two-source agreement outranks one-source at equal probability."""
    one_source = _base_pred(
        fixture_id=1,
        markets={
            "corners_9_5": {
                "lean": "Over",
                "confidence": "high",
                "score": 0.35,
                "prob": 0.68,
                "dg_lean": "Over",
                "book_lean": None,
            }
        },
    )
    two_source = _base_pred(
        fixture_id=2,
        markets={
            "goals_2_5": {
                "lean": "Over",
                "confidence": "high",
                "score": 0.35,
                "prob": 0.68,
                "dg_lean": "Over",
                "book_lean": "Over",
            }
        },
    )
    picks = build_strongest_picks([one_source, two_source])
    assert picks[0]["fixture_id"] == 2
    assert picks[0]["agreement_n_sources"] == 2


def test_build_strongest_picks_ranks_by_strength():
    weak = _base_pred(
        fixture_id=1,
        markets={
            "corners_9_5": {
                "lean": "Over",
                "confidence": "high",
                "score": 0.3,
                "prob": 0.66,
                "dg_lean": None,
                "book_lean": None,
            }
        },
    )
    strong = _base_pred(
        fixture_id=2,
        home_name="Strong Home",
        markets={
            "goals_2_5": {
                "lean": "Over",
                "confidence": "high",
                "score": 0.5,
                "prob": 0.8,
                "dg_lean": "Over",
                "book_lean": "Over",
            }
        },
    )
    picks = build_strongest_picks([weak, strong])
    assert len(picks) == 2
    assert picks[0]["fixture_id"] == 2
    assert "_rank" not in picks[0]


def test_select_top_n_returns_multiple_markets():
    pred = _base_pred(
        lean="Home",
        confidence="high",
        score=0.45,
        dg_sim_lean="Home",
        book_lean="Home",
        probs={"home": 0.74, "draw": 0.16, "away": 0.10},
        markets={
            "goals_2_5": {
                "lean": "Over",
                "confidence": "high",
                "score": 0.2,
                "prob": 0.66,
                "dg_lean": "Over",
                "book_lean": "Over",
            },
            "btts": {
                "lean": "Yes",
                "confidence": "high",
                "score": 0.25,
                "prob": 0.67,
                "dg_lean": "Yes",
                "book_lean": "Yes",
            },
        },
    )
    top = select_top_n_candidates(pred, 3)
    assert len(top) == 3
    keys = {t["market_key"] for t in top}
    assert keys == {"match_1x2", "btts", "goals_2_5"}


def test_collect_gate_passing_candidates_count():
    pred = _base_pred(
        markets={
            "goals_2_5": {
                "lean": "Over",
                "confidence": "high",
                "score": 0.4,
                "prob": 0.72,
                "dg_lean": "Over",
                "book_lean": "Over",
            }
        }
    )
    assert len(collect_gate_passing_candidates(pred)) == 1


def test_heuristic_under_can_clear_gate():
    pred = _base_pred(
        markets={
            "corners_9_5": {
                "lean": "Under",
                "confidence": "high",
                "score": -0.6,
                "prob": 0.68,
                "dg_lean": "Under",
                "book_lean": "Under",
            }
        }
    )
    pick = select_strongest_lean(pred)
    assert pick is not None
    assert pick["market_key"] == "corners_9_5"
    assert pick["lean"] == "Under"
    assert pick["prob"] == 0.68


def test_low_auc_market_excluded_from_strongest(monkeypatch):
    monkeypatch.setattr(config, "STRONGEST_AUC_MIN_LABELS", 300)
    monkeypatch.setattr(config, "STRONGEST_AUC_MIN_WEEKS", 8)
    monkeypatch.setattr(config, "STRONGEST_MIN_AUC", 0.55)
    pred = _base_pred(
        markets={
            "corners_9_5": {
                "lean": "Over",
                "confidence": "high",
                "score": 0.4,
                "prob": 0.72,
                "dg_lean": "Over",
                "book_lean": "Over",
            }
        }
    )
    # Under-powered: low AUC must still pass (gate dormant).
    underpowered = {
        "corners_9_5": {
            "auc": 0.40,
            "auc_se": 0.02,
            "n_labels": 50,
            "n_weeks": 2,
        }
    }
    pick = select_strongest_lean(pred, market_aucs=underpowered)
    assert pick is not None
    assert pick["market_key"] == "corners_9_5"

    # Powered + tight SE below bar: excluded.
    powered_low = {
        "corners_9_5": {
            "auc": 0.40,
            "auc_se": 0.02,
            "n_labels": 400,
            "n_weeks": 10,
        }
    }
    assert select_strongest_lean(pred, market_aucs=powered_low) is None

    # Powered + upper bound clears bar: passes (0.52 + 1.64*0.05 = 0.602).
    powered_borderline = {
        "corners_9_5": {
            "auc": 0.52,
            "auc_se": 0.05,
            "n_labels": 400,
            "n_weeks": 10,
        }
    }
    pick = select_strongest_lean(pred, market_aucs=powered_borderline)
    assert pick is not None
    assert pick["market_key"] == "corners_9_5"

    # Market absent from the table: passes.
    pick = select_strongest_lean(pred, market_aucs={"goals_2_5": powered_low["corners_9_5"]})
    assert pick is not None
    assert pick["market_key"] == "corners_9_5"


def test_candidate_carries_prob_raw():
    pred = _base_pred(
        markets={
            "goals_2_5": {
                "lean": "Over",
                "confidence": "high",
                "score": 0.4,
                "prob": 0.66,
                "prob_raw": 0.91,
                "dg_lean": "Over",
                "book_lean": "Over",
            }
        }
    )
    pick = select_strongest_lean(pred)
    assert pick is not None
    assert pick["prob"] == 0.66
    assert pick["prob_raw"] == 0.91


def test_fh_over_0_5_requires_higher_min_prob(monkeypatch):
    """Average FH Over (~0.70) must fail; clear edges (~0.82) still pass."""
    monkeypatch.setattr(config, "STRONGEST_MIN_PROB", 0.65)
    monkeypatch.setattr(config, "STRONGEST_MIN_PROB_FH_OVER_0_5", 0.80)
    monkeypatch.setattr(
        config,
        "STRONGEST_MIN_PROB_BY_MARKET",
        {"fh_over_0_5": 0.80},
    )

    low = _base_pred(
        markets={
            "fh_over_0_5": {
                "lean": "Over",
                "confidence": "high",
                "score": 0.4,
                "prob": 0.70,
                "dg_lean": "Over",
                "book_lean": "Over",
            }
        }
    )
    assert select_strongest_lean(low) is None
    assert collect_gate_passing_candidates(low) == []

    high = _base_pred(
        markets={
            "fh_over_0_5": {
                "lean": "Over",
                "confidence": "high",
                "score": 0.4,
                "prob": 0.82,
                "dg_lean": "Over",
                "book_lean": "Over",
            }
        }
    )
    pick = select_strongest_lean(high)
    assert pick is not None
    assert pick["market_key"] == "fh_over_0_5"
    assert pick["prob"] == 0.82


def test_other_markets_keep_global_min_prob(monkeypatch):
    """goals_2_5 at 0.70 still clears the global 0.65 floor."""
    monkeypatch.setattr(config, "STRONGEST_MIN_PROB", 0.65)
    monkeypatch.setattr(
        config,
        "STRONGEST_MIN_PROB_BY_MARKET",
        {"fh_over_0_5": 0.80},
    )
    pred = _base_pred(
        markets={
            "goals_2_5": {
                "lean": "Over",
                "confidence": "high",
                "score": 0.4,
                "prob": 0.70,
                "dg_lean": "Over",
                "book_lean": "Over",
            }
        }
    )
    pick = select_strongest_lean(pred)
    assert pick is not None
    assert pick["market_key"] == "goals_2_5"
    assert pick["prob"] == 0.70


def test_diversify_day_picks_caps_over_family(monkeypatch):
    from dg.report.best_leans import diversify_day_picks

    monkeypatch.setattr(config, "STRONGEST_DIVERSIFY_ENABLED", True)
    monkeypatch.setattr(config, "STRONGEST_MAX_SAME_MARKET_SHARE", 0.5)
    monkeypatch.setattr(config, "STRONGEST_MAX_OVER_FAMILY_SHARE", 0.4)
    picks = [
        {"market_key": "goals_2_5", "lean": "Over", "fixture_id": i}
        for i in range(1, 6)
    ] + [
        {"market_key": "match_1x2", "lean": "Home", "fixture_id": 10},
        {"market_key": "match_1x2", "lean": "Away", "fixture_id": 11},
    ]
    kept = diversify_day_picks(picks)
    over_n = sum(1 for p in kept if p["market_key"] == "goals_2_5")
    assert over_n <= 2  # 0.4 * 7 → max 2
    assert any(p["market_key"] == "match_1x2" for p in kept)


def test_hit_rate_ranks_before_prob(monkeypatch):
    from dg.report.best_leans import _better_candidate

    monkeypatch.setattr(config, "STRONGEST_POISSON_PROB_EPSILON", 0.03)
    a = {
        "market_key": "btts",
        "prob": 0.70,
        "_rank": (2, 2, 1, 0.70, 0.3),
    }
    b = {
        "market_key": "goals_2_5",
        "prob": 0.78,
        "_rank": (2, 2, 1, 0.78, 0.3),
    }
    rates = {"btts": 0.62, "goals_2_5": 0.48}
    assert _better_candidate(a, b, market_hit_rates=rates) is True
