"""Tests for AI Picks vetting (mocked LLM)."""
from __future__ import annotations

import json

from dg.ai.vet_strongest import (
    gate_screen_scores,
    parse_screen_response,
    replace_ai_picks_for_day,
    vet_strongest_for_day,
)
from dg.storage.db import connect, init_db


def test_parse_screen_response_resilient_keys():
    raw = json.dumps(
        {
            "Scores": [
                {
                    "fixture_id": 10,
                    "market_key": "goals_2_5",
                    "rating": 88,
                    "Approve": True,
                    "Reason": "Aligned high-prob over.",
                }
            ]
        }
    )
    rows = parse_screen_response(raw)
    assert len(rows) == 1
    assert rows[0]["fixture_id"] == 10
    assert rows[0]["market_key"] == "goals_2_5"
    assert rows[0]["score"] == 88
    assert rows[0]["approve"] is True


def test_gate_requires_approve_and_min_score():
    cands = [
        {
            "fixture_id": 1,
            "market_key": "match_1x2",
            "league": "EPL",
            "home_name": "A",
            "away_name": "B",
            "date_utc": "2026-08-30T15:00:00+00:00",
            "lean": "Home",
        },
        {
            "fixture_id": 2,
            "market_key": "btts",
            "league": "EPL",
            "home_name": "C",
            "away_name": "D",
            "date_utc": "2026-08-30T16:00:00+00:00",
            "lean": "Yes",
        },
        {
            "fixture_id": 3,
            "market_key": "goals_2_5",
            "league": "EPL",
            "home_name": "E",
            "away_name": "F",
            "date_utc": "2026-08-30T17:00:00+00:00",
            "lean": "Over",
        },
    ]
    scores = [
        {"fixture_id": 1, "market_key": "match_1x2", "score": 90, "approve": True, "reason": "ok"},
        {"fixture_id": 2, "market_key": "btts", "score": 95, "approve": False, "reason": "no"},
        {"fixture_id": 3, "market_key": "goals_2_5", "score": 60, "approve": True, "reason": "low"},
        {"fixture_id": 99, "market_key": "match_1x2", "score": 99, "approve": True, "reason": "unknown"},
    ]
    approved = gate_screen_scores(cands, scores, min_score=70)
    assert len(approved) == 1
    assert approved[0]["fixture_id"] == 1
    assert approved[0]["ai_score"] == 90


def test_gate_one_per_fixture_keeps_higher_score():
    cands = [
        {
            "fixture_id": 1,
            "market_key": "match_1x2",
            "league": "A",
            "date_utc": "2026-08-30T12:00:00+00:00",
        },
        {
            "fixture_id": 1,
            "market_key": "goals_2_5",
            "league": "A",
            "date_utc": "2026-08-30T12:00:00+00:00",
        },
    ]
    scores = [
        {"fixture_id": 1, "market_key": "match_1x2", "score": 72, "approve": True, "reason": "a"},
        {"fixture_id": 1, "market_key": "goals_2_5", "score": 91, "approve": True, "reason": "b"},
    ]
    approved = gate_screen_scores(cands, scores, min_score=70)
    assert len(approved) == 1
    assert approved[0]["market_key"] == "goals_2_5"
    assert approved[0]["ai_score"] == 91


def test_replace_and_load_ai_picks(tmp_path):
    from dg.ai.vet_strongest import load_ai_picks

    conn = init_db(connect(tmp_path / "ai.db"))
    approved = [
        {
            "fixture_id": 42,
            "market_key": "goals_2_5",
            "lean": "Over",
            "league": "Championship",
            "home_name": "Derby",
            "away_name": "Swansea",
            "ai_score": 81,
            "ai_reason": "Strong over signal.",
        }
    ]
    n = replace_ai_picks_for_day(conn, "2026-08-30", approved, model="test-model")
    assert n == 1
    loaded = load_ai_picks(conn, "2026-08-30")
    assert len(loaded) == 1
    assert loaded[0]["ai_score"] == 81
    assert "Strong over" in loaded[0]["ai_reason"]
    conn.close()


def test_vet_skips_without_key(tmp_path, monkeypatch):
    from dg import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dg.db")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    config.ensure_dirs()
    conn = init_db(connect(config.DB_PATH))
    summary = vet_strongest_for_day(conn, day="2026-08-30")
    assert summary["skipped_no_key"] is True
    assert summary["written"] == 0
    conn.close()


def test_vet_with_injected_chat(tmp_path, monkeypatch):
    from dg import config
    from dg.report import best_leans

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dg.db")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "AI_VET_MIN_SCORE", 70)
    config.ensure_dirs()

    fake_pick = {
        "fixture_id": 7,
        "market_key": "match_1x2",
        "market_label": "Match winner",
        "lean": "Home",
        "lean_plain": "Favours Home",
        "confidence": "high",
        "prob": 0.7,
        "agreement_key": "aligned",
        "agreement_label": "Aligned",
        "league": "Premier League",
        "home_name": "Home FC",
        "away_name": "Away FC",
        "date_utc": "2026-08-30T15:00:00+00:00",
        "kickoff_display": "Sun 30 Aug · 16:00 WAT",
        "why": ["rating gap"],
    }

    monkeypatch.setattr(
        best_leans,
        "load_strongest_day",
        lambda date=None: {"day": date or "2026-08-30", "picks": [fake_pick], "empty": False},
    )

    def fake_chat(system, user):
        assert "conservative" in system.lower() or "Score" in system or "JSON" in system
        return json.dumps(
            {
                "scores": [
                    {
                        "fixtureId": 7,
                        "marketKey": "match_1x2",
                        "score": 85,
                        "approve": True,
                        "reason": "Aligned home lean.",
                    }
                ]
            }
        )

    conn = init_db(connect(config.DB_PATH))
    # Patch load_strongest_day where vet imports it
    import dg.ai.vet_strongest as vs

    monkeypatch.setattr(
        vs,
        "load_strongest_day",
        lambda date=None: {"day": date or "2026-08-30", "picks": [fake_pick], "empty": False},
    )
    summary = vet_strongest_for_day(conn, day="2026-08-30", chat_fn=fake_chat)
    assert summary["n_candidates"] == 1
    assert summary["written"] == 1
    assert summary["n_approved"] == 1
    from dg.ai.vet_strongest import load_ai_picks

    picks = load_ai_picks(conn, "2026-08-30")
    assert picks[0]["ai_score"] == 85
    conn.close()
