"""Data quality: roster diffs, day-over-day swings, staleness."""
from __future__ import annotations

import json
import logging
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dg import config
from dg.storage.db import previous_snapshot

logger = logging.getLogger(__name__)


@dataclass
class QualityReport:
    new_teams: List[str] = field(default_factory=list)
    missing_teams: List[str] = field(default_factory=list)
    anomalies: List[Dict[str, Any]] = field(default_factory=list)
    staleness_hours: Optional[float] = None
    warnings: List[str] = field(default_factory=list)


def _team_rows(conn, snapshot_id: int) -> Dict[int, Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM dg_team_rating WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchall()
    return {int(r["team_id"]): dict(r) for r in rows}


def roster_diff(conn, snapshot_id: int) -> QualityReport:
    report = QualityReport()
    prev = previous_snapshot(conn, snapshot_id)
    if prev is None:
        report.warnings.append("No previous snapshot — skipping roster diff")
        return report

    cur = _team_rows(conn, snapshot_id)
    old = _team_rows(conn, int(prev["id"]))
    new_ids = set(cur) - set(old)
    missing_ids = set(old) - set(cur)
    report.new_teams = sorted(cur[i]["team"] for i in new_ids)
    report.missing_teams = sorted(old[i]["team"] for i in missing_ids)
    return report


def day_over_day_swings(conn, snapshot_id: int) -> List[Dict[str, Any]]:
    """Flag teams whose index changed by > 2 SD of that metric's day-over-day deltas."""
    prev = previous_snapshot(conn, snapshot_id)
    if prev is None:
        return []

    cur = _team_rows(conn, snapshot_id)
    old = _team_rows(conn, int(prev["id"]))
    common = set(cur) & set(old)

    deltas: Dict[str, List[float]] = {k: [] for k in config.INDEX_KEYS}
    per_team: Dict[int, Dict[str, float]] = {}

    for tid in common:
        per_team[tid] = {}
        for k in config.INDEX_KEYS:
            a, b = cur[tid].get(k), old[tid].get(k)
            if a is None or b is None:
                continue
            d = float(a) - float(b)
            deltas[k].append(d)
            per_team[tid][k] = d

    thresholds: Dict[str, float] = {}
    for k, vals in deltas.items():
        if len(vals) < 5:
            continue
        sd = statistics.pstdev(vals)
        if sd == 0 or math.isnan(sd):
            continue
        thresholds[k] = config.ANOMALY_Z_THRESHOLD * sd

    anomalies: List[Dict[str, Any]] = []
    for tid, dmap in per_team.items():
        for k, d in dmap.items():
            thr = thresholds.get(k)
            if thr is None:
                continue
            if abs(d) > thr:
                anomalies.append(
                    {
                        "team_id": tid,
                        "team": cur[tid]["team"],
                        "metric": k,
                        "delta": round(d, 2),
                        "threshold": round(thr, 2),
                        "from": old[tid].get(k),
                        "to": cur[tid].get(k),
                    }
                )
    return anomalies


def persist_anomalies(conn, snapshot_id: int, anomalies: List[Dict[str, Any]]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for a in anomalies:
        conn.execute(
            """
            INSERT INTO ingest_anomaly
                (snapshot_id, created_at, kind, team_id, team, metric, detail)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                now,
                "swing",
                a.get("team_id"),
                a.get("team"),
                a.get("metric"),
                json.dumps(a),
            ),
        )


def staleness_hours(generated_at: str) -> float:
    try:
        ts = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        return float("nan")
    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds() / 3600.0


def run_quality_checks(conn, snapshot_id: int, generated_at: str) -> QualityReport:
    report = roster_diff(conn, snapshot_id)
    report.anomalies = day_over_day_swings(conn, snapshot_id)
    persist_anomalies(conn, snapshot_id, report.anomalies)
    report.staleness_hours = staleness_hours(generated_at)
    if report.staleness_hours == report.staleness_hours and report.staleness_hours > 48:
        report.warnings.append(
            f"DG ratings appear stale: generated_at is {report.staleness_hours:.1f}h old"
        )
    if report.new_teams:
        report.warnings.append(f"New teams: {', '.join(report.new_teams[:20])}")
    if report.missing_teams:
        report.warnings.append(f"Missing vs prior: {', '.join(report.missing_teams[:20])}")
    if report.anomalies:
        report.warnings.append(f"{len(report.anomalies)} day-over-day swings flagged")
    logger.info(
        "Quality: +%d -%d teams, %d anomalies, staleness=%.1fh",
        len(report.new_teams),
        len(report.missing_teams),
        len(report.anomalies),
        report.staleness_hours or -1,
    )
    return report
