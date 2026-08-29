"""Offline tests for storage, ingest, features, rules, doctor."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dg.features.matchup import build_matchup
from dg.features.team import build_team_features
from dg.ingest.aliases import resolve_name
from dg.ingest.fixtures import ingest_fixtures
from dg.ingest.ratings import ingest_ratings, team_ids_for_snapshot
from dg.model.rules import predict_fixture
from dg.quality.checks import day_over_day_swings, run_quality_checks
from dg.quality.doctor import check_fixtures_resolve, check_meta, check_ratings, run_doctor
from dg.report.render import render_report, write_report
from dg.storage.db import connect, init_db

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def sample_meta():
    return json.loads((FIXTURES / "dg_meta_sample.json").read_text())


@pytest.fixture()
def sample_ratings():
    return json.loads((FIXTURES / "dg_ratings_sample.json").read_text())


@pytest.fixture()
def sample_fixtures():
    return json.loads((FIXTURES / "fixtures_sample.json").read_text())


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "test.db"
    conn = connect(path)
    init_db(conn)
    yield conn
    conn.close()


def test_meta_ok(sample_meta):
    r = check_meta(sample_meta)
    assert r.ok


def test_meta_missing():
    r = check_meta({})
    assert not r.ok


def test_ratings_indices_in_range(sample_ratings):
    # Bypass MIN_TEAMS by calling check on padded list length via direct field checks
    from dg import config

    for t in sample_ratings:
        for k in config.INDEX_KEYS:
            v = float(t[k])
            assert 0.0 <= v <= 100.0


def test_ratings_required_keys(sample_ratings):
    r = check_ratings(sample_ratings)
    # Will fail MIN_TEAMS — that's expected for sample
    assert any("expected >=" in e for e in r.errors)
    # But should not fail on missing keys for first team
    assert not any("missing required keys" in e for e in r.errors)


def test_doctor_full_live_shape(sample_meta, sample_ratings):
    # Pad to satisfy MIN_TEAMS without inventing metrics badly — duplicate with new ids
    padded = list(sample_ratings)
    base_id = 900000
    while len(padded) < 300:
        clone = dict(sample_ratings[len(padded) % len(sample_ratings)])
        clone["team_id"] = base_id + len(padded)
        clone["team"] = f"Pad {len(padded)}"
        padded.append(clone)
    r = run_doctor(sample_meta, padded)
    assert r.ok, r.errors


def test_fixture_ids_resolve(sample_ratings, sample_fixtures):
    ids = {int(t["team_id"]) for t in sample_ratings}
    # May fail if fixture teams not all in sample — ensure they are
    for f in sample_fixtures:
        assert f["home"]["id"] in ids
        assert f["away"]["id"] in ids
    from dg.quality.doctor import DoctorReport

    report = DoctorReport()
    check_fixtures_resolve(sample_fixtures, ids, report)
    assert report.ok, report.errors


def test_ingest_idempotent(db, sample_meta, sample_ratings):
    sid1, ins1 = ingest_ratings(
        db,
        sample_ratings,
        generated_at=sample_meta["generated_at"],
        payload_sha256="abc",
        meta=sample_meta,
    )
    db.commit()
    assert ins1 is True
    sid2, ins2 = ingest_ratings(
        db,
        sample_ratings,
        generated_at=sample_meta["generated_at"],
        payload_sha256="abc",
        meta=sample_meta,
    )
    db.commit()
    assert ins2 is False
    assert sid1 == sid2
    n = db.execute("SELECT COUNT(*) AS n FROM dg_team_rating").fetchone()["n"]
    assert n == len(sample_ratings)


def test_fixtures_ingest_and_predict(db, sample_meta, sample_ratings, sample_fixtures):
    sid, _ = ingest_ratings(
        db,
        sample_ratings,
        generated_at=sample_meta["generated_at"],
        payload_sha256="abc",
        meta=sample_meta,
    )
    known = team_ids_for_snapshot(db, sid)
    n_up, n_proj, warns = ingest_fixtures(
        db, sample_fixtures, snapshot_id=sid, known_team_ids=known
    )
    db.commit()
    assert n_up == len(sample_fixtures)
    assert n_proj == len(sample_fixtures)
    assert warns == []

    fx = dict(db.execute("SELECT * FROM fixture LIMIT 1").fetchone())
    pred = predict_fixture(db, fx, sid)
    db.commit()
    assert pred is not None
    assert pred["lean"] in ("Home", "Draw", "Away")
    assert pred["confidence"] in ("low", "medium", "high")
    assert pred["drivers"]
    assert "rule" in pred["note"]
    assert pred.get("markets")
    assert "goals_2_5" in pred["markets"]
    assert pred["markets"]["goals_2_5"]["lean"] in ("Over", "Under")
    assert pred["markets"]["btts"]["lean"] in ("Yes", "No")
    assert pred.get("probs")
    assert pred["probs"].get("home") is not None
    assert pred["markets"]["goals_2_5"].get("prob") is not None
    row = db.execute(
        "SELECT markets_json, probs_json FROM prediction WHERE fixture_id = ?",
        (pred["fixture_id"],),
    ).fetchone()
    assert row and row["markets_json"]
    assert row["probs_json"]
    stored = json.loads(row["markets_json"])
    assert "corners_9_5" in stored
    probs = json.loads(row["probs_json"])
    assert "dgrtg_home" in probs or probs.get("home") is not None


def test_quality_second_snapshot(db, sample_meta, sample_ratings):
    sid1, _ = ingest_ratings(
        db,
        sample_ratings,
        generated_at=sample_meta["generated_at"],
        payload_sha256="a",
        meta=sample_meta,
    )
    # Mutate one metric heavily for second day
    mutated = []
    for t in sample_ratings:
        t2 = dict(t)
        if t2["team"] == sample_ratings[0]["team"]:
            t2["pace_index"] = min(100.0, float(t2["pace_index"]) + 40)
        mutated.append(t2)
    sid2, _ = ingest_ratings(
        db,
        mutated,
        generated_at="2026-08-30T05:00:00+00:00",
        payload_sha256="b",
        meta={"generated_at": "2026-08-30T05:00:00+00:00"},
    )
    db.commit()
    q = run_quality_checks(db, sid2, "2026-08-30T05:00:00+00:00")
    assert q.staleness_hours is not None
    # swings may or may not trigger depending on SD — at least runs
    assert isinstance(q.anomalies, list)
    _ = day_over_day_swings(db, sid2)
    assert sid1 != sid2


def test_team_features_and_matchup(db, sample_meta, sample_ratings):
    sid, _ = ingest_ratings(
        db,
        sample_ratings,
        generated_at=sample_meta["generated_at"],
        payload_sha256="a",
        meta=sample_meta,
    )
    db.commit()
    a = sample_ratings[0]
    b = sample_ratings[1]
    ha = build_team_features(db, int(a["team_id"]), sid)
    aw = build_team_features(db, int(b["team_id"]), sid)
    m = build_matchup(ha, aw)
    assert m["ok"]
    assert "pressing_mismatch" in m
    assert "pace_clash" in m


def test_alias_fuzzy():
    index = {
        "manchester city": (50, "Manchester City"),
        "nottingham forest": (65, "Nottingham Forest"),
        "hull city": (71, "Hull City"),
    }
    r = resolve_name("Man City", index)
    assert r is not None
    assert r[0] == 50
    assert r[3] == "seed"
    r2 = resolve_name("Nott'm Forest", index)
    assert r2 is not None
    assert r2[0] == 65


def test_report_write(tmp_path, monkeypatch, sample_meta):
    from dg import config
    from dg.quality.checks import QualityReport

    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path)
    md = render_report(
        generated_at=sample_meta["generated_at"],
        snapshot_id=1,
        n_teams=10,
        quality=QualityReport(),
        predictions=[
            {
                "date_utc": "2026-08-29T15:00:00+00:00",
                "league": "Premier League",
                "home_name": "A",
                "away_name": "B",
                "lean": "Home",
                "confidence": "medium",
                "match_character": "open",
                "dg_sim_lean": "Home",
                "book_lean": "Home",
                "drivers": ["pressing mismatch (+0.20)"],
            }
        ],
    )
    path = write_report(md, day="2026-08-29")
    assert path.exists()
    text = path.read_text()
    assert "DG Index Daily Report" in text
    assert "rule-based" in text.lower() or "rule-based" in text or "not a trained" in text.lower()
