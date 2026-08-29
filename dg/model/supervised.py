"""Supervised model stub — inactive until SUPERVISED_MIN_LABELS met."""
from __future__ import annotations

import logging
from typing import Any, Dict

from dg import config

logger = logging.getLogger(__name__)


def labelled_count(conn) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM match_result
        WHERE ftr IS NOT NULL AND home_team_id IS NOT NULL AND away_team_id IS NOT NULL
        """
    ).fetchone()
    return int(row["n"]) if row else 0


def train_if_ready(conn) -> Dict[str, Any]:
    n = labelled_count(conn)
    if n < config.SUPERVISED_MIN_LABELS:
        msg = (
            f"Supervised training gated: {n}/{config.SUPERVISED_MIN_LABELS} labelled matches"
        )
        logger.info(msg)
        return {"trained": False, "n_labels": n, "message": msg}
    # Placeholder for future logistic regression / gradient boosting
    return {
        "trained": False,
        "n_labels": n,
        "message": (
            f"{n} labels available — supervised trainer not yet implemented; "
            "use rule_v1 until explicitly enabled"
        ),
    }
