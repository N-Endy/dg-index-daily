"""Load AI Picks for the web UI."""
from __future__ import annotations

from typing import Any, Dict, Optional

from dg import config
from dg.ai.vet_strongest import load_ai_picks
from dg.report.loaders import (
    _fixture_sort_key,
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
    # Reuse dashboard meta (stale banner, generated_at_display) for the day filter
    ctx = load_dashboard_context(date_filter=day_key)
    has_key = bool(config.OPENAI_API_KEY)

    conn = get_connection()
    try:
        picks = load_ai_picks(conn, day_key)
    finally:
        conn.close()

    picks.sort(key=_fixture_sort_key)

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
            "None of today’s Strongest leans cleared the AI bar "
            f"(approve + score ≥ {config.AI_VET_MIN_SCORE}), or vetting has not run yet."
        )

    return {
        **ctx,
        "day": day_key,
        "picks": picks,
        "n_picks": len(picks),
        "has_openai_key": has_key,
        "ai_min_score": config.AI_VET_MIN_SCORE,
        "ai_model": config.OPENAI_MODEL,
        "page_empty": empty_db or not picks,
        "message": message,
    }
