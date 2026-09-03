"""Tests for AI Picks vetting (mocked LLM)."""
from __future__ import annotations

import json

from dg.ai.vet_strongest import (
    compute_publish_score,
    fixture_group_payload,
    gate_screen_scores,
    parse_screen_response,
    replace_ai_picks_for_day,
    vet_strongest_for_day,
)
from dg.storage.db import connect, init_db


def _vet_prediction(fixture_id: int) -> dict:
    return {
        "fixture_id": fixture_id,
        "home_name": f"H{fixture_id}",
        "away_name": f"A{fixture_id}",
        "lean": "Home",
        "confidence": "high",
        "score": 0.4,
        "dg_sim_lean": "Home",
        "book_lean": "Home",
        "probs": {"home": 0.7, "draw": 0.2, "away": 0.1},
        "markets": {},
        "date_utc": "2026-08-30T15:00:00+00:00",
        "kickoff_display": "Sun 30 Aug · 16:00 WAT",
    }


def _vet_ctx(n: int = 4) -> dict:
    preds = [_vet_prediction(i) for i in range(1, n + 1)]
    return {"day": "2026-08-30", "predictions": preds, "picks": [], "empty": False}


def test_chat_json_sends_max_completion_tokens(monkeypatch):
    from dg import config
    from dg.ai import openai_client as oc

    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(config, "OPENAI_REASONING_EFFORT", "low")
    captured = {}

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": '{"ok":true}'}}]}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            return _Resp()

    monkeypatch.setattr(oc.httpx, "Client", _Client)
    out = oc.chat_json(system="s", user="u", max_tokens=1500)
    assert out == '{"ok":true}'
    body = captured["json"]
    assert "max_completion_tokens" in body
    assert body["max_completion_tokens"] == 1500
    assert "max_tokens" not in body
    assert body.get("reasoning_effort") == "low"


def test_extract_message_content_from_parts():
    from dg.ai.openai_client import _extract_message_content

    assert (
        _extract_message_content(
            {"content": [{"type": "output_text", "text": '{"scores":[]}'}]}
        )
        == '{"scores":[]}'
    )


def test_compute_publish_score_not_echo_of_prob():
    # Fully agreed, coherent, Poisson-backed at 96% must not land near 96.
    score = compute_publish_score(
        agreement=3,
        coherence=3,
        market_trust=3,
        concerns=[],
        prob=0.96,
    )
    assert score == 100
    assert score != 96

    thin = compute_publish_score(
        agreement=1,
        coherence=1,
        market_trust=2,
        concerns=["single source only"],
        prob=0.96,
    )
    assert thin < 70
    assert abs(thin - 96) > 20


def test_compute_publish_score_differs_at_same_prob():
    strong = compute_publish_score(
        agreement=3, coherence=3, market_trust=3, concerns=[], prob=0.80
    )
    weak = compute_publish_score(
        agreement=1, coherence=1, market_trust=1, concerns=[], prob=0.80
    )
    assert strong > weak
    assert strong - weak >= 20


def test_parse_component_response():
    raw = json.dumps(
        {
            "picks": [
                {
                    "fixtureId": 10,
                    "marketKey": "goals_2_5",
                    "verdict": "publish",
                    "agreement": 3,
                    "coherence": 2,
                    "marketTrust": 3,
                    "concerns": ["thin book"],
                    "reason": "Aligned over with solid drivers.",
                }
            ]
        }
    )
    rows = parse_screen_response(raw)
    assert len(rows) == 1
    assert rows[0]["fixture_id"] == 10
    assert rows[0]["market_key"] == "goals_2_5"
    assert rows[0]["approve"] is True
    assert rows[0]["score_source"] == "components"
    assert rows[0]["components"]["agreement"] == 3
    assert rows[0]["components"]["coherence"] == 2
    assert rows[0]["components"]["market_trust"] == 3
    assert "thin book" in rows[0]["concerns"]


def test_fixture_group_payload_shuffles_deterministically():
    group = {
        "fixture_id": 42,
        "home_name": "A",
        "away_name": "B",
        "league": "EPL",
        "date_utc": "2026-08-30T15:00:00+00:00",
        "candidates": [
            {"fixture_id": 42, "market_key": "match_1x2", "lean": "Home", "prob": 0.7},
            {"fixture_id": 42, "market_key": "goals_2_5", "lean": "Over", "prob": 0.8},
            {"fixture_id": 42, "market_key": "btts", "lean": "Yes", "prob": 0.75},
        ],
    }
    a = fixture_group_payload(group)
    b = fixture_group_payload(group)
    keys_a = [c["marketKey"] for c in a["candidates"]]
    keys_b = [c["marketKey"] for c in b["candidates"]]
    assert keys_a == keys_b
    assert set(keys_a) == {"match_1x2", "goals_2_5", "btts"}
    assert "minScoreHint" not in a
    assert "prob" in a["candidates"][0]


