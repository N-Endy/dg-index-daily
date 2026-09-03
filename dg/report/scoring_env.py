"""League scoring environment: recent vs baseline goals-per-match (mean reversion)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from dg import config
from dg.report.results_attach import normalize_result_day

# Overs most exposed to a hot scoring environment.
SCORING_ENV_OVER_MARKETS = frozenset(
    {
        "goals_2_5",
        "goals_3_5",
        "btts",
        "fh_over_0_5",
    }
)
SCORING_ENV_OVER_LEANS = frozenset({"Over", "Yes"})


def _as_of_date(as_of: Optional[date] = None) -> date:
    if as_of is not None:
        return as_of
    return datetime.now(timezone.utc).date()


def _mean(xs: List[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def compute_scoring_environment(
    rows: List[Any],
    *,
    as_of: Optional[date] = None,
    recent_days: Optional[int] = None,
    baseline_days: Optional[int] = None,
    min_matches: Optional[int] = None,
    stretch_ratio: Optional[float] = None,
    stretch_delta: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Build global recent vs baseline GPM from finished match_result-like rows.

    Each row needs ``date``, ``fthg``, ``ftag`` (sqlite Row or mapping).
    """
    day0 = _as_of_date(as_of)
    recent_n = int(
        recent_days if recent_days is not None else config.SCORING_ENV_RECENT_DAYS
    )
    baseline_n = int(
        baseline_days
        if baseline_days is not None
        else config.SCORING_ENV_BASELINE_DAYS
    )
    min_n = int(
        min_matches if min_matches is not None else config.SCORING_ENV_MIN_MATCHES
    )
    ratio_thr = float(
        stretch_ratio
        if stretch_ratio is not None
        else config.SCORING_ENV_STRETCH_RATIO
    )
    delta_thr = float(
        stretch_delta
        if stretch_delta is not None
        else config.SCORING_ENV_STRETCH_DELTA
    )

    recent_goals: List[float] = []
    baseline_goals: List[float] = []
    for r in rows:
        get = r.__getitem__ if not isinstance(r, dict) else r.get
        try:
            day_s = normalize_result_day(get("date"))
        except (KeyError, TypeError, IndexError):
            continue
        if not day_s:
            continue
        try:
            d = date.fromisoformat(day_s)
        except ValueError:
            continue
        if d > day0:
            continue
        try:
            goals = float(int(get("fthg")) + int(get("ftag")))
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        age = (day0 - d).days
        if 0 <= age <= recent_n:
            recent_goals.append(goals)
        if 0 <= age <= baseline_n:
            baseline_goals.append(goals)

    gpm_recent = _mean(recent_goals)
    gpm_baseline = _mean(baseline_goals)
    n_recent = len(recent_goals)
    n_baseline = len(baseline_goals)
    powered = n_recent >= min_n and n_baseline >= min_n and gpm_baseline not in (None, 0.0)

    stretch: Optional[float] = None
    delta: Optional[float] = None
    if gpm_recent is not None and gpm_baseline is not None and gpm_baseline > 0:
        stretch = gpm_recent / gpm_baseline
        delta = gpm_recent - gpm_baseline

    stretched = bool(
        powered
        and stretch is not None
        and delta is not None
        and (stretch >= ratio_thr or delta >= delta_thr)
    )

    caution = None
    if stretched and gpm_recent is not None and gpm_baseline is not None:
        caution = (
            f"Recent scoring is hot vs baseline "
            f"({gpm_recent:.2f} GPM last {recent_n}d vs {gpm_baseline:.2f} over {baseline_n}d) "
            f"— Overs may regress."
        )

    return {
        "as_of": day0.isoformat(),
        "recent_days": recent_n,
        "baseline_days": baseline_n,
        "min_matches": min_n,
        "n_recent": n_recent,
        "n_baseline": n_baseline,
        "gpm_recent": gpm_recent,
        "gpm_baseline": gpm_baseline,
        "stretch": stretch,
        "delta": delta,
        "powered": powered,
        "stretched": stretched,
        "caution": caution,
        "over_prob_bump": float(config.SCORING_ENV_OVER_PROB_BUMP) if stretched else 0.0,
    }


def load_scoring_environment(
    conn,
    *,
    as_of: Optional[date] = None,
) -> Dict[str, Any]:
    """Load finished results and compute the global scoring-environment summary."""
    rows = conn.execute(
        """
        SELECT date, fthg, ftag
        FROM match_result
        WHERE ftr IS NOT NULL
          AND fthg IS NOT NULL
          AND ftag IS NOT NULL
        """
    ).fetchall()
    return compute_scoring_environment(rows, as_of=as_of)


def over_market_prob_bump(
    market_key: Optional[str],
    lean: Optional[str],
    scoring_env: Optional[Dict[str, Any]],
) -> float:
    """Extra Strongest min-prob when Overs face a stretched scoring environment."""
    if not scoring_env or not scoring_env.get("stretched"):
        return 0.0
    if str(market_key or "") not in SCORING_ENV_OVER_MARKETS:
        return 0.0
    if str(lean or "").strip() not in SCORING_ENV_OVER_LEANS:
        return 0.0
    return float(scoring_env.get("over_prob_bump") or config.SCORING_ENV_OVER_PROB_BUMP)
