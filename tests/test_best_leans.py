"""Unit tests for strongest-lean selection."""
from __future__ import annotations

from dg.report.best_leans import (
    MIN_PROB,
    build_strongest_picks,
    select_strongest_lean,
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
                "confidence": "medium",
                "score": 0.5,
                "prob": 0.8,
                "dg_lean": "Over",
                "book_lean": "Over",
            }
        }
    )
    assert select_strongest_lean(pred) is None


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


def test_prefers_poisson_over_corners_when_both_qualify():
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