def test_vet_batches_candidates(tmp_path, monkeypatch):
    from dg import config
    import dg.ai.vet_strongest as vs

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dg.db")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "AI_VET_BATCH_SIZE", 2)
    monkeypatch.setattr(config, "AI_VET_MIN_SCORE", 70)
    config.ensure_dirs()

    monkeypatch.setattr(
        vs,
        "load_strongest_day",
        lambda date=None: _vet_ctx(4),
    )
    calls = {"n": 0}

    def fake_chat(system, user):
        calls["n"] += 1
        assert "minScoreHint" not in user
        assert "Do not return a 0-100 score" in system or "component" in system.lower()
        fids = [1, 2] if calls["n"] == 1 else [3, 4]
        return json.dumps(
            {
                "picks": [
                    {
                        "fixtureId": fid,
                        "marketKey": "match_1x2",
                        "verdict": "publish",
                        "agreement": 3,
                        "coherence": 3,
                        "marketTrust": 2,
                        "concerns": [],
                        "reason": "ok",
                    }
                    for fid in fids
                ]
            }
        )

    conn = init_db(connect(config.DB_PATH))
    summary = vs.vet_strongest_for_day(conn, day="2026-08-30", chat_fn=fake_chat)
    assert calls["n"] == 2
    assert summary["n_batches"] == 2
    assert summary["written"] == 4
    assert summary["n_flat_score_fallback"] == 0
    assert "echo_rate" in summary
    conn.close()


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
    assert rows[0]["score_source"] == "flat"


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


def test_gate_components_recompute_with_prob():
    cands = [
        {
            "fixture_id": 1,
            "market_key": "goals_2_5",
            "league": "EPL",
            "prob": 0.96,
            "date_utc": "2026-08-30T15:00:00+00:00",
        }
    ]
    scores = parse_screen_response(
        json.dumps(
            {
                "picks": [
                    {
                        "fixtureId": 1,
                        "marketKey": "goals_2_5",
                        "verdict": "publish",
                        "agreement": 3,
                        "coherence": 3,
                        "marketTrust": 3,
                        "concerns": [],
                        "reason": "full stack",
                    }
                ]
            }
        )
    )
    approved = gate_screen_scores(cands, scores, min_score=70)
    assert len(approved) == 1
    assert approved[0]["ai_score"] == 100
    assert approved[0]["ai_score_source"] == "components"
    assert approved[0]["ai_score"] != 96
    # Thin single-source at same prob must not clear the floor.
    thin_scores = parse_screen_response(
        json.dumps(
            {
                "picks": [
                    {
                        "fixtureId": 1,
                        "marketKey": "goals_2_5",
                        "verdict": "publish",
                        "agreement": 1,
                        "coherence": 1,
                        "marketTrust": 2,
                        "concerns": ["dg only"],
                        "reason": "thin",
                    }
                ]
            }
        )
    )
    thin = gate_screen_scores(cands, thin_scores, min_score=70)
    assert thin == []


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


def _seed_fixture(conn, fixture_id: int, *, league_id: int = 39) -> None:
    now = "2026-08-30T12:00:00+00:00"
    conn.execute(
        """
        INSERT OR IGNORE INTO fixture (
            fixture_id, date_utc, league, league_id, league_country,
            home_id, away_id, home_name, away_name,
            first_seen_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, 1, 2, 'Home FC', 'Away FC', ?, ?)
        """,
        (fixture_id, now, "Premier League", league_id, "England", now, now),
    )


