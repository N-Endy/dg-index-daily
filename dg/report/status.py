"""Operational status for the web UI."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from dg import config
from dg.quality.checks import staleness_hours
from dg.report.loaders import format_generated_at, get_connection, today_wat
from dg.storage.db import latest_snapshot


def load_status_context() -> Dict[str, Any]:
    conn = get_connection()
    try:
        snap = latest_snapshot(conn)
        generated_at = snap["generated_at"] if snap else None
        age = staleness_hours(generated_at) if generated_at else None
        stale = bool(age is not None and age == age and age > config.STALE_HOURS_THRESHOLD)

        run = conn.execute(
            "SELECT * FROM run_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        run_info: Optional[Dict[str, Any]] = None
        if run:
            stages = {}
            try:
                stages = json.loads(run["stages_json"] or "{}")
            except json.JSONDecodeError:
                stages = {}
            run_info = {
                "started_at": run["started_at"],
                "finished_at": run["finished_at"],
                "status": run["status"],
                "exit_code": run["exit_code"],
                "message": run["message"],
                "stages": stages,
            }

        score_sources = conn.execute(
            """
            SELECT source, COUNT(*) AS n
            FROM match_result
            WHERE ftr IS NOT NULL
            GROUP BY source
            ORDER BY n DESC
            """
        ).fetchall()

        flash_last = conn.execute(
            "SELECT MAX(scraped_at) AS t FROM flashscore_row"
        ).fetchone()

        day = today_wat()
        ai_count = conn.execute(
            "SELECT COUNT(*) AS n FROM ai_pick WHERE day = ?", (day,)
        ).fetchone()

        from dg.report.selection_audit import selection_regret_audit
        from dg.report.scoreboard import recent_ai_performance, recent_strongest_performance

        selection_audit = selection_regret_audit(conn)
        strongest_scoreboard = recent_strongest_performance(conn)
        ai_scoreboard = recent_ai_performance(conn)

        n_fixtures = conn.execute("SELECT COUNT(*) AS n FROM fixture").fetchone()
        n_predictions = conn.execute("SELECT COUNT(*) AS n FROM prediction").fetchone()

        return {
            "generated_at": generated_at,
            "generated_at_display": format_generated_at(generated_at),
            "staleness_hours": age,
            "stale": stale,
            "snapshot_id": int(snap["id"]) if snap else None,
            "n_teams": int(snap["n_teams"]) if snap else 0,
            "last_run": run_info,
            "score_sources": [dict(r) for r in score_sources],
            "flashscore_last_scrape": flash_last["t"] if flash_last else None,
            "ai_picks_today": int(ai_count["n"]) if ai_count else 0,
            "has_openai_key": bool(config.OPENAI_API_KEY),
            "selection_audit": selection_audit,
            "strongest_scoreboard": strongest_scoreboard,
            "ai_scoreboard": ai_scoreboard,
            "n_fixtures": int(n_fixtures["n"]) if n_fixtures else 0,
            "n_predictions": int(n_predictions["n"]) if n_predictions else 0,
            "today_wat": day,
        }
    finally:
        conn.close()
