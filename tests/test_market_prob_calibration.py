"""Tests for per-market probability calibration."""
from __future__ import annotations

from dg import config
from dg.model.evaluate import _market_pos_prob
from dg.model.markets import markets_model_tag
from dg.model.supervised import (
    _auc,
    _auc_se,
    _fit_logit_platt,
    _logit,
    _samples_for_market_calibration,
    _sigmoid,
    apply_market_prob_calibration,
    fit_market_prob_calibration,
    load_market_aucs,
)
from dg.storage.db import connect, init_db


def _live_model_version(prefix: str = "test_v1") -> str:
    return f"{prefix}+{markets_model_tag()}"


def test_logit_platt_is_monotonic():
    probs = [0.55 + 0.004 * i for i in range(80)]
    labels = [1 if p > 0.70 else 0 for p in probs]
    a, b = _fit_logit_platt(probs, labels)
    cals = [_sigmoid(a * _logit(p) + b) for p in probs]
    assert all(cals[i] <= cals[i + 1] + 1e-9 for i in range(len(cals) - 1))


def test_auc_perfect_and_random():
    assert _auc([0.9, 0.8, 0.2, 0.1], [1, 1, 0, 0]) == 1.0
    mixed = _auc([0.5, 0.5, 0.5, 0.5], [1, 0, 1, 0])
    assert mixed is not None
    assert abs(mixed - 0.5) < 1e-9


def test_auc_se_shrinks_with_n():
    se_small = _auc_se(0.70, n_pos=20, n_neg=20)
    se_large = _auc_se(0.70, n_pos=200, n_neg=200)
    assert se_small is not None and se_large is not None
    assert se_large < se_small
    assert _auc_se(0.70, n_pos=0, n_neg=10) is None


def test_apply_identity_when_unfitted():
    markets = {
        "version": "x",
        "goals_2_5": {"lean": "Over", "prob": 0.81},
    }
    out = apply_market_prob_calibration(markets, {})
    assert out["goals_2_5"]["prob"] == 0.81
    assert out["goals_2_5"]["prob_raw"] == 0.81


def test_apply_shrinks_when_underpowered(monkeypatch):
    monkeypatch.setattr(config, "MARKET_PROB_CALIBRATION_ENABLED", True)
    monkeypatch.setattr(config, "MARKET_PROB_CALIBRATION_MIN_FIT", 80)
    monkeypatch.setattr(config, "MARKET_PROB_CALIBRATION_MIN_WEEKS", 4)
    monkeypatch.setattr(config, "MARKET_CALIBRATION_SHRINKAGE", 50)
    params = {
        "goals_2_5": {
            "slope": 1.0,
            "intercept": 0.0,
            "base_rate": 0.60,
            "n_labels": 10,
            "n_weeks": 1,
        }
    }
    markets = {"goals_2_5": {"lean": "Over", "prob": 0.90}}
    out = apply_market_prob_calibration(markets, params)
    w = 10 / (10 + 50)
    expected = w * 0.90 + (1 - w) * 0.60
    assert abs(out["goals_2_5"]["prob"] - round(expected, 4)) < 1e-6
    assert out["goals_2_5"]["prob_raw"] == 0.90


def test_apply_logit_when_powered(monkeypatch):
    monkeypatch.setattr(config, "MARKET_PROB_CALIBRATION_ENABLED", True)
    monkeypatch.setattr(config, "MARKET_PROB_CALIBRATION_MIN_FIT", 10)
    monkeypatch.setattr(config, "MARKET_PROB_CALIBRATION_MIN_WEEKS", 1)
    params = {
        "goals_2_5": {
            "slope": 0.2,
            "intercept": 0.4,
            "base_rate": 0.65,
            "n_labels": 100,
            "n_weeks": 8,
        }
    }
    p_raw = 0.90
    markets = {"goals_2_5": {"lean": "Over", "prob": p_raw}}
    out = apply_market_prob_calibration(markets, params)
    expected = _sigmoid(0.2 * _logit(p_raw) + 0.4)
    assert abs(out["goals_2_5"]["prob"] - round(min(0.98, max(0.02, expected)), 4)) < 1e-6
    assert out["goals_2_5"]["prob_raw"] == 0.90


