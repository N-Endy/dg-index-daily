"""Backtest-derived per-market reliability for Strongest ranking tie-breaks."""
from __future__ import annotations

from typing import Any, Dict

from dg import config


def market_hit_rates_from_backtest(conn) -> Dict[str, float]:
    """
    Return market_key -> rule hit_rate from evaluate_joined when enough labels exist.
    Used when STRONGEST_USE_MARKET_HIT_RATES=1.
    """
    from dg.model.evaluate import evaluate_joined

    summary: Dict[str, Any] = evaluate_joined(conn)
    min_graded = config.STRONGEST_MARKET_HIT_MIN_GRADED
    out: Dict[str, float] = {}
    for mkey, entry in (summary.get("markets") or {}).items():
        rule = entry.get("rule") or {}
        hr = rule.get("hit_rate")
        n_graded = int(rule.get("n_graded") or 0)
        if hr is not None and n_graded >= min_graded:
            out[str(mkey)] = float(hr)
    return out
