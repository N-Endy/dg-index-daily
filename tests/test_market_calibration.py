"""Tests for market calibration buckets, shrinkage, and agreement tiers."""
from __future__ import annotations

from dg.report.market_reliability import (
    agreement_tier_from_candidate,
    agreement_tier_from_market,
    finalize_calibration_rows,
    load_market_calibration,
    prob_band_for,
    reliability_for,
    store_market_calibration,
)
from dg.storage.db import connect, init_db


def test_prob_band_for():
    assert prob_band_for(0.70) == "lt_75"
    assert prob_band_for(0.80) == "75_85"
    assert prob_band_for(0.90) == "85_92"
    assert prob_band_for(0.93) == "92_plus"
    assert prob_band_for(None) is None


def test_agreement_tier_from_market():
    assert (
        agreement_tier_from_market(
            {"lean": "Over", "dg_lean": "Over", "book_lean": "Over"}
        )
        == "agree2"
    )
    assert (
        agreement_tier_from_market({"lean": "Over", "dg_lean": "Over", "book_lean": None})
        == "agree1"
    )
    assert (
        agreement_tier_from_market(
            {"lean": "Over", "dg_lean": "Over", "book_lean": "Under"}
        )
        == "split"
    )
    assert agreement_tier_from_market({"lean": "Over"}) == "none"


def test_agreement_tier_from_candidate():
    assert (
        agreement_tier_from_candidate(
            {"agreement_key": "aligned", "agreement_n_sources": 2}
        )
        == "agree2"
    )
    assert (
        agreement_tier_from_candidate(
            {"agreement_key": "aligned", "agreement_n_sources": 1}
        )
        == "agree1"
    )
    assert (
        agreement_tier_from_candidate({"agreement_key": "split", "agreement_n_sources": 2})
        == "split"
    )
    assert (
        agreement_tier_from_candidate(
            {
                "agreement_key": "unknown",
                "lean": "Over",
                "dg_lean": "Over",
                "book_lean": None,
            }
        )
        == "agree1"
    )


def test_finalize_calibration_includes_tier_parents():
    raw = {
        ("goals_2_5", "agree2", "92_plus"): [240, 400],
        ("goals_2_5", "agree2", "85_92"): [38, 58],
        ("goals_2_5", "agree2", "no_prob"): [148, 227],
        ("goals_2_5", "split", "92_plus"): [26, 42],
        ("btts", "agree2", "92_plus"): [100, 140],
    }
    rows = finalize_calibration_rows(raw)
    keys = {(r["market_key"], r["agreement_tier"], r["prob_band"]) for r in rows}
    assert ("goals_2_5", "agree2", "92_plus") in keys
    assert ("goals_2_5", "agree2", "no_prob") in keys
    assert ("goals_2_5", "agree2", "all") in keys
    assert ("all", "agree2", "all") in keys
    assert ("all", "all", "all") in keys
    mt = next(
        r
        for r in rows
        if r["market_key"] == "goals_2_5"
        and r["agreement_tier"] == "agree2"
        and r["prob_band"] == "all"
    )
    # 400+58+227
    assert mt["n_graded"] == 685
    assert mt["hits"] == 240 + 38 + 148


def test_store_and_load_no_running_max(tmp_path):
    conn = init_db(connect(tmp_path / "cal.db"))
    summary = {
        "calibration": finalize_calibration_rows(
            {
                ("goals_2_5", "agree2", "75_85"): [60, 84],
                ("goals_2_5", "agree2", "85_92"): [48, 72],
                ("goals_2_5", "agree2", "92_plus"): [264, 426],
            }
        )
    }
    n = store_market_calibration(conn, summary)
    assert n >= 3
    loaded = load_market_calibration(conn)
    # No running-max: rates stay empirical (declining)
    assert abs(loaded[("goals_2_5", "agree2", "75_85")]["hit_rate"] - 60 / 84) < 1e-9
    assert abs(loaded[("goals_2_5", "agree2", "85_92")]["hit_rate"] - 48 / 72) < 1e-9
    assert abs(loaded[("goals_2_5", "agree2", "92_plus")]["hit_rate"] - 264 / 426) < 1e-9
    conn.close()


def test_shrinkage_thin_bucket_blends_to_parent(tmp_path, monkeypatch):
    from dg import config

    monkeypatch.setattr(config, "MARKET_CALIBRATION_SHRINKAGE", 50)
    monkeypatch.setattr(config, "MARKET_CALIBRATION_DEFAULT_RATE", 0.50)

    conn = init_db(connect(tmp_path / "cal2.db"))
    # Thin 85_92 (n=58, raw 0.655) should shrink toward market+tier parent, not cliff.
    store_market_calibration(
        conn,
        {
            "calibration": [
                {
                    "market_key": "goals_2_5",
                    "agreement_tier": "agree2",
                    "prob_band": "85_92",
                    "n_graded": 58,
                    "hits": 38,
                    "hit_rate": 38 / 58,
                },
                {
                    "market_key": "goals_2_5",
                    "agreement_tier": "agree2",
                    "prob_band": "92_plus",
                    "n_graded": 400,
                    "hits": 248,
                    "hit_rate": 0.62,
                },
                {
                    "market_key": "goals_2_5",
                    "agreement_tier": "agree2",
                    "prob_band": "all",
                    "n_graded": 458,
                    "hits": 286,
                    "hit_rate": 286 / 458,
                },
                {
                    "market_key": "all",
                    "agreement_tier": "agree2",
                    "prob_band": "all",
                    "n_graded": 2000,
                    "hits": 1280,
                    "hit_rate": 0.64,
                },
                {
                    "market_key": "all",
                    "agreement_tier": "all",
                    "prob_band": "all",
                    "n_graded": 4000,
                    "hits": 2240,
                    "hit_rate": 0.56,
                },
            ]
        },
    )
    calib = load_market_calibration(conn)
    r88 = reliability_for(calib, "goals_2_5", "agree2", 0.88)
    r93 = reliability_for(calib, "goals_2_5", "agree2", 0.93)
    # No cliff: gap should be small (< 0.05), not the old 0.10+ drop.
    assert abs(r88["rate"] - r93["rate"]) < 0.05
    assert r88["source"] == "market_tier_band"
    # Thin bucket rate is between raw and parent
    parent = 286 / 458
    raw = 38 / 58
    assert min(raw, parent) - 0.01 <= r88["rate"] <= max(raw, parent) + 0.01
    conn.close()


