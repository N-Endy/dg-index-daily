"""Load AI Picks for the web UI."""
from __future__ import annotations

from typing import Any, Dict, Optional

from dg import config
from dg.ai.vet_strongest import load_ai_picks, load_board_note
from dg.report.loaders import (
    get_connection,
    load_dashboard_context,
    today_wat,
)


def load_ai_picks_page(*, day: Optional[str] = None) -> Dict[str, Any]:
    """
    Context for /ai-picks.
    Uses today's WAT day by default; shows stored approvals from ai_pick.
    """
    day_key = day or today_wat()
    ctx = load_dashboard_context(date_filter=day_key)
    has_key = bool(config.OPENAI_API_KEY)

    conn = get_connection()
    try:
        picks = load_ai_picks(conn, day_key)
        board_note = load_board_note(conn, day_key)
        from dg.report.scoreboard import recent_ai_performance, recent_strongest_performance
        from dg.report.scoring_env import load_scoring_environment

        ai_scoreboard = recent_ai_performance(conn)
        strongest_scoreboard = recent_strongest_performance(conn)
        scoring_env = load_scoring_environment(conn)
    finally:
        conn.close()

    # Highest publish confidence first; kickoff breaks ties within a score.
    picks.sort(
        key=lambda p: (
            -int(p.get("ai_score") or 0),
            p.get("date_utc") or "",
            (p.get("league_display") or p.get("league") or "").lower(),
        )
    )

    empty_db = bool(ctx.get("empty"))
    message = None
    if empty_db:
        message = ctx.get("message") or "No data yet. The daily refresh has not run."
    elif not picks and not has_key:
        message = (
            "AI Picks need OPENAI_API_KEY on the server. "
            "Add it in Railway Variables, then re-run the daily job (or vet-ai-picks)."
        )
    elif not picks:
        message = (
            "None of today’s signal-based candidates cleared the AI bar "
            f"(estimated chance ≥ {config.AI_VET_MIN_SCORE}%), or vetting has not run yet."
        )

    return {
        **ctx,
        "day": day_key,
        "picks": picks,
        "n_picks": len(picks),
        "has_openai_key": has_key,
        "ai_min_score": config.AI_VET_MIN_SCORE,
        "ai_model": config.OPENAI_MODEL,
        "ai_top_n": config.AI_VET_MAX_CANDIDATES,
        "board_note": board_note,
        "page_empty": empty_db or not picks,
        "message": message,
        "ai_scoreboard": ai_scoreboard,
        "strongest_scoreboard": strongest_scoreboard,
        "scoring_env": scoring_env,
    }
