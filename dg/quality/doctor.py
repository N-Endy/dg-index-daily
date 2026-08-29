"""Contract checks against live (or provided) DataGaffer payloads."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from dg import config


@dataclass
class DoctorReport:
    ok: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def fail(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def check_meta(meta: Dict[str, Any], report: Optional[DoctorReport] = None) -> DoctorReport:
    report = report or DoctorReport()
    if not isinstance(meta, dict):
        report.fail("dg_meta.json is not an object")
        return report
    if "generated_at" not in meta or not meta["generated_at"]:
        report.fail("dg_meta.json missing generated_at")
    return report


def check_ratings(
    teams: List[Dict[str, Any]],
    report: Optional[DoctorReport] = None,
) -> DoctorReport:
    report = report or DoctorReport()
    if not isinstance(teams, list):
        report.fail("dg_ratings.json is not a list")
        return report
    if len(teams) < config.MIN_TEAMS:
        report.fail(f"expected >= {config.MIN_TEAMS} teams, got {len(teams)}")
    if not teams:
        return report

    sample = teams[0]
    missing = [k for k in config.REQUIRED_RATING_KEYS if k not in sample]
    if missing:
        report.fail(f"dg_ratings missing required keys: {missing}")

    strength_missing = [k for k in config.RATING_STRENGTH_KEYS if k not in sample]
    if strength_missing:
        report.warn(f"dg_ratings missing strength keys (warn-only): {strength_missing}")

    for i, t in enumerate(teams):
        for key in config.INDEX_KEYS:
            val = t.get(key)
            if val is None:
                report.fail(f"team[{i}] ({t.get('team')}) missing {key}")
                break
            try:
                fval = float(val)
            except (TypeError, ValueError):
                report.fail(f"team[{i}] ({t.get('team')}) non-numeric {key}={val!r}")
                break
            if not (0.0 <= fval <= 100.0):
                report.fail(
                    f"team[{i}] ({t.get('team')}) {key}={fval} outside 0–100"
                )
                break
        if len(report.errors) > 20:
            report.warn("truncated further rating checks after 20 errors")
            break
    return report


def check_fixtures_resolve(
    fixtures: List[Dict[str, Any]],
    team_ids: Set[int],
    report: Optional[DoctorReport] = None,
) -> DoctorReport:
    report = report or DoctorReport()
    unresolved = []
    for fx in fixtures:
        home = fx.get("home") or {}
        away = fx.get("away") or {}
        hid = home.get("id") or fx.get("home_id")
        aid = away.get("id") or fx.get("away_id")
        try:
            hid_i, aid_i = int(hid), int(aid)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            unresolved.append(f"bad ids in fixture {fx.get('fixture_id')}")
            continue
        if hid_i not in team_ids:
            unresolved.append(f"home {hid_i} ({home.get('name')})")
        if aid_i not in team_ids:
            unresolved.append(f"away {aid_i} ({away.get('name')})")
    if unresolved:
        # Fail the contract — plan says every fixture team ID must resolve
        report.fail(
            f"{len(unresolved)} unresolved fixture team ids (sample): "
            + "; ".join(unresolved[:10])
        )
    return report


def run_doctor(
    meta: Dict[str, Any],
    teams: List[Dict[str, Any]],
    fixtures: Optional[List[Dict[str, Any]]] = None,
) -> DoctorReport:
    report = DoctorReport()
    check_meta(meta, report)
    check_ratings(teams, report)
    if fixtures is not None:
        team_ids = {int(t["team_id"]) for t in teams if "team_id" in t}
        check_fixtures_resolve(fixtures, team_ids, report)
    return report
