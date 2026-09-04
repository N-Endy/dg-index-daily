"""Tests for AI Picks vetting (mocked LLM)."""
from __future__ import annotations

import json

from dg.ai.vet_strongest import (
    build_board_envelope,
    compute_publish_score,
    fixture_group_payload,
    gate_screen_scores,
    load_board_note,
    parse_screen_response,
    replace_ai_picks_for_day,
    replace_board_note_for_day,
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
        "home_win_pct": 55.0,
        "draw_pct": 25.0,
        "away_win_pct": 20.0,
        "over_2_5_pct": 60.0,
        "btts_pct": 55.0,
        "sim_xg_home": 1.4,
        "sim_xg_away": 1.1,
        "probs": {
            "home": 0.7,
            "draw": 0.2,
            "away": 0.1,
            "lam_home": 1.5,
            "lam_away": 1.1,
            "over_2_5": 0.58,
            "btts_yes": 0.54,
        },
        "markets": {},
        "book_odds": {
            "home_win": 1.9,
            "draw": 3.5,
            "away_win": 4.0,
            "over_2_5": 1.8,
            "under_2_5": 2.0,
            "btts_yes": 1.9,
            "btts_no": 1.9,
        },
        "sim_stats": {
            "xg": {"home": 1.4, "away": 1.1},
            "percents": {
                "home_win_pct": 55.0,
                "draw_pct": 25.0,
                "away_win_pct": 20.0,
                "over_2_5_pct": 60.0,
                "under_2_5_pct": 40.0,
                "btts_pct": 55.0,
                "btts_no_pct": 45.0,
                "corners_over_9_5_pct": 52.0,
                "shots_over_25_5_pct": 70.0,
                "sot_over_8_5_pct": 65.0,
            },
            "matchup_pace": {"score": 0.2},
        },
        "date_utc": "2026-08-30T15:00:00+00:00",
        "kickoff_display": "Sun 30 Aug · 16:00 WAT",
    }


def _vet_ctx(n: int = 4) -> dict:
    preds = [_vet_prediction(i) for i in range(1, n + 1)]
    return {
        "day": "2026-08-30",
        "predictions": preds,
        "picks": [],
        "empty": False,
        "scoring_env": {},
    }


def _llm_publish(
    fixture_id: int,
    *,
    market_key: str = "match_1x2",
    lean: str = "Home",
    coherence: int = 3,
    reason: str = "ok",
) -> dict:
    return {
        "fixtureId": fixture_id,
        "marketKey": market_key,
        "lean": lean,
        "verdict": "publish",
        "coherence": coherence,
        "concerns": [],
        "reason": reason,
    }


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