def test_fit_market_prob_calibration_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MARKET_PROB_CALIBRATION_MIN_FIT", 5)
    monkeypatch.setattr(config, "MARKET_PROB_CALIBRATION_MIN_WEEKS", 1)
    conn = connect(tmp_path / "mpc.db")
    init_db(conn)
    conn.execute(
        """
        INSERT INTO dg_snapshot (generated_at, scraped_at, payload_sha256, n_teams)
        VALUES ('2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 'x', 1)
        """
    )
    now = "2026-01-01T00:00:00+00:00"
    # Imperfect ranking so AUC is interior and Hanley–McNeil SE is defined.
    # (p, goals): mixed hits so not perfectly separable.
    rows = [
        (0.55, 0),
        (0.58, 3),
        (0.62, 0),
        (0.66, 3),
        (0.70, 0),
        (0.74, 3),
        (0.78, 3),
        (0.82, 3),
    ]
    for i, (p, fthg) in enumerate(rows):
        fid = i + 1
        conn.execute(
            """
            INSERT INTO fixture (
                fixture_id, date_utc, league, home_name, away_name, home_id, away_id,
                first_seen_at, last_seen_at
            ) VALUES (?, ?, 'Test', 'A', 'B', ?, ?, ?, ?)
            """,
            (fid, f"2026-01-{10 + i:02d}T15:00:00+00:00", 10 + i, 20 + i, now, now),
        )
        markets = {
            "goals_2_5": {
                "lean": "Over",
                "prob": p,
                "prob_raw": p,
            }
        }
        conn.execute(
            """
            INSERT INTO prediction (
                fixture_id, snapshot_id, predicted_at, model_version,
                lean, confidence, score, drivers_json, markets_json, probs_json
            ) VALUES (?, 1, ?, ?, 'Home', 'high', 0.4, '[]', ?, ?)
            """,
            (
                fid,
                now,
                _live_model_version(),
                __import__("json").dumps(markets),
                '{"home":0.6,"draw":0.25,"away":0.15}',
            ),
        )
        conn.execute(
            """
            INSERT INTO match_result (
                source, season, league_code, date, home_name, away_name,
                home_team_id, away_team_id, fthg, ftag, ftr
            ) VALUES ('football-data.co.uk', '2627', 'T1', ?, 'A', 'B', ?, ?, ?, 0, 'H')
            """,
            (f"{10 + i:02d}/01/2026", 10 + i, 20 + i, fthg),
        )
    conn.commit()
    summary = fit_market_prob_calibration(conn, model_version="test_v1")
    assert summary["fitted"] is True
    assert "goals_2_5" in summary["markets"]
    assert summary["markets"]["goals_2_5"]["n_labels"] == 8
    assert summary["markets"]["goals_2_5"]["auc_se"] is not None

    loaded = load_market_aucs(conn, model_version="test_v1")
    conn.close()
    assert "goals_2_5" in loaded
    assert loaded["goals_2_5"]["auc"] is not None
    assert loaded["goals_2_5"]["auc_se"] is not None
    assert loaded["goals_2_5"]["n_labels"] == 8
    assert loaded["goals_2_5"]["n_weeks"] >= 1


