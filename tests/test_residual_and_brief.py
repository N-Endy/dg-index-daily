"""Tests for residual logistic heads and Luna match brief helpers."""
from __future__ import annotations

from dg.ai.match_brief import match_script_from_sim, similar_case_summary
from dg.ai.vet_strongest import fixture_group_payload
from dg.model.residual import (
    _fit_logistic,
    _predict_proba,
    apply_residual_1x2,
    build_feature_vector,
)
from dg.storage.db import connect, init_db
from dg.storage.migrations import migrate


def test_logistic_separates_signal():
    # Separable 2-feature points so gradient has signal
    X = [[0.0, 0.0], [0.0, 0.1], [1.0, 0.9], [1.0, 1.0]] * 5
    y = [0, 0, 1, 1] * 5
    w, b = _fit_logistic(X, y, lr=0.3, max_iter=1000, l2=0.0001)
    p_low = _predict_proba(w, b, [0.0, 0.0])
    p_high = _predict_proba(w, b, [1.0, 1.0])
    assert p_high > p_low
    assert p_high - p_low > 0.15


def test_build_feature_vector_accuracy_vs_value():
    matchup = {"rating_gap": 0.5, "pace_clash": 110.0, "nec_sum": 120.0}
    sim = {
        "percents": {"home_win_pct": 50, "draw_pct": 25, "away_win_pct": 25},
        "projected_xg": {"xgot": {"total": "2.0"}},
        "shots_on_target": {"total": "10"},
    }
    book = {"home_win": 2.0, "draw": 3.5, "away_win": 3.5}
    names_a, xa = build_feature_vector(
        matchup=matchup, sim=sim, book=book, head="accuracy"
    )
    names_v, xv = build_feature_vector(
        matchup=matchup, sim=sim, book=book, head="value"
    )
    assert "book_home" not in names_a
    assert "book_home" in names_v
    assert len(xv) == len(names_v)
    assert len(xa) == len(names_a)


def test_apply_residual_noop_without_model(tmp_path):
    migrate(tmp_path / "r.db")
    conn = connect(tmp_path / "r.db")
    init_db(conn)
    blended = {"home": 0.4, "draw": 0.3, "away": 0.3}
    out = apply_residual_1x2(
        conn, blended, matchup={}, sim={}, book={}, head="accuracy"
    )
    assert out == blended
    conn.close()


def test_match_script_fields():
    ctx = {
        "fields": {
            "top_scores": [{"score": "1-1", "probability_pct": 12.0}],
            "correct_score_model": "poisson_dixon_coles",
            "xgot_total": 1.8,
            "sot_total": 9.5,
            "shot_accuracy_total": 36.0,
            "congestion_home": False,
            "congestion_away": True,
            "over_2_5_pct": 55.0,
        },
        "meta": {},
        "congestion_away": True,
    }
    script = match_script_from_sim(ctx)
    assert script["xgotTotal"] == 1.8
    assert script["sotTotal"] == 9.5
    assert script["congestion"]["away"] is True
    assert script["topScores"][0]["score"] == "1-1"


def test_fixture_group_payload_includes_script():
    group = {
        "fixture_id": 1,
        "home_name": "A",
        "away_name": "B",
        "league": "PL",
        "matchScript": {"xgotTotal": 1.5},
        "candidates": [
            {
                "fixture_id": 1,
                "market_key": "goals_2_5",
                "lean": "Over",
                "prob": 0.7,
                "why": ["pace"],
                "similarCases": {"record": "7-5"},
            }
        ],
    }
    payload = fixture_group_payload(group)
    assert payload["matchScript"]["xgotTotal"] == 1.5
    assert payload["candidates"][0]["similarCases"]["record"] == "7-5"


def test_similar_case_summary_runs(tmp_path):
    migrate(tmp_path / "s.db")
    conn = connect(tmp_path / "s.db")
    init_db(conn)
    summary = similar_case_summary(
        conn, market_key="goals_2_5", lean="Over", agreement_key="aligned"
    )
    assert summary["marketKey"] == "goals_2_5"
    assert "record" in summary
    conn.close()