def _seed_calibration(conn, *, market_key="goals_2_5", band="92_plus", rate=0.62, n=400):
    from dg.report.market_reliability import store_market_calibration

    hits = int(round(rate * n))
    rows = [
        {
            "market_key": market_key,
            "agreement_tier": "agree2",
            "prob_band": band,
            "n_graded": n,
            "hits": hits,
            "hit_rate": rate,
        },
        {
            "market_key": market_key,
            "agreement_tier": "agree2",
            "prob_band": "all",
            "n_graded": n,
            "hits": hits,
            "hit_rate": rate,
        },
        {
            "market_key": market_key,
            "agreement_tier": "agree1",
            "prob_band": band,
            "n_graded": n,
            "hits": int(round(0.48 * n)),
            "hit_rate": 0.48,
        },
        {
            "market_key": market_key,
            "agreement_tier": "agree1",
            "prob_band": "all",
            "n_graded": n,
            "hits": int(round(0.48 * n)),
            "hit_rate": 0.48,
        },
        {
            "market_key": "all",
            "agreement_tier": "agree2",
            "prob_band": "all",
            "n_graded": n * 2,
            "hits": hits * 2,
            "hit_rate": rate,
        },
        {
            "market_key": "all",
            "agreement_tier": "agree1",
            "prob_band": "all",
            "n_graded": n,
            "hits": int(round(0.48 * n)),
            "hit_rate": 0.48,
        },
        {
            "market_key": "all",
            "agreement_tier": "all",
            "prob_band": "all",
            "n_graded": n * 3,
            "hits": hits * 2 + int(round(0.48 * n)),
            "hit_rate": 0.56,
        },
        {
            "market_key": "btts",
            "agreement_tier": "agree2",
            "prob_band": "lt_75",
            "n_graded": n,
            "hits": int(round(0.59 * n)),
            "hit_rate": 0.59,
        },
        {
            "market_key": "btts",
            "agreement_tier": "agree2",
            "prob_band": "92_plus",
            "n_graded": n,
            "hits": int(round(0.592 * n)),
            "hit_rate": 0.592,
        },
        {
            "market_key": "btts",
            "agreement_tier": "agree2",
            "prob_band": "all",
            "n_graded": n,
            "hits": int(round(0.59 * n)),
            "hit_rate": 0.59,
        },
        {
            "market_key": "goals_2_5",
            "agreement_tier": "agree2",
            "prob_band": "92_plus",
            "n_graded": n,
            "hits": int(round(0.62 * n)),
            "hit_rate": 0.62,
        },
        {
            "market_key": "goals_2_5",
            "agreement_tier": "agree2",
            "prob_band": "all",
            "n_graded": n,
            "hits": int(round(0.62 * n)),
            "hit_rate": 0.62,
        },
        {
            "market_key": "match_1x2",
            "agreement_tier": "agree2",
            "prob_band": "lt_75",
            "n_graded": n,
            "hits": hits,
            "hit_rate": rate,
        },
        {
            "market_key": "match_1x2",
            "agreement_tier": "agree2",
            "prob_band": "all",
            "n_graded": n,
            "hits": hits,
            "hit_rate": rate,
        },
        {
            "market_key": "goals_3_5",
            "agreement_tier": "agree2",
            "prob_band": "92_plus",
            "n_graded": n,
            "hits": int(round(0.34 * n)),
            "hit_rate": 0.34,
        },
        {
            "market_key": "goals_3_5",
            "agreement_tier": "agree2",
            "prob_band": "all",
            "n_graded": n,
            "hits": int(round(0.34 * n)),
            "hit_rate": 0.34,
        },
    ]
    seen = set()
    deduped = []
    for row in rows:
        key = (row["market_key"], row["agreement_tier"], row["prob_band"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    store_market_calibration(conn, {"calibration": deduped})


def _calib_dict(*, rate=0.62, n=400, market="goals_2_5", band="92_plus"):
    return {
        (market, "agree2", band): {"hit_rate": rate, "n_graded": n, "hits": int(rate * n)},
        (market, "agree2", "all"): {"hit_rate": rate, "n_graded": n, "hits": int(rate * n)},
        (market, "agree1", band): {"hit_rate": 0.48, "n_graded": n, "hits": int(0.48 * n)},
        (market, "agree1", "all"): {"hit_rate": 0.48, "n_graded": n, "hits": int(0.48 * n)},
        ("all", "agree2", "all"): {"hit_rate": rate, "n_graded": n, "hits": int(rate * n)},
        ("all", "agree1", "all"): {"hit_rate": 0.48, "n_graded": n, "hits": int(0.48 * n)},
        ("all", "all", "all"): {"hit_rate": 0.56, "n_graded": n * 2, "hits": int(0.56 * n * 2)},
        ("match_1x2", "agree2", "lt_75"): {
            "hit_rate": rate,
            "n_graded": n,
            "hits": int(rate * n),
        },
        ("match_1x2", "agree2", "all"): {
            "hit_rate": rate,
            "n_graded": n,
            "hits": int(rate * n),
        },
        ("goals_3_5", "agree2", "92_plus"): {
            "hit_rate": 0.34,
            "n_graded": n,
            "hits": int(0.34 * n),
        },
        ("goals_3_5", "agree2", "all"): {
            "hit_rate": 0.34,
            "n_graded": n,
            "hits": int(0.34 * n),
        },
        ("btts", "agree2", "92_plus"): {
            "hit_rate": 0.592,
            "n_graded": n,
            "hits": int(0.592 * n),
        },
        ("btts", "agree2", "all"): {"hit_rate": 0.59, "n_graded": n, "hits": int(0.59 * n)},
        ("btts", "agree2", "lt_75"): {"hit_rate": 0.59, "n_graded": n, "hits": int(0.59 * n)},
    }


def test_compute_publish_score_not_echo_of_prob():
    # Fully coherent at base 0.62 → judgment 1.02 → ~63, never 96/100.
    score = compute_publish_score(
        base_rate=0.62,
        coherence=3,
        concerns=[],
    )
    assert 55 <= score <= 70
    assert score != 96
    assert score < 95

    thin = compute_publish_score(
        base_rate=0.62,
        coherence=1,
        concerns=["single source only"],
    )
    assert thin < score


def test_compute_publish_score_differs_at_same_base():
    strong = compute_publish_score(base_rate=0.62, coherence=3, concerns=[])
    weak = compute_publish_score(base_rate=0.62, coherence=0, concerns=["x", "y"])
    assert strong > weak


def test_parse_component_response():
    raw = json.dumps(
        {
            "picks": [
                {
                    "fixtureId": 10,
                    "marketKey": "goals_2_5",
                    "lean": "Over",
                    "verdict": "publish",
                    "coherence": 2,
                    "concerns": ["thin book"],
                    "risk": "Late equaliser risk if game opens up.",
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
    assert rows[0]["components"]["coherence"] == 2
    assert "agreement" not in rows[0]["components"]
    assert "thin book" in rows[0]["concerns"]
    assert rows[0]["risk"] == "Late equaliser risk if game opens up."


def test_single_source_ranks_below_two_source():
    """Vancouver-style single-source must score below corroborated pick."""
    calib = _calib_dict()
    cands = [
        {
            "fixture_id": 1,
            "market_key": "goals_2_5",
            "prob": 0.95,
            "agreement_key": "aligned",
            "agreement_n_sources": 2,
            "date_utc": "2026-08-30T15:00:00+00:00",
        },
        {
            "fixture_id": 2,
            "market_key": "goals_2_5",
            "prob": 0.95,
            "agreement_key": "aligned",
            "agreement_n_sources": 1,
            "date_utc": "2026-08-30T15:00:00+00:00",
        },
    ]
    scores = parse_screen_response(
        json.dumps(
            {
                "picks": [
                    {
                        "fixtureId": 1,
                        "marketKey": "goals_2_5",
                        "lean": "Over",
                        "verdict": "publish",
                        "coherence": 3,
                        "concerns": [],
                        "reason": "two source",
                    },
                    {
                        "fixtureId": 2,
                        "marketKey": "goals_2_5",
                        "lean": "Over",
                        "verdict": "publish",
                        "coherence": 3,
                        "concerns": ["dg only"],
                        "reason": "single source",
                    },
                ]
            }
        )
    )
    approved = gate_screen_scores(cands, scores, min_score=40, calibration=calib)
    by_fid = {p["fixture_id"]: p for p in approved}
    assert by_fid[1]["ai_score"] > by_fid[2]["ai_score"]
    assert by_fid[1]["ai_agreement_tier"] == "agree2"
    assert by_fid[2]["ai_agreement_tier"] == "agree1"


def test_fixture_group_payload_uses_signal_pack():
    from dg.ai.signal_pack import build_fixture_signal_pack

    pred = _vet_prediction(42)
    pack = build_fixture_signal_pack(pred)
    group = {
        "fixture_id": 42,
        "home_name": "A",
        "away_name": "B",
        "league": "EPL",
        "signal_pack": pack,
        "candidates": [],
    }
    a = fixture_group_payload(group)
    b = fixture_group_payload(group)
    assert a == b
    assert a["fixtureId"] == 42
    assert "sim" in a and "book" in a
    assert "allowedMarkets" in a
    assert "candidates" not in a
    assert "lean" not in a
    assert "prob" not in a
    assert "confidence" not in a
    dumped = json.dumps(a)
    assert '"lean": "Home"' not in dumped


def test_vet_batches_candidates(tmp_path, monkeypatch):
    from dg import config
    import dg.ai.vet_strongest as vs

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dg.db")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "AI_VET_BATCH_SIZE", 2)
    monkeypatch.setattr(config, "AI_VET_MIN_SCORE", 55)
    monkeypatch.setattr(config, "AI_VET_BOARD_NOTE", False)
    monkeypatch.setattr(config, "STRONGEST_DIVERSIFY_ENABLED", False)
    config.ensure_dirs()

    monkeypatch.setattr(
        vs,
        "load_strongest_day",
        lambda date=None: _vet_ctx(4),
    )
    calls = {"n": 0}
    boards = []

    def fake_chat(system, user):
        calls["n"] += 1
        assert "minScoreHint" not in user
        assert "0-100" in system or "coherence" in system.lower()
        payload = json.loads(user.split("\n", 1)[-1])
        assert "board" in payload
        boards.append(payload["board"])
        for fx in payload["fixtures"]:
            assert "sim" in fx
            assert "allowedMarkets" in fx
            assert "candidates" not in fx
        fids = [1, 2] if calls["n"] == 1 else [3, 4]
        return json.dumps({"picks": [_llm_publish(fid) for fid in fids]})

    conn = init_db(connect(config.DB_PATH))
    _seed_calibration(conn, market_key="match_1x2", band="lt_75", rate=0.55)
    summary = vs.vet_strongest_for_day(conn, day="2026-08-30", chat_fn=fake_chat)
    assert calls["n"] == 2
    assert summary["n_batches"] == 2
    assert summary["written"] == 4
    assert summary["n_flat_score_fallback"] == 0
    assert "echo_rate" in summary
    assert boards[0] == boards[1]
    assert boards[0]["totalFixtures"] == 4
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
            "prob": 0.70,
        },
        {
            "fixture_id": 2,
            "market_key": "btts",
            "league": "EPL",
            "home_name": "C",
            "away_name": "D",
            "date_utc": "2026-08-30T16:00:00+00:00",
            "lean": "Yes",
            "prob": 0.70,
        },
        {
            "fixture_id": 3,
            "market_key": "goals_2_5",
            "league": "EPL",
            "home_name": "E",
            "away_name": "F",
            "date_utc": "2026-08-30T17:00:00+00:00",
            "lean": "Over",
            "prob": 0.70,
        },
    ]
    scores = [
        {"fixture_id": 1, "market_key": "match_1x2", "score": 90, "approve": True, "reason": "ok", "score_source": "flat"},
        {"fixture_id": 2, "market_key": "btts", "score": 95, "approve": False, "reason": "no", "score_source": "flat"},
        {"fixture_id": 3, "market_key": "goals_2_5", "score": 40, "approve": True, "reason": "low", "score_source": "flat"},
        {"fixture_id": 99, "market_key": "match_1x2", "score": 99, "approve": True, "reason": "unknown", "score_source": "flat"},
    ]
    # Ensure candidates map to agree2 so seeded rates apply
    for c in cands:
        c["agreement_key"] = "aligned"
        c["agreement_n_sources"] = 2
    calib = _calib_dict(rate=0.60, market="match_1x2", band="lt_75")
    # flat 90 → judgment ~1.03 → 0.60*1.03 ≈ 62; floor 55 passes
    # flat 40 → judgment 0.93 → 0.60*0.93 ≈ 56 — use min_score=58 to fail
    approved = gate_screen_scores(cands, scores, min_score=58, calibration=calib)
    assert len(approved) == 1
    assert approved[0]["fixture_id"] == 1
    assert approved[0]["ai_score"] < 90  # remapped, not raw flat
    assert approved[0]["ai_score_source"] == "flat"


def test_gate_components_recompute_with_base_rate():
    cands = [
        {
            "fixture_id": 1,
            "market_key": "goals_2_5",
            "league": "EPL",
            "prob": 0.96,
            "agreement_key": "aligned",
            "agreement_n_sources": 2,
            "date_utc": "2026-08-30T15:00:00+00:00",
        }
    ]
    calib = _calib_dict(rate=0.62, market="goals_2_5", band="92_plus")
    scores = parse_screen_response(
        json.dumps(
            {
                "picks": [
                    {
                        "fixtureId": 1,
                        "marketKey": "goals_2_5",
                        "lean": "Over",
                        "verdict": "publish",
                        "coherence": 3,
                        "concerns": [],
                        "reason": "full stack",
                    }
                ]
            }
        )
    )
    approved = gate_screen_scores(cands, scores, min_score=55, calibration=calib)
    assert len(approved) == 1
    # 0.62 * 1.02 ≈ 63
    assert 60 <= approved[0]["ai_score"] <= 66
    assert approved[0]["ai_score_source"] == "components"
    assert approved[0]["ai_score"] != 96
    assert abs(approved[0]["ai_base_rate"] - 0.62) < 0.03
    assert approved[0]["ai_agreement_tier"] == "agree2"

    # Low-reliability market falls below floor
    weak_cands = [
        {
            "fixture_id": 1,
            "market_key": "goals_3_5",
            "league": "EPL",
            "prob": 0.93,
            "agreement_key": "aligned",
            "agreement_n_sources": 2,
            "date_utc": "2026-08-30T15:00:00+00:00",
        }
    ]
    weak_scores = parse_screen_response(
        json.dumps(
            {
                "picks": [
                    {
                        "fixtureId": 1,
                        "marketKey": "goals_3_5",
                        "lean": "Over",
                        "verdict": "publish",
                        "coherence": 3,
                        "concerns": [],
                        "reason": "weak market",
                    }
                ]
            }
        )
    )
    weak = gate_screen_scores(weak_cands, weak_scores, min_score=55, calibration=calib)
    assert weak == []


def test_gate_one_per_fixture_keeps_higher_score():
    cands = [
        {
            "fixture_id": 1,
            "market_key": "match_1x2",
            "league": "A",
            "prob": 0.70,
            "date_utc": "2026-08-30T12:00:00+00:00",
        },
        {
            "fixture_id": 1,
            "market_key": "goals_2_5",
            "league": "A",
            "prob": 0.93,
            "date_utc": "2026-08-30T12:00:00+00:00",
        },
    ]
    scores = parse_screen_response(
        json.dumps(
            {
                "picks": [
                    {
                        "fixtureId": 1,
                        "marketKey": "match_1x2",
                        "lean": "Home",
                        "verdict": "publish",
                        "agreement": 2,
                        "coherence": 2,
                        "concerns": [],
                        "reason": "a",
                    },
                    {
                        "fixtureId": 1,
                        "marketKey": "goals_2_5",
                        "lean": "Over",
                        "verdict": "publish",
                        "agreement": 3,
                        "coherence": 3,
                        "concerns": [],
                        "reason": "b",
                    },
                ]
            }
        )
    )
    calib = _calib_dict()
    approved = gate_screen_scores(cands, scores, min_score=55, calibration=calib)
    assert len(approved) == 1
    assert approved[0]["market_key"] == "goals_2_5"


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
            "ai_components": {"coherence": 2, "concerns": []},
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
    assert loaded[0]["ai_components"]["coherence"] == 2
    assert "agreement" not in (loaded[0].get("ai_components") or {})
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
    monkeypatch.setattr(config, "AI_VET_MIN_SCORE", 55)
    monkeypatch.setattr(config, "AI_VET_BOARD_NOTE", False)
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
                        "lean": "Home",
                        "verdict": "publish",
                        "agreement": 3,
                        "coherence": 3,
                        "concerns": [],
                        "reason": "Aligned home lean.",
                    }
                ]
            }
        )

    conn = init_db(connect(config.DB_PATH))
    _seed_fixture(conn, 7)
    _seed_calibration(conn, market_key="match_1x2", band="lt_75", rate=0.55)
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
    assert picks[0]["ai_score"] >= 55
    assert picks[0]["ai_score"] < 95
    assert picks[0]["ai_base_rate"] is not None
    conn.close()


