"""Tests for Poisson goals layer and DG Rating strength fields."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dg.ingest.ratings import extract_strength_fields, ingest_ratings
from dg.model.goals import derive_probabilities, expected_goals, predict_goals, score_matrix
from dg.model.markets import predict_markets
from dg.storage.db import connect, init_db
from dg.storage.migrations import backfill_strength_from_raw, migrate
from dg.web.plain_language import probability_plain, strength_gap_plain

FIXTURES = Path(__file__).parent / "fixtures"


def test_poisson_probabilities_sum_to_one():
    probs = derive_probabilities(1.4, 1.1)
    s = probs["home"] + probs["draw"] + probs["away"]
    assert abs(s - 1.0) < 0.02
    assert abs(probs["over_2_5"] + probs["under_2_5"] - 1.0) < 1e-6
    assert abs(probs["over_3_5"] + probs["under_3_5"] - 1.0) < 1e-6
    assert abs(probs["btts_yes"] + probs["btts_no"] - 1.0) < 1e-6
    # Over 3.5 is a subset of Over 2.5
    assert probs["over_3_5"] <= probs["over_2_5"] + 1e-9


def test_higher_lambda_raises_over_25():
    low = derive_probabilities(0.8, 0.7)
    high = derive_probabilities(1.8, 1.6)
    assert high["over_2_5"] > low["over_2_5"]
    assert high["over_3_5"] > low["over_3_5"]
    assert high["lam_home"] > low["lam_home"]


def test_expected_goals_monotonic_in_attack():
    weak = {"home_ortg": 1.0, "home_drtg": 1.4, "away_ortg": 1.2, "away_drtg": 1.3, "home_coef": 1.0, "away_coef": 1.0}
    strong = {**weak, "home_ortg": 2.0}
    lh1, _ = expected_goals(weak, league_avg=1.35)
    lh2, _ = expected_goals(strong, league_avg=1.35)
    assert lh2 > lh1


def test_score_matrix_shape():
    m = score_matrix(1.2, 1.0, max_goals=5)
    assert len(m) == 6
    assert abs(sum(sum(row) for row in m) - 1.0) < 1e-6


def test_predict_goals_keys():
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
    for k in ("home", "draw", "away", "over_2_5", "over_3_5", "btts_yes", "lam_home", "lam_away"):
        assert k in out


def test_markets_attach_poisson_prob():
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
    gp = predict_goals(
        {
            "home_ortg": 1.6,
            "home_drtg": 1.0,
            "away_ortg": 1.1,
            "away_drtg": 1.4,
            "home_coef": 1.0,
            "away_coef": 1.0,
        }
    )
    markets = predict_markets(matchup, goal_probs=gp)
    assert markets["goals_2_5"].get("prob") is not None
    assert 0.05 < markets["goals_2_5"]["prob"] < 0.95
    assert markets["goals_3_5"].get("prob") is not None
    assert markets["goals_3_5"]["lean"] in ("Over", "Under")
    assert markets["btts"].get("prob") is not None


def test_extract_strength_fields():
    payload = {"DGRtg": 2.1, "ORtg": 1.5, "DRtg": 1.0, "home_rating": 0.5, "away_rating": -0.2, "consistency": 0.6, "coef_adj": 1.0}
    f = extract_strength_fields(payload)
    assert f["dgrtg"] == 2.1
    assert f["ortg"] == 1.5
    assert f["drtg"] == 1.0


def test_backfill_strength_from_raw(tmp_path):
    db_path = tmp_path / "bf.db"
    conn = connect(db_path)
    # Create schema without going through full init backfill first — use init_db
    init_db(conn)
    # Insert a minimal snapshot + rating with only raw_json strength
    conn.execute(
        "INSERT INTO dg_snapshot (generated_at, scraped_at, payload_sha256, n_teams) VALUES (?,?,?,?)",
        ("2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00", "x", 1),
    )
    payload = {
        "team_id": 1,
        "team": "Test FC",
        "DGRtg": 1.9,
        "ORtg": 1.4,
        "DRtg": 1.2,
        "home_rating": 0.3,
        "away_rating": -0.1,
        "consistency": 0.55,
        "coef_adj": 0.9,
        "ppda_index": 50,
        "pace_index": 50,
        "agix_index": 50,
        "nec_index": 50,
        "control_index": 50,
    }
    # Clear dgrtg if present by inserting with nulls then backfill
    conn.execute(
        """
        INSERT INTO dg_team_rating (snapshot_id, team_id, team, raw_json, ppda_index, pace_index, agix_index, nec_index, control_index)
        VALUES (1, 1, 'Test FC', ?, 50, 50, 50, 50, 50)
        """,
        (json.dumps(payload),),
    )
    conn.execute("UPDATE dg_team_rating SET dgrtg = NULL WHERE team_id = 1")
    conn.commit()
    n = backfill_strength_from_raw(conn)
    conn.commit()
    assert n >= 1
    row = conn.execute("SELECT dgrtg, ortg, drtg FROM dg_team_rating WHERE team_id = 1").fetchone()
    assert row["dgrtg"] == pytest.approx(1.9)
    assert row["ortg"] == pytest.approx(1.4)
    conn.close()


def test_ingest_stores_strength(tmp_path, sample_meta=None):
    ratings = json.loads((FIXTURES / "dg_ratings_sample.json").read_text())
    # Ensure sample has strength keys or inject them
    for t in ratings:
        t.setdefault("DGRtg", 1.5)
        t.setdefault("ORtg", 1.2)
        t.setdefault("DRtg", 1.1)
        t.setdefault("home_rating", 0.2)
        t.setdefault("away_rating", -0.1)
        t.setdefault("consistency", 0.5)
        t.setdefault("coef_adj", 1.0)
    meta = json.loads((FIXTURES / "dg_meta_sample.json").read_text())
    conn = connect(tmp_path / "ing.db")
    init_db(conn)
    sid, inserted = ingest_ratings(conn, ratings, generated_at=meta["generated_at"], payload_sha256="t", meta=meta)
    conn.commit()
    assert inserted
    row = conn.execute("SELECT dgrtg, ortg FROM dg_team_rating WHERE snapshot_id = ? LIMIT 1", (sid,)).fetchone()
    assert row["dgrtg"] is not None
    assert row["ortg"] is not None
    conn.close()


def test_plain_language_helpers():
    assert probability_plain(0.58) == "58%"
    assert probability_plain(None) == ""
    s = strength_gap_plain("Arsenal", "Brighton", 2.37, 2.00, 0.37)
    assert s is not None
    assert "Arsenal" in s and "2.37" in s
    assert "edge" in s or "matched" in s


def test_sim_xg_blend_pulls_lambda_toward_match_projection():
    base = {
        "home_ortg": 1.2,
        "home_drtg": 1.2,
        "away_ortg": 1.2,
        "away_drtg": 1.2,
        "home_coef": 1.0,
        "away_coef": 1.0,
    }
    lh0, la0 = expected_goals(base, league_avg=1.35, cfg={
        "home_advantage": 1.0,
        "xg_blend_weight": 0.0,
        "sim_xg_blend_weight": 0.0,
        "min_lambda": 0.15,
        "max_lambda": 4.5,
        "default_league_avg": 1.35,
    })
    blended = {
        **base,
        "sim_xg_home": 2.4,
        "sim_xg_away": 0.6,
    }
    lh1, la1 = expected_goals(blended, league_avg=1.35, cfg={
        "home_advantage": 1.0,
        "xg_blend_weight": 0.0,
        "sim_xg_blend_weight": 0.5,
        "min_lambda": 0.15,
        "max_lambda": 4.5,
        "default_league_avg": 1.35,
    })
    assert lh1 > lh0
    assert la1 < la0