def test_shrinkage_n0_returns_parent(monkeypatch):
    from dg import config

    monkeypatch.setattr(config, "MARKET_CALIBRATION_SHRINKAGE", 50)
    monkeypatch.setattr(config, "MARKET_CALIBRATION_DEFAULT_RATE", 0.50)

    calib = {
        ("all", "all", "all"): {"hit_rate": 0.56, "n_graded": 1000, "hits": 560},
        ("all", "agree2", "all"): {"hit_rate": 0.64, "n_graded": 500, "hits": 320},
        ("goals_2_5", "agree2", "all"): {"hit_rate": 0.62, "n_graded": 200, "hits": 124},
    }
    # No band leaf — stops at market_tier after shrinking empty band (n=0)
    r = reliability_for(calib, "goals_2_5", "agree2", 0.93)
    assert r["source"] == "market_tier"
    # Empty band shrink leaves rate unchanged from market_tier shrink result
    assert abs(r["rate"] - 0.62) < 0.05


def test_no_prob_never_matched_directly(tmp_path, monkeypatch):
    from dg import config

    monkeypatch.setattr(config, "MARKET_CALIBRATION_SHRINKAGE", 50)
    conn = init_db(connect(tmp_path / "cal3.db"))
    store_market_calibration(
        conn,
        {
            "calibration": [
                {
                    "market_key": "goals_2_5",
                    "agreement_tier": "agree2",
                    "prob_band": "no_prob",
                    "n_graded": 227,
                    "hits": 148,
                    "hit_rate": 148 / 227,
                },
                {
                    "market_key": "goals_2_5",
                    "agreement_tier": "agree2",
                    "prob_band": "all",
                    "n_graded": 227,
                    "hits": 148,
                    "hit_rate": 148 / 227,
                },
                {
                    "market_key": "all",
                    "agreement_tier": "agree2",
                    "prob_band": "all",
                    "n_graded": 227,
                    "hits": 148,
                    "hit_rate": 148 / 227,
                },
                {
                    "market_key": "all",
                    "agreement_tier": "all",
                    "prob_band": "all",
                    "n_graded": 227,
                    "hits": 148,
                    "hit_rate": 148 / 227,
                },
            ]
        },
    )
    calib = load_market_calibration(conn)
    r = reliability_for(calib, "goals_2_5", "agree2", None)
    assert r["source"] == "market_tier"
    assert r["prob_band"] == "all"
    conn.close()


def test_agree2_outranks_agree1_same_market_prob(tmp_path, monkeypatch):
    """Vancouver inversion: single-source must score below two-source at equal market/prob."""
    from dg import config
    from dg.ai.vet_strongest import compute_publish_score

    monkeypatch.setattr(config, "MARKET_CALIBRATION_SHRINKAGE", 50)
    conn = init_db(connect(tmp_path / "cal4.db"))
    store_market_calibration(
        conn,
        {
            "calibration": [
                {
                    "market_key": "goals_2_5",
                    "agreement_tier": "agree2",
                    "prob_band": "92_plus",
                    "n_graded": 384,
                    "hits": 238,
                    "hit_rate": 0.620,
                },
                {
                    "market_key": "goals_2_5",
                    "agreement_tier": "agree2",
                    "prob_band": "all",
                    "n_graded": 700,
                    "hits": 448,
                    "hit_rate": 0.640,
                },
                {
                    "market_key": "goals_2_5",
                    "agreement_tier": "agree1",
                    "prob_band": "92_plus",
                    "n_graded": 100,
                    "hits": 48,
                    "hit_rate": 0.480,
                },
                {
                    "market_key": "goals_2_5",
                    "agreement_tier": "agree1",
                    "prob_band": "all",
                    "n_graded": 150,
                    "hits": 72,
                    "hit_rate": 0.480,
                },
                {
                    "market_key": "all",
                    "agreement_tier": "agree2",
                    "prob_band": "all",
                    "n_graded": 2000,
                    "hits": 1280,
                    "hit_rate": 0.640,
                },
                {
                    "market_key": "all",
                    "agreement_tier": "agree1",
                    "prob_band": "all",
                    "n_graded": 400,
                    "hits": 200,
                    "hit_rate": 0.500,
                },
                {
                    "market_key": "all",
                    "agreement_tier": "all",
                    "prob_band": "all",
                    "n_graded": 3000,
                    "hits": 1680,
                    "hit_rate": 0.560,
                },
            ]
        },
    )
    calib = load_market_calibration(conn)
    r2 = reliability_for(calib, "goals_2_5", "agree2", 0.93)
    r1 = reliability_for(calib, "goals_2_5", "agree1", 0.93)
    s2 = compute_publish_score(base_rate=r2["rate"], coherence=3, concerns=[])
    s1 = compute_publish_score(
        base_rate=r1["rate"], coherence=3, concerns=["dg only"]
    )
    assert s2 > s1
    assert r2["rate"] > r1["rate"]
    conn.close()