def test_vet_flat_score_fallback_counts(tmp_path, monkeypatch):
    from dg import config
    import dg.ai.vet_strongest as vs

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dg.db")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "AI_VET_MIN_SCORE", 55)
    monkeypatch.setattr(config, "AI_VET_BOARD_NOTE", False)
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
                        "lean": "Home",
                        "score": 85,
                        "approve": True,
                        "reason": "legacy flat score",
                    }
                ]
            }
        )

    conn = init_db(connect(config.DB_PATH))
    _seed_fixture(conn, 7)
    _seed_calibration(conn, market_key="match_1x2", band="lt_75", rate=0.55)
    summary = vs.vet_strongest_for_day(conn, day="2026-08-30", chat_fn=fake_chat)
    assert summary["written"] == 1
    assert summary["n_flat_score_fallback"] == 1
    from dg.ai.vet_strongest import load_ai_picks

    picks = load_ai_picks(conn, "2026-08-30")
    # Flat 85 remapped through base_rate, not published as 85.
    assert picks[0]["ai_score"] != 85
    assert picks[0]["ai_score"] < 80
    conn.close()


def test_vet_llm_can_pick_alternate_market(tmp_path, monkeypatch):
    from dg import config
    import dg.ai.vet_strongest as vs

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dg.db")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "AI_VET_MIN_SCORE", 55)
    monkeypatch.setattr(config, "AI_VET_BOARD_NOTE", False)
    config.ensure_dirs()

    pred = {
        **_vet_prediction(99),
        "home_name": "Alpha",
        "away_name": "Beta",
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
                        "lean": "Yes",
                        "verdict": "publish",
                        "agreement": 3,
                        "coherence": 3,
                        "concerns": [],
                        "reason": "BTTS more coherent.",
                    }
                ]
            }
        )

    conn = init_db(connect(config.DB_PATH))
    _seed_fixture(conn, 99)
    _seed_calibration(conn)
    summary = vs.vet_strongest_for_day(conn, day="2026-08-30", chat_fn=fake_chat)
    assert summary["written"] == 1
    from dg.ai.vet_strongest import load_ai_picks

    picks = load_ai_picks(conn, "2026-08-30")
    assert picks[0]["market_key"] == "btts"
    conn.close()