def test_replace_and_load_ai_picks(tmp_path):
    from dg.ai.vet_strongest import load_ai_picks

    conn = init_db(connect(tmp_path / "ai.db"))
    _seed_fixture(conn, 42, league_id=40)
    conn.execute(
        "UPDATE fixture SET league = 'Championship', league_country = 'England', "
        "home_logo = 'https://example.com/home.png', away_logo = 'https://example.com/away.png' "
        "WHERE fixture_id = 42"
    )
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
            "ai_components": {"agreement": 3, "coherence": 2, "market_trust": 3, "concerns": []},
        }
    ]
    n = replace_ai_picks_for_day(conn, "2026-08-30", approved, model="test-model")
    assert n == 1
    loaded = load_ai_picks(conn, "2026-08-30")
    assert len(loaded) == 1
    assert loaded[0]["ai_score"] == 81
    assert "Strong over" in loaded[0]["ai_reason"]
    assert loaded[0]["home_logo"] == "https://example.com/home.png"
    assert loaded[0]["away_logo"] == "https://example.com/away.png"
    assert loaded[0]["ai_components"]["agreement"] == 3
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

    vet_day = {
        "day": "2026-08-30",
        "predictions": [{**_vet_prediction(7), "home_name": "Home FC", "away_name": "Away FC"}],
        "picks": [],
        "empty": False,
    }

    monkeypatch.setattr(
        best_leans,
        "load_strongest_day",
        lambda date=None: vet_day,
    )

    def fake_chat(system, user):
        assert "component" in system.lower() or "publish" in system.lower()
        return json.dumps(
            {
                "picks": [
                    {
                        "fixtureId": 7,
                        "marketKey": "match_1x2",
                        "verdict": "publish",
                        "agreement": 3,
                        "coherence": 3,
                        "marketTrust": 2,
                        "concerns": [],
                        "reason": "Aligned home lean.",
                    }
                ]
            }
        )

    conn = init_db(connect(config.DB_PATH))
    _seed_fixture(conn, 7)
    import dg.ai.vet_strongest as vs

    monkeypatch.setattr(
        vs,
        "load_strongest_day",
        lambda date=None: vet_day,
    )
    summary = vet_strongest_for_day(conn, day="2026-08-30", chat_fn=fake_chat)
    assert summary["n_fixtures"] == 1
    assert summary["n_candidates"] == 1
    assert summary["written"] == 1
    assert summary["n_approved"] == 1
    assert summary["n_flat_score_fallback"] == 0
    from dg.ai.vet_strongest import load_ai_picks

    picks = load_ai_picks(conn, "2026-08-30")
    # agreement 3 + coherence 3 + trust 2 + strength(0.7→0.5) = 30+30+13.33+10 ≈ 83
    assert picks[0]["ai_score"] >= 70
    assert abs(picks[0]["ai_score"] - 70) > 3  # not echoing match-winner %
    conn.close()


def test_vet_flat_score_fallback_counts(tmp_path, monkeypatch):
    from dg import config
    import dg.ai.vet_strongest as vs

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dg.db")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "AI_VET_MIN_SCORE", 70)
    config.ensure_dirs()

    monkeypatch.setattr(
        vs,
        "load_strongest_day",
        lambda date=None: {
            "day": "2026-08-30",
            "predictions": [_vet_prediction(7)],
            "picks": [],
            "empty": False,
        },
    )

    def fake_chat(system, user):
        return json.dumps(
            {
                "picks": [
                    {
                        "fixtureId": 7,
                        "marketKey": "match_1x2",
                        "score": 85,
                        "approve": True,
                        "reason": "legacy flat score",
                    }
                ]
            }
        )

    conn = init_db(connect(config.DB_PATH))
    _seed_fixture(conn, 7)
    summary = vs.vet_strongest_for_day(conn, day="2026-08-30", chat_fn=fake_chat)
    assert summary["written"] == 1
    assert summary["n_flat_score_fallback"] == 1
    conn.close()


def test_vet_llm_can_pick_alternate_market(tmp_path, monkeypatch):
    from dg import config
    import dg.ai.vet_strongest as vs

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dg.db")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "AI_VET_MIN_SCORE", 70)
    config.ensure_dirs()

    pred = {
        "fixture_id": 99,
        "home_name": "Alpha",
        "away_name": "Beta",
        "lean": "Home",
        "confidence": "high",
        "score": 0.45,
        "dg_sim_lean": "Home",
        "book_lean": "Home",
        "probs": {"home": 0.74, "draw": 0.16, "away": 0.10},
        "markets": {
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
        "date_utc": "2026-08-30T15:00:00+00:00",
        "kickoff_display": "Sun 30 Aug · 16:00 WAT",
    }
    monkeypatch.setattr(
        vs,
        "load_strongest_day",
        lambda date=None: {
            "day": "2026-08-30",
            "predictions": [pred],
            "picks": [],
            "empty": False,
        },
    )

    def fake_chat(system, user):
        return json.dumps(
            {
                "picks": [
                    {
                        "fixtureId": 99,
                        "marketKey": "btts",
                        "verdict": "publish",
                        "agreement": 3,
                        "coherence": 3,
                        "marketTrust": 3,
                        "concerns": [],
                        "reason": "BTTS more coherent.",
                    }
                ]
            }
        )

    conn = init_db(connect(config.DB_PATH))
    _seed_fixture(conn, 99)
    summary = vs.vet_strongest_for_day(conn, day="2026-08-30", chat_fn=fake_chat)
    assert summary["written"] == 1
    from dg.ai.vet_strongest import load_ai_picks

    picks = load_ai_picks(conn, "2026-08-30")
    assert picks[0]["market_key"] == "btts"
    conn.close()