def test_samples_ignore_stale_markets_tag(tmp_path):
    """Stale markets-tag predictions must not enter the calibration sample."""
    conn = connect(tmp_path / "stale_mpc.db")
    init_db(conn)
    conn.execute(
        """
        INSERT INTO dg_snapshot (generated_at, scraped_at, payload_sha256, n_teams)
        VALUES ('2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 'x', 1)
        """
    )
    now = "2026-01-01T00:00:00+00:00"
    markets = {"goals_2_5": {"lean": "Over", "prob": 0.80, "prob_raw": 0.80}}
    for i, mv in enumerate(
        (
            "rule_v2_old+markets_v2_2671c13b04",
            _live_model_version(),
        )
    ):
        fid = i + 1
        conn.execute(
            """
            INSERT INTO fixture (
                fixture_id, date_utc, league, home_name, away_name, home_id, away_id,
                first_seen_at, last_seen_at
            ) VALUES (?, ?, 'Test', 'A', 'B', ?, ?, ?, ?)
            """,
            (fid, f"2026-02-{10 + i:02d}T15:00:00+00:00", 10 + i, 20 + i, now, now),
        )
        conn.execute(
            """
            INSERT INTO prediction (
                fixture_id, snapshot_id, predicted_at, model_version,
                lean, confidence, score, drivers_json, markets_json, probs_json
            ) VALUES (?, 1, ?, ?, 'Home', 'high', 0.4, '[]', ?, ?)
            """,
            (
                fid,
                now,
                mv,
                __import__("json").dumps(markets),
                '{"home":0.6,"draw":0.25,"away":0.15}',
            ),
        )
        conn.execute(
            """
            INSERT INTO match_result (
                source, season, league_code, date, home_name, away_name,
                home_team_id, away_team_id, fthg, ftag, ftr
            ) VALUES ('football-data.co.uk', '2627', 'T1', ?, 'A', 'B', ?, ?, 3, 0, 'H')
            """,
            (f"{10 + i:02d}/02/2026", 10 + i, 20 + i),
        )
    conn.commit()
    samples = _samples_for_market_calibration(conn)
    conn.close()
    assert "goals_2_5" in samples
    assert len(samples["goals_2_5"][0]) == 1


def test_fit_market_prob_calibration_empty_when_no_live_tag(tmp_path):
    """Cold start: no live-tag rows -> fitted False and no table rows."""
    conn = connect(tmp_path / "empty_mpc.db")
    init_db(conn)
    conn.execute(
        """
        INSERT INTO dg_snapshot (generated_at, scraped_at, payload_sha256, n_teams)
        VALUES ('2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 'x', 1)
        """
    )
    now = "2026-01-01T00:00:00+00:00"
    conn.execute(
        """
        INSERT INTO fixture (
            fixture_id, date_utc, league, home_name, away_name, home_id, away_id,
            first_seen_at, last_seen_at
        ) VALUES (1, '2026-03-01T15:00:00+00:00', 'Test', 'A', 'B', 10, 20, ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO prediction (
            fixture_id, snapshot_id, predicted_at, model_version,
            lean, confidence, score, drivers_json, markets_json, probs_json
        ) VALUES (
            1, 1, ?, 'rule_v2_old+markets_v2_2671c13b04',
            'Home', 'high', 0.4, '[]',
            '{"goals_2_5":{"lean":"Over","prob":0.8,"prob_raw":0.8}}',
            '{"home":0.6,"draw":0.25,"away":0.15}'
        )
        """,
        (now,),
    )
    conn.execute(
        """
        INSERT INTO match_result (
            source, season, league_code, date, home_name, away_name,
            home_team_id, away_team_id, fthg, ftag, ftr
        ) VALUES ('football-data.co.uk', '2627', 'T1', '01/03/2026', 'A', 'B', 10, 20, 3, 0, 'H')
        """
    )
    conn.commit()
    summary = fit_market_prob_calibration(conn, model_version="test_v1")
    n_rows = conn.execute("SELECT COUNT(*) AS c FROM market_prob_calibration").fetchone()["c"]
    conn.close()
    assert summary["fitted"] is False
    assert summary["n_markets"] == 0
    assert n_rows == 0


def test_market_pos_prob_uses_lean_side_convention():
    over = {"lean": "Over", "prob": 0.72}
    under = {"lean": "Under", "prob": 0.72}
    assert abs(_market_pos_prob(over, "Over") - 0.72) < 1e-9
    assert abs(_market_pos_prob(under, "Over") - 0.28) < 1e-9