def _gate_market(lean: str, prob: float = 0.70) -> dict:
    return {
        "lean": lean,
        "confidence": "high",
        "score": 0.2,
        "prob": prob,
        "dg_lean": lean,
        "book_lean": lean,
    }


def test_candidate_payload_uses_calibration_base_rate():
    from dg.ai.vet_strongest import candidate_payload
    from dg.report.market_reliability import reliability_for

    calib = _calib_dict()
    pick = {
        "fixture_id": 1,
        "market_key": "goals_2_5",
        "lean": "Over",
        "prob": 0.92,
        "agreement_key": "aligned",
        "agreement_n_sources": 2,
        "why": ["pace"],
    }
    payload = candidate_payload(pick, calibration=calib)
    reli = reliability_for(calib, "goals_2_5", "agree2", 0.92)
    assert payload["baseRate"] == round(float(reli["rate"]), 3)
    assert payload["baseN"] == int(reli.get("n") or 0)
    assert payload["projectedEst"] == compute_publish_score(
        base_rate=float(reli["rate"]), coherence=2
    )


def test_build_board_envelope_deterministic():
    groups = [{"fixture_id": 1}, {"fixture_id": 2}]
    candidates = [
        {"market_key": "goals_2_5"},
        {"market_key": "btts"},
        {"market_key": "goals_2_5"},
    ]
    env = {
        "gpm_recent": 3.1,
        "gpm_baseline": 2.6,
        "stretched": True,
        "caution": "hot",
    }
    sb = {"hit_rate": 0.55, "n_graded": 40, "window_days": 30}
    a = build_board_envelope(groups, candidates, scoring_env=env, ai_scoreboard=sb)
    b = build_board_envelope(groups, candidates, scoring_env=env, ai_scoreboard=sb)
    assert a == b
    assert a["totalFixtures"] == 2
    assert a["marketMix"] == {"btts": 1, "goals_2_5": 2}
    assert a["scoringEnv"]["stretched"] is True
    assert a["recentAiHitRate"]["nGraded"] == 40


