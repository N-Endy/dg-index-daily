"""Backtest-derived per-market reliability for Strongest ranking and AI publish estimates."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from dg import config

# Probability bands used for calibration buckets.
PROB_BAND_ORDER: Tuple[str, ...] = ("lt_75", "75_85", "85_92", "92_plus")
PROB_BAND_LABELS: Dict[str, str] = {
    "lt_75": "under 75%",
    "75_85": "75–85%",
    "85_92": "85–92%",
    "92_plus": "92%+",
    "no_prob": "no stated prob",
    "all": "all probs",
}
AGREEMENT_TIERS: Tuple[str, ...] = ("agree2", "agree1", "split", "none")
AGREEMENT_TIER_LABELS: Dict[str, str] = {
    "agree2": "both sources agree",
    "agree1": "single source agrees",
    "split": "sources split",
    "none": "no source compare",
    "all": "all agreement",
}
MARKET_LABELS: Dict[str, str] = {
    "goals_2_5": "goals 2.5",
    "goals_3_5": "goals 3.5",
    "btts": "BTTS",
    "match_1x2": "match winner",
    "fh_over_0_5": "FH 0.5",
    "fh_1x2": "FH 1X2",
    "team_goals_home_1_5": "home goals 1.5",
    "team_goals_away_1_5": "away goals 1.5",
    "corners_9_5": "corners 9.5",
    "shots_25_5": "shots 25.5",
    "sot_8_5": "SOT 8.5",
    "cards_3_5": "cards 3.5",
    "all": "all markets",
}

# Calibration key: (market_key, agreement_tier, prob_band)
CalibKey = Tuple[str, str, str]


def prob_band_for(prob: Any) -> Optional[str]:
    """Map a lean probability to a calibration band key. None → no_prob caller side."""
    try:
        p = float(prob)
    except (TypeError, ValueError):
        return None
    if p < 0.75:
        return "lt_75"
    if p < 0.85:
        return "75_85"
    if p < 0.92:
        return "85_92"
    return "92_plus"


def agreement_tier_from_market(m: Dict[str, Any]) -> str:
    """Derive agreement tier from a market dict's lean / dg_lean / book_lean."""
    lean = str(m.get("lean") or "").strip()
    if not lean:
        return "none"
    srcs = [s for s in (m.get("dg_lean"), m.get("book_lean")) if s]
    if not srcs:
        return "none"
    if all(str(s).strip() == lean for s in srcs):
        return "agree2" if len(srcs) == 2 else "agree1"
    return "split"


def agreement_tier_from_candidate(cand: Dict[str, Any]) -> str:
    """
    Derive agreement tier from Strongest candidate fields.
    Prefers agreement_key + agreement_n_sources; falls back to lean sources.
    """
    key = str(cand.get("agreement_key") or "").lower()
    try:
        n = int(cand.get("agreement_n_sources") or 0)
    except (TypeError, ValueError):
        n = 0
    if key == "aligned":
        if n >= 2:
            return "agree2"
        if n == 1:
            return "agree1"
        # Aligned with unknown n — inspect sources list if present.
        sources = cand.get("agreement_sources") or []
        if isinstance(sources, list) and len(sources) >= 2:
            return "agree2"
        if isinstance(sources, list) and len(sources) == 1:
            return "agree1"
        return "agree2"
    if key == "partial":
        return "agree1"
    if key == "split":
        return "split"
    if key in ("unknown", ""):
        # Fall back to lean fields when available.
        return agreement_tier_from_market(
            {
                "lean": cand.get("lean"),
                "dg_lean": cand.get("dg_lean") or cand.get("dg_sim_lean"),
                "book_lean": cand.get("book_lean"),
            }
        )
    return "none"


def market_label_plain(market_key: str) -> str:
    return MARKET_LABELS.get(market_key, market_key.replace("_", " "))


def band_label_plain(band: str) -> str:
    return PROB_BAND_LABELS.get(band, band)


def agreement_tier_label_plain(tier: str) -> str:
    return AGREEMENT_TIER_LABELS.get(tier, tier)


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


def finalize_calibration_rows(
    raw: Dict[CalibKey, List[int]],
) -> List[Dict[str, Any]]:
    """
    Expand per-(market, tier, band) hit counters into the shrinkage parent chain:

    - leaf: (market, tier, band) including no_prob
    - market+tier: (market, tier, all)
    - tier: (all, tier, all)
    - global: (all, all, all)

    raw values are [hits, n_graded].
    """
    leaves: Dict[CalibKey, List[int]] = {
        k: [int(v[0]), int(v[1])] for k, v in raw.items() if v[1] > 0
    }
    market_tier: Dict[Tuple[str, str], List[int]] = {}
    tier_totals: Dict[str, List[int]] = {}
    grand = [0, 0]

    for (mkey, tier, band), (hits, n) in leaves.items():
        mt = market_tier.setdefault((mkey, tier), [0, 0])
        mt[0] += hits
        mt[1] += n
        tt = tier_totals.setdefault(tier, [0, 0])
        tt[0] += hits
        tt[1] += n
        grand[0] += hits
        grand[1] += n

    rows: List[Dict[str, Any]] = []

    def _row(mkey: str, tier: str, band: str, hits: int, n: int) -> Dict[str, Any]:
        return {
            "market_key": mkey,
            "agreement_tier": tier,
            "prob_band": band,
            "hits": hits,
            "n_graded": n,
            "hit_rate": (hits / n) if n else 0.0,
        }

    for (mkey, tier, band), (hits, n) in sorted(leaves.items()):
        rows.append(_row(mkey, tier, band, hits, n))
    for (mkey, tier), (hits, n) in sorted(market_tier.items()):
        rows.append(_row(mkey, tier, "all", hits, n))
    for tier, (hits, n) in sorted(tier_totals.items()):
        rows.append(_row("all", tier, "all", hits, n))
    if grand[1]:
        rows.append(_row("all", "all", "all", grand[0], grand[1]))
    return rows