def test_ai_vet_signal_pack_lists_allowed_markets(tmp_path, monkeypatch):
    from dg import config
    import dg.ai.vet_strongest as vs

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dg.db")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "AI_VET_MIN_SCORE", 55)
    monkeypatch.setattr(config, "AI_VET_BOARD_NOTE", False)
    config.ensure_dirs()

    pred = _vet_prediction(50)
    monkeypatch.setattr(
        vs,
        "load_strongest_day",
        lambda date=None: {
            "day": "2026-08-30",
            "predictions": [pred],
            "picks": [],
            "empty": False,
            "scoring_env": {},
        },
    )

    seen = {"n": 0}

    def fake_chat(system, user):
        seen["n"] += 1
        payload = json.loads(user.split("\n", 1)[-1])
        fx = payload["fixtures"][0]
        assert "candidates" not in fx
        assert "sim" in fx and "book" in fx
        keys = {m["marketKey"] for m in fx["allowedMarkets"]}
        assert "match_1x2" in keys and "goals_2_5" in keys
        return json.dumps({"picks": [_llm_publish(50)]})

    conn = init_db(connect(config.DB_PATH))
    _seed_fixture(conn, 50)
    _seed_calibration(conn, market_key="match_1x2", band="lt_75", rate=0.55)
    summary = vs.vet_strongest_for_day(conn, day="2026-08-30", chat_fn=fake_chat)
    assert summary["n_candidates"] == 1
    assert summary["written"] == 1
    assert seen["n"] == 1
    conn.close()


def test_board_note_persists_and_risk_round_trips(tmp_path, monkeypatch):
    from dg import config
    import dg.ai.vet_strongest as vs
    from dg.report.ai_picks import load_ai_picks_page

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dg.db")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "AI_VET_MIN_SCORE", 55)
    monkeypatch.setattr(config, "AI_VET_BOARD_NOTE", True)
    config.ensure_dirs()

    vet_day = {
        "day": "2026-08-30",
        "predictions": [_vet_prediction(11)],
        "picks": [],
        "empty": False,
        "scoring_env": {
            "gpm_recent": 3.0,
            "gpm_baseline": 2.5,
            "stretched": True,
            "caution": "hot scoring",
        },
    }
    monkeypatch.setattr(vs, "load_strongest_day", lambda date=None: vet_day)

    def fake_chat(system, user):
        if "board note" in system.lower():
            return json.dumps(
                {
                    "note": "A selective board with one home lean.",
                    "themes": ["home lean"],
                }
            )
        return json.dumps(
            {
                "picks": [
                    {
                        "fixtureId": 11,
                        "marketKey": "match_1x2",
                        "lean": "Home",
                        "verdict": "publish",
                        "coherence": 3,
                        "concerns": [],
                        "risk": "Away counter could spoil it.",
                        "reason": "Aligned home lean.",
                    }
                ]
            }
        )

    conn = init_db(connect(config.DB_PATH))
    _seed_fixture(conn, 11)
    _seed_calibration(conn, market_key="match_1x2", band="lt_75", rate=0.55)
    summary = vs.vet_strongest_for_day(conn, day="2026-08-30", chat_fn=fake_chat)
    assert summary["written"] == 1
    assert summary["board_note_written"] == 1
    assert summary["board_note_error"] is None

    note = load_board_note(conn, "2026-08-30")
    assert note is not None
    assert "selective board" in note["note"]
    assert "home lean" in note["themes"]

    from dg.ai.vet_strongest import load_ai_picks

    picks = load_ai_picks(conn, "2026-08-30")
    assert picks[0]["ai_risk"] == "Away counter could spoil it."
    # Est.% still comes from base rate × judgment, not from risk text.
    expected = compute_publish_score(base_rate=float(picks[0]["ai_base_rate"]), coherence=3)
    assert picks[0]["ai_score"] == expected
    conn.close()

    page = load_ai_picks_page(day="2026-08-30")
    assert page["board_note"]["note"]
    assert page["picks"][0]["ai_risk"] == "Away counter could spoil it."


def test_board_note_failure_preserves_picks(tmp_path, monkeypatch):
    from dg import config
    import dg.ai.vet_strongest as vs

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dg.db")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "AI_VET_MIN_SCORE", 55)
    monkeypatch.setattr(config, "AI_VET_BOARD_NOTE", True)
    config.ensure_dirs()

    monkeypatch.setattr(
        vs,
        "load_strongest_day",
        lambda date=None: {
            "day": "2026-08-30",
            "predictions": [_vet_prediction(12)],
            "picks": [],
            "empty": False,
            "scoring_env": {},
        },
    )

    def fake_chat(system, user):
        if "board note" in system.lower():
            raise RuntimeError("board note boom")
        return json.dumps(
            {
                "picks": [
                    {
                        "fixtureId": 12,
                        "marketKey": "match_1x2",
                        "lean": "Home",
                        "verdict": "publish",
                        "coherence": 3,
                        "concerns": [],
                        "reason": "ok",
                    }
                ]
            }
        )

    conn = init_db(connect(config.DB_PATH))
    _seed_fixture(conn, 12)
    _seed_calibration(conn, market_key="match_1x2", band="lt_75", rate=0.55)
    summary = vs.vet_strongest_for_day(conn, day="2026-08-30", chat_fn=fake_chat)
    assert summary["written"] == 1
    assert summary["errors"] == 0
    assert summary["board_note_written"] == 0
    assert "boom" in (summary["board_note_error"] or "")
    assert load_board_note(conn, "2026-08-30") is None

    from dg.ai.vet_strongest import load_ai_picks

    assert len(load_ai_picks(conn, "2026-08-30")) == 1
    conn.close()