def store_market_calibration(conn, summary: Dict[str, Any]) -> int:
    """Replace market_calibration rows from evaluate_joined summary['calibration']."""
    rows = list(summary.get("calibration") or [])
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("DELETE FROM market_calibration")
    n = 0
    for row in rows:
        mkey = str(row.get("market_key") or "")
        tier = str(row.get("agreement_tier") or "all")
        band = str(row.get("prob_band") or "")
        if not mkey or not band:
            continue
        n_graded = int(row.get("n_graded") or 0)
        hits = int(row.get("hits") or 0)
        if n_graded <= 0:
            continue
        hit_rate = float(
            row.get("hit_rate") if row.get("hit_rate") is not None else hits / n_graded
        )
        conn.execute(
            """
            INSERT INTO market_calibration (
                market_key, agreement_tier, prob_band, n_graded, hits, hit_rate, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (mkey, tier, band, n_graded, hits, hit_rate, now),
        )
        n += 1
    conn.commit()
    return n


def load_market_calibration(conn) -> Dict[CalibKey, Dict[str, Any]]:
    """
    Load calibration table as (market_key, agreement_tier, prob_band) -> stats.
    No running-max correction — rates stay empirical; shrinkage handles thin buckets.
    """
    try:
        rows = conn.execute(
            """
            SELECT market_key, agreement_tier, prob_band, n_graded, hits, hit_rate
            FROM market_calibration
            """
        ).fetchall()
    except Exception:
        return {}

    out: Dict[CalibKey, Dict[str, Any]] = {}
    for r in rows:
        d = dict(r)
        key = (
            str(d["market_key"]),
            str(d.get("agreement_tier") or "all"),
            str(d["prob_band"]),
        )
        out[key] = {
            "hit_rate": float(d["hit_rate"]),
            "n_graded": int(d["n_graded"]),
            "hits": int(d["hits"]),
        }
    return out


def _shrink(hits: int, n: int, parent_rate: float, k: float) -> float:
    """Empirical-Bayes shrink toward parent_rate. n=0 returns parent_rate exactly."""
    if n <= 0:
        return float(parent_rate)
    return (float(hits) + float(k) * float(parent_rate)) / (float(n) + float(k))


def reliability_for(
    calibration: Optional[Dict[CalibKey, Dict[str, Any]]],
    market_key: Any,
    agreement_tier: Any,
    prob: Any = None,
) -> Dict[str, Any]:
    """
    Resolve a base hit rate via empirical-Bayes shrinkage:

    global → (tier) → (market, tier) → (market, tier, band)

    Band ``no_prob`` is never matched directly (only contributes to parents).
    """
    k = float(getattr(config, "MARKET_CALIBRATION_SHRINKAGE", 50))
    default_rate = float(config.MARKET_CALIBRATION_DEFAULT_RATE)
    mkey = str(market_key or "")
    tier = str(agreement_tier or "none")
    if tier not in AGREEMENT_TIERS:
        tier = "none"
    band = prob_band_for(prob)  # None when missing — skip leaf
    calib = calibration or {}

    def _entry(key: CalibKey) -> Dict[str, Any]:
        return calib.get(key) or {"hit_rate": 0.0, "n_graded": 0, "hits": 0}

    global_e = _entry(("all", "all", "all"))
    global_rate = (
        float(global_e["hit_rate"]) if int(global_e["n_graded"] or 0) > 0 else default_rate
    )
    rate = global_rate
    source = "global"
    matched_key: CalibKey = ("all", "all", "all")
    leaf_n = 0
    leaf_hits = 0
    leaf_raw = global_rate

    # Tier parent
    tier_e = _entry(("all", tier, "all"))
    rate = _shrink(
        int(tier_e.get("hits") or 0),
        int(tier_e.get("n_graded") or 0),
        rate,
        k,
    )
    if int(tier_e.get("n_graded") or 0) > 0:
        source = "tier"
        matched_key = ("all", tier, "all")
        leaf_n = int(tier_e["n_graded"])
        leaf_hits = int(tier_e.get("hits") or 0)
        leaf_raw = float(tier_e["hit_rate"])

    # Market + tier
    if mkey:
        mt_e = _entry((mkey, tier, "all"))
        rate = _shrink(
            int(mt_e.get("hits") or 0),
            int(mt_e.get("n_graded") or 0),
            rate,
            k,
        )
        if int(mt_e.get("n_graded") or 0) > 0:
            source = "market_tier"
            matched_key = (mkey, tier, "all")
            leaf_n = int(mt_e["n_graded"])
            leaf_hits = int(mt_e.get("hits") or 0)
            leaf_raw = float(mt_e["hit_rate"])

    # Market + tier + band (never match no_prob)
    if mkey and band:
        mtb_e = _entry((mkey, tier, band))
        rate = _shrink(
            int(mtb_e.get("hits") or 0),
            int(mtb_e.get("n_graded") or 0),
            rate,
            k,
        )
        if int(mtb_e.get("n_graded") or 0) > 0:
            source = "market_tier_band"
            matched_key = (mkey, tier, band)
            leaf_n = int(mtb_e["n_graded"])
            leaf_hits = int(mtb_e.get("hits") or 0)
            leaf_raw = float(mtb_e["hit_rate"])

    return {
        "rate": float(rate),
        "n": leaf_n,
        "hits": leaf_hits,
        "raw_rate": float(leaf_raw),
        "source": source,
        "market_key": matched_key[0],
        "agreement_tier": matched_key[1],
        "prob_band": matched_key[2],
        "monotonic_corrected": False,
    }