def test_gate_risk_does_not_change_est_score():
    cands = [
        {
            "fixture_id": 1,
            "market_key": "goals_2_5",
            "lean": "Over",
            "prob": 0.92,
            "agreement_key": "aligned",
            "agreement_n_sources": 2,
            "league": "EPL",
            "home_name": "A",
            "away_name": "B",
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
                        "lean": "Over",
                        "verdict": "publish",
                        "coherence": 3,
                        "concerns": [],
                        "risk": "Anything dramatic",
                        "reason": "ok",
                    }
                ]
            }
        )
    )
    approved = gate_screen_scores(cands, scores, min_score=40, calibration=_calib_dict())
    assert len(approved) == 1
    assert approved[0]["ai_risk"] == "Anything dramatic"
    assert approved[0]["ai_score"] == compute_publish_score(
        base_rate=float(approved[0]["ai_base_rate"]), coherence=3
    )


def test_replace_board_note_clears_on_empty(tmp_path, monkeypatch):
    from dg import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dg.db")
    config.ensure_dirs()
    conn = init_db(connect(config.DB_PATH))
    assert replace_board_note_for_day(
        conn, "2026-08-30", note="Hello", themes=["a"], model="test"
    ) == 1
    assert load_board_note(conn, "2026-08-30")["note"] == "Hello"
    assert replace_board_note_for_day(
        conn, "2026-08-30", note="", themes=[], model="test"
    ) == 0
    assert load_board_note(conn, "2026-08-30") is None
    conn.close()


def test_ai_soft_fallback_keeps_best_below_bar(tmp_path, monkeypatch):
    from dg import config
    import dg.ai.vet_strongest as vs
    from dg.ai.vet_strongest import load_ai_picks

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dg.db")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "AI_VET_MIN_SCORE", 80)
    monkeypatch.setattr(config, "AI_VET_SOFT_FALLBACK", True)
    monkeypatch.setattr(config, "AI_VET_BOARD_NOTE", False)
    monkeypatch.setattr(config, "STRONGEST_DIVERSIFY_ENABLED", False)
    config.ensure_dirs()

    monkeypatch.setattr(
        vs,
        "load_strongest_day",
        lambda date=None: {
            "day": "2026-08-30",
            "predictions": [_vet_prediction(21)],
            "picks": [],
            "empty": False,
            "scoring_env": {},
        },
    )

    def fake_chat(system, user):
        return json.dumps(
            {
                "picks": [
                    {
                        "fixtureId": 21,
                        "marketKey": "match_1x2",
                        "lean": "Home",
                        "verdict": "publish",
                        "coherence": 3,
                        "concerns": [],
                        "reason": "best of a thin set",
                    }
                ]
            }
        )

    conn = init_db(connect(config.DB_PATH))
    _seed_fixture(conn, 21)
    # Base rate ~0.55 → Est.% around mid-50s, below 80 floor.
    _seed_calibration(conn, market_key="match_1x2", band="lt_75", rate=0.55)
    summary = vs.vet_strongest_for_day(conn, day="2026-08-30", chat_fn=fake_chat)
    assert summary["soft_fallback"] is True
    assert summary["written"] == 1
    picks = load_ai_picks(conn, "2026-08-30")
    assert len(picks) == 1
    assert picks[0]["ai_score"] < 80
    assert "below bar" in (picks[0].get("ai_reason") or "")
    conn.close()


def test_vet_no_candidates_preserves_existing_ai_picks(tmp_path, monkeypatch):
    from dg import config
    import dg.ai.vet_strongest as vs
    from dg.ai.vet_strongest import load_ai_picks, replace_ai_picks_for_day

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dg.db")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "AI_VET_BOARD_NOTE", False)
    config.ensure_dirs()

    # Predictions exist but no DG/book signals → nothing to vet.
    bare = {
        "fixture_id": 33,
        "home_name": "H33",
        "away_name": "A33",
        "lean": "Home",
        "confidence": "high",
        "probs": {"home": 0.7, "draw": 0.2, "away": 0.1},
        "markets": {},
        "book_odds": {},
        "sim_stats": {},
        "date_utc": "2026-08-30T15:00:00+00:00",
    }
    monkeypatch.setattr(
        vs,
        "load_strongest_day",
        lambda date=None: {
            "day": "2026-08-30",
            "predictions": [bare],
            "picks": [],
            "empty": False,
            "scoring_env": {},
        },
    )

    conn = init_db(connect(config.DB_PATH))
    _seed_fixture(conn, 33)
    replace_ai_picks_for_day(
        conn,
        "2026-08-30",
        [
            {
                "fixture_id": 33,
                "market_key": "goals_2_5",
                "lean": "Over",
                "ai_score": 60,
                "ai_reason": "prior pick",
                "league": "EPL",
                "home_name": "H",
                "away_name": "A",
            }
        ],
        model="test",
    )
    assert len(load_ai_picks(conn, "2026-08-30")) == 1

    summary = vs.vet_strongest_for_day(conn, day="2026-08-30", chat_fn=lambda s, u: "{}")
    assert summary["skipped_no_candidates"] is True
    assert summary["written"] == 0
    picks = load_ai_picks(conn, "2026-08-30")
    assert len(picks) == 1
    assert picks[0]["ai_reason"] == "prior pick"
    conn.close()


def test_vet_can_pick_lean_disagreeing_with_dashboard_chip(tmp_path, monkeypatch):
    """AI may publish a lean that disagrees with our dashboard market chip."""
    from dg import config
    import dg.ai.vet_strongest as vs
    from dg.ai.vet_strongest import load_ai_picks
    from dg.ai.signal_pack import build_fixture_signal_pack

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dg.db")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "AI_VET_MIN_SCORE", 55)
    monkeypatch.setattr(config, "AI_VET_BOARD_NOTE", False)
    monkeypatch.setattr(config, "AI_VET_SOFT_FALLBACK", False)
    config.ensure_dirs()

    pred = {
        **_vet_prediction(44),
        "markets": {
            "goals_2_5": {
                "lean": "Over",
                "confidence": "high",
                "score": 0.4,
                "prob": 0.72,
                "dg_lean": "Under",
                "book_lean": "Under",
            }
        },
    }
    # Flip sim toward Under so AI lean Under is signal-backed.
    pred["sim_stats"]["percents"]["over_2_5_pct"] = 38.0
    pred["sim_stats"]["percents"]["under_2_5_pct"] = 62.0
    pred["over_2_5_pct"] = 38.0
    pred["book_odds"]["over_2_5"] = 2.2
    pred["book_odds"]["under_2_5"] = 1.7

    pack = build_fixture_signal_pack(pred)
    assert "lean" not in pack
    assert pack["sim"]["percents"].get("over_2_5_pct") == 38.0

    monkeypatch.setattr(
        vs,
        "load_strongest_day",
        lambda date=None: {
            "day": "2026-08-30",
            "predictions": [pred],
            "picks": [],
            "empty": False,
            "scoring_env": {},
        },
    )

    def fake_chat(system, user):
        payload = json.loads(user.split("\n", 1)[-1])
        fx = payload["fixtures"][0]
        assert "candidates" not in fx
        assert "sim" in fx
        return json.dumps(
            {
                "picks": [
                    {
                        "fixtureId": 44,
                        "marketKey": "goals_2_5",
                        "lean": "Under",
                        "verdict": "publish",
                        "coherence": 3,
                        "concerns": [],
                        "reason": "sim and book favour under",
                    }
                ]
            }
        )

    conn = init_db(connect(config.DB_PATH))
    _seed_fixture(conn, 44)
    _seed_calibration(conn, market_key="goals_2_5", band="lt_75", rate=0.62)
    summary = vs.vet_strongest_for_day(conn, day="2026-08-30", chat_fn=fake_chat)
    assert summary["written"] == 1
    picks = load_ai_picks(conn, "2026-08-30")
    assert picks[0]["market_key"] == "goals_2_5"
    assert picks[0]["lean"] == "Under"
    conn.close()


def test_parse_rejects_invalid_ai_lean():
    rows = parse_screen_response(
        json.dumps(
            {
                "picks": [
                    {
                        "fixtureId": 1,
                        "marketKey": "goals_2_5",
                        "lean": "Home",
                        "verdict": "publish",
                        "coherence": 3,
                    }
                ]
            }
        )
    )
    assert rows == []


def test_signal_pack_omits_dashboard_leans():
    from dg.ai.signal_pack import build_fixture_signal_pack, materialize_ai_candidate

    pred = _vet_prediction(7)
    pred["markets"] = {
        "goals_2_5": {
            "lean": "Over",
            "confidence": "high",
            "prob": 0.9,
            "dg_lean": "Over",
            "book_lean": "Over",
        }
    }
    pack = build_fixture_signal_pack(pred)
    blob = json.dumps(pack)
    assert "markets" not in pack
    assert '"lean": "Over"' not in blob
    assert '"confidence"' not in blob
    cand = materialize_ai_candidate(pred, "goals_2_5", "Under")
    assert cand is not None
    assert cand["lean"] == "Under"
    assert cand["prob"] is not None


def test_signal_pack_prop_pct_follows_markets_line():
    from dg.ai.signal_pack import (
        _prop_sim_over_pct,
        build_fixture_signal_pack,
        reference_prob_for_lean,
    )

    pred = _vet_prediction(8)
    pred["markets"] = {"corners_9_5": {"line": 10.5, "lean": "Over", "prob": 0.61}}
    pred["sim_stats"]["percents"]["corners_over_9_5_pct"] = 52.0
    pred["sim_stats"]["percents"]["corners_over_10_5_pct"] = 61.0
    assert _prop_sim_over_pct(pred, "corners_9_5") == 61.0
    assert reference_prob_for_lean(pred, "corners_9_5", "Over") == 0.61
    assert reference_prob_for_lean(pred, "corners_9_5", "Under") == 0.39
    pack = build_fixture_signal_pack(pred)
    assert pack["sim"]["percents"]["corners_over_10_5_pct"] == 61.0
    assert pack["sim"]["percents"]["corners_over_9_5_pct"] == 52.0
