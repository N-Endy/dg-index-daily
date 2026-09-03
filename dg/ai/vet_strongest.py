"""Screen Strongest leans with an LLM and persist approved AI picks."""
from __future__ import annotations

import json
import logging
import random
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from dg import config
from dg.ai.openai_client import OpenAIError, chat_json
from dg.report.best_leans import (
    build_ai_vet_fixture_groups,
    build_strongest_picks,
    flatten_vet_groups,
    get_market_hit_rates,
    load_strongest_day,
)

logger = logging.getLogger(__name__)

# Judgment multiplier bounds — coherence/concerns only (agreement is in the base rate).
JUDGMENT_BASE = 0.90
JUDGMENT_PER_COHERENCE = 0.04
JUDGMENT_CONCERN_PENALTY = 0.04
JUDGMENT_MIN = 0.85
JUDGMENT_MAX = 1.05
PUBLISH_CAP = 0.95
ECHO_DELTA = 3

SYSTEM_PROMPT = (
    "You are a conservative football analyst reviewing pre-filtered directional leans "
    "from a rule-based ratings model (DataGaffer DG Index). "
    "For each fixture you may see multiple market candidates that already passed hard gates. "
    "Pick at most ONE candidate per fixture to publish (or none). "
    "Judge ONLY the provided fields (probability, confidence, DG/book agreement, drivers, style). "
    "Do not invent stats, injuries, lineups, or odds. "
    "Do NOT return a numeric 0-100 score — estimated publish chance is computed downstream from "
    "measured market hit rates (keyed by source agreement) plus your coherence judgment. "
    "For the chosen candidate set: "
    "verdict='publish' or 'skip'; "
    "coherence 0-3 (do drivers and match style support this market and direction); "
    "concerns as a short string list of red flags (each will reduce the estimate). "
    "Respond with JSON only: "
    '{"picks":[{"fixtureId":123,"marketKey":"goals_2_5","verdict":"publish",'
    '"coherence":0-3,'
    '"concerns":["optional flag"],"reason":"one short plain sentence"}]}.'
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp_component(value: Any, *, default: int = 0) -> int:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(0, min(3, n))


def _clamp_float(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def judgment_multiplier(
    *,
    coherence: int = 0,
    concerns: Optional[List[str]] = None,
    flat_score: Optional[int] = None,
    agreement: int = 0,  # ignored; agreement is in the base rate
) -> float:
    """
    Map AI screen quality to a tight multiplier around the measured base hit rate.
    Agreement is handled by the calibration tier, not this multiplier.
    Flat legacy scores (0–100) map onto the same 0.85–1.05 band.
    """
    _ = agreement
    if flat_score is not None:
        try:
            s = float(flat_score)
        except (TypeError, ValueError):
            s = 0.0
        s = max(0.0, min(100.0, s))
        return _clamp_float(
            JUDGMENT_MIN + (s / 100.0) * (JUDGMENT_MAX - JUDGMENT_MIN),
            JUDGMENT_MIN,
            JUDGMENT_MAX,
        )
    n_concerns = len(concerns or [])
    raw = (
        JUDGMENT_BASE
        + JUDGMENT_PER_COHERENCE * int(coherence)
        - JUDGMENT_CONCERN_PENALTY * n_concerns
    )
    return _clamp_float(raw, JUDGMENT_MIN, JUDGMENT_MAX)


def compute_publish_score(
    *,
    base_rate: float,
    coherence: int = 0,
    concerns: Optional[List[str]] = None,
    flat_score: Optional[int] = None,
    agreement: int = 0,  # ignored
    market_trust: int = 0,  # ignored
) -> int:
    """
    Estimated chance the lean lands (0–100): measured base_rate × judgment, capped.
    """
    _ = market_trust
    judgment = judgment_multiplier(
        coherence=coherence,
        concerns=concerns,
        flat_score=flat_score,
        agreement=agreement,
    )
    try:
        rate = float(base_rate)
    except (TypeError, ValueError):
        rate = float(config.MARKET_CALIBRATION_DEFAULT_RATE)
    return max(0, min(100, int(round(100.0 * min(rate * judgment, PUBLISH_CAP)))))


# Back-compat alias used by older tests that still call model_strength_band.
def model_strength_band(prob: Any) -> float:
    try:
        p = float(prob)
    except (TypeError, ValueError):
        return 0.5
    if p >= 0.85:
        return 1.0
    if p >= 0.75:
        return 0.75
    if p >= 0.65:
        return 0.5
    return 0.25


def candidate_payload(pick: Dict[str, Any]) -> Dict[str, Any]:
    why = list(pick.get("why") or [])[:4]
    return {
        "fixtureId": pick.get("fixture_id"),
        "marketKey": pick.get("market_key"),
        "league": pick.get("league"),
        "league_display": pick.get("league_display"),
        "homeTeam": pick.get("home_name"),
        "awayTeam": pick.get("away_name"),
        "kickoff": pick.get("kickoff_display") or pick.get("date_utc"),
        "market": pick.get("market_label") or pick.get("market_key"),
        "lean": pick.get("lean"),
        "leanPlain": pick.get("lean_plain"),
        "confidence": pick.get("confidence"),
        "prob": pick.get("prob"),
        "agreement": pick.get("agreement_label") or pick.get("agreement_key"),
        "dgLean": pick.get("dg_lean"),
        "bookLean": pick.get("book_lean"),
        "strength": pick.get("strength_label"),
        "style": pick.get("style_label"),
        "why": why,
    }


def fixture_group_payload(group: Dict[str, Any]) -> Dict[str, Any]:
    """Build fixture JSON; shuffle candidates with a fixture-seeded RNG."""
    candidates = list(group.get("candidates") or [])
    fid = group.get("fixture_id")
    try:
        seed = int(fid) if fid is not None else 0
    except (TypeError, ValueError):
        seed = 0
    rng = random.Random(seed)
    shuffled = list(candidates)
    rng.shuffle(shuffled)
    return {
        "fixtureId": group.get("fixture_id"),
        "homeTeam": group.get("home_name"),
        "awayTeam": group.get("away_name"),
        "league": group.get("league_display") or group.get("league"),
        "kickoff": group.get("kickoff_display") or group.get("date_utc"),
        "candidates": [candidate_payload(c) for c in shuffled],
    }


def _extract_json_object(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _parse_concerns(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        return [text] if text else []
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text:
            out.append(text[:120])
    return out[:8]


def _parse_verdict_approve(row: Dict[str, Any]) -> Tuple[bool, bool]:
    """
    Return (approve, used_flat_score_path hint for score).
    Component path uses verdict; flat path uses approve.
    """
    verdict = row.get("verdict", row.get("Verdict"))
    if verdict is not None:
        if isinstance(verdict, str):
            return verdict.strip().lower() in ("publish", "approve", "approved", "yes"), False
        return bool(verdict), False
    approve = row.get("approve", row.get("Approve", row.get("passed")))
    if isinstance(approve, str):
        return approve.strip().lower() in ("1", "true", "yes", "y"), True
    if approve is None:
        return False, True
    return bool(approve), True


def parse_screen_response(raw: str) -> List[Dict[str, Any]]:
    """
    Parse LLM JSON into score rows.

    Preferred shape: coherence (+ optional legacy agreement) with verdict.
    Fallback: flat score 0–100 + approve (legacy) — remapped onto judgment later.
    """
    data = _extract_json_object(raw)
    rows = (
        data.get("picks")
        or data.get("Picks")
        or data.get("scores")
        or data.get("Scores")
        or []
    )
    if not isinstance(rows, list):
        return []
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        fid = row.get("fixtureId", row.get("fixture_id", row.get("PredictionId")))
        mkey = row.get("marketKey", row.get("market_key", row.get("Market")))
        reason = row.get("reason", row.get("Reason", "")) or ""
        try:
            fid_i = int(fid)
        except (TypeError, ValueError):
            continue
        if not mkey:
            continue

        has_components = any(
            k in row
            for k in (
                "coherence",
                "Coherence",
                "agreement",
                "Agreement",
                "verdict",
                "Verdict",
            )
        )
        # Prefer component path when verdict or coherence present; flat score otherwise.
        has_flat = any(k in row for k in ("score", "Score", "rating"))
        use_components = has_components and (
            "coherence" in row
            or "Coherence" in row
            or "verdict" in row
            or "Verdict" in row
            or not has_flat
        )

        approve_b, _ = _parse_verdict_approve(row)
        concerns = _parse_concerns(row.get("concerns", row.get("Concerns")))

        components: Optional[Dict[str, Any]] = None
        score_source = "flat"
        score_i: Optional[int] = None

        if use_components:
            coherence = _clamp_component(
                row.get("coherence", row.get("Coherence")), default=0
            )
            # Parse agreement if present but do not store — base rate owns agreement.
            _ = _clamp_component(row.get("agreement", row.get("Agreement")), default=0)
            components = {
                "coherence": coherence,
                "concerns": concerns,
            }
            score_i = compute_publish_score(
                base_rate=float(config.MARKET_CALIBRATION_DEFAULT_RATE),
                coherence=coherence,
                concerns=concerns,
            )
            score_source = "components"
        else:
            score = row.get("score", row.get("Score", row.get("rating")))
            try:
                score_i = int(round(float(score)))
            except (TypeError, ValueError):
                continue
            score_i = max(0, min(100, score_i))
            score_source = "flat"

        out.append(
            {
                "fixture_id": fid_i,
                "market_key": str(mkey),
                "score": int(score_i),
                "approve": approve_b,
                "reason": str(reason).strip()[:512],
                "score_source": score_source,
                "components": components,
                "concerns": concerns,
            }
        )
    return out


def _finalize_score_for_candidate(
    score_row: Dict[str, Any],
    cand: Dict[str, Any],
    *,
    calibration: Optional[Dict[Any, Dict[str, Any]]] = None,
) -> Tuple[int, str, Dict[str, Any]]:
    """
    Return (final_score, score_source, reliability_meta).
    Uses measured market hit rate (keyed by agreement tier) × AI judgment.
    """
    from dg.report.market_reliability import (
        agreement_tier_from_candidate,
        reliability_for,
    )

    tier = agreement_tier_from_candidate(cand)
    reli = reliability_for(
        calibration, cand.get("market_key"), tier, cand.get("prob")
    )
    components = score_row.get("components")
    if isinstance(components, dict) and score_row.get("score_source") == "components":
        coherence = _clamp_component(components.get("coherence"))
        concerns = list(components.get("concerns") or score_row.get("concerns") or [])
        score = compute_publish_score(
            base_rate=float(reli["rate"]),
            coherence=coherence,
            concerns=concerns,
        )
        return score, "components", reli

    flat = int(score_row.get("score") or 0)
    flat = max(0, min(100, flat))
    score = compute_publish_score(
        base_rate=float(reli["rate"]),
        flat_score=flat,
    )
    try:
        prob = float(cand.get("prob"))
        echo = abs(flat - int(round(prob * 100)))
        logger.warning(
            "AI vet flat-score fallback fixture=%s market=%s flat=%s model_pct=%s "
            "echo_delta=%s publish=%s base_rate=%.3f tier=%s",
            cand.get("fixture_id"),
            cand.get("market_key"),
            flat,
            int(round(prob * 100)),
            echo,
            score,
            float(reli["rate"]),
            tier,
        )
    except (TypeError, ValueError):
        logger.warning(
            "AI vet flat-score fallback fixture=%s market=%s flat=%s publish=%s tier=%s",
            cand.get("fixture_id"),
            cand.get("market_key"),
            flat,
            score,
            tier,
        )
    return score, "flat", reli


def gate_screen_scores(
    candidates: List[Dict[str, Any]],
    scores: List[Dict[str, Any]],
    *,
    min_score: Optional[int] = None,
    calibration: Optional[Dict[Any, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Keep approved scores >= min_score that match a known candidate.
    One pick per fixture (highest score wins).
    Recomputes scores from measured base rate × judgment.
    """
    from dg.report.market_reliability import agreement_tier_from_candidate

    threshold = int(min_score if min_score is not None else config.AI_VET_MIN_SCORE)
    by_key = {
        (int(c["fixture_id"]), str(c.get("market_key") or "")): c
        for c in candidates
        if c.get("fixture_id") is not None and c.get("market_key")
    }
    best_by_fixture: Dict[int, Tuple[int, Dict[str, Any]]] = {}
    for s in scores:
        key = (int(s["fixture_id"]), str(s["market_key"]))
        cand = by_key.get(key)
        if cand is None:
            continue
        if not s.get("approve"):
            continue
        final_score, score_source, reli = _finalize_score_for_candidate(
            s, cand, calibration=calibration
        )
        if final_score < threshold:
            continue
        fid = int(s["fixture_id"])
        components = s.get("components") if isinstance(s.get("components"), dict) else None
        if isinstance(components, dict):
            components = {
                "coherence": components.get("coherence"),
                "concerns": components.get("concerns") or [],
            }
        tier = agreement_tier_from_candidate(cand)
        merged = {
            **cand,
            "ai_score": final_score,
            "ai_reason": s.get("reason") or "",
            "ai_approve": True,
            "ai_score_source": score_source,
            "ai_components": components,
            "ai_base_rate": float(reli["rate"]),
            "ai_base_n": int(reli.get("n") or 0),
            "ai_base_source": reli.get("source"),
            "ai_base_band": reli.get("prob_band"),
            "ai_base_market": reli.get("market_key"),
            "ai_base_tier": reli.get("agreement_tier") or tier,
            "ai_agreement_tier": tier,
        }
        prev = best_by_fixture.get(fid)
        if prev is None or final_score > prev[0]:
            best_by_fixture[fid] = (final_score, merged)
    approved = [item for _, item in best_by_fixture.values()]
    approved.sort(
        key=lambda p: (
            -int(p.get("ai_score") or 0),
            (p.get("league_display") or p.get("league") or "").lower(),
            p.get("date_utc") or "",
        )
    )
    return approved


def _score_telemetry(approved: List[Dict[str, Any]]) -> Dict[str, Any]:
    scores = [int(p.get("ai_score") or 0) for p in approved]
    n_flat = sum(1 for p in approved if p.get("ai_score_source") == "flat")
    echo_n = 0
    for p in approved:
        try:
            prob = float(p.get("prob"))
        except (TypeError, ValueError):
            continue
        if abs(int(p.get("ai_score") or 0) - int(round(prob * 100))) <= ECHO_DELTA:
            echo_n += 1
    n = len(scores)
    if n:
        ordered = sorted(scores)
        mid = n // 2
        if n % 2:
            median = ordered[mid]
        else:
            median = (ordered[mid - 1] + ordered[mid]) / 2.0
        score_min = ordered[0]
        score_max = ordered[-1]
    else:
        median = None
        score_min = None
        score_max = None
    return {
        "echo_rate": (echo_n / n) if n else 0.0,
        "score_min": score_min,
        "score_median": median,
        "score_max": score_max,
        "n_flat_score_fallback": n_flat,
    }


def replace_ai_picks_for_day(
    conn,
    day: str,
    approved: List[Dict[str, Any]],
    *,
    model: str,
) -> int:
    """Delete existing rows for day and insert approved picks. Returns count written."""
    now = _utcnow_iso()
    conn.execute("DELETE FROM ai_pick WHERE day = ?", (day,))
    n = 0
    for pick in approved:
        fid = pick.get("fixture_id")
        mkey = pick.get("market_key")
        if fid is None or not mkey:
            continue
        conn.execute(
            """
            INSERT INTO ai_pick (
                day, fixture_id, market_key, lean, score, reason, model, pick_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                day,
                int(fid),
                str(mkey),
                pick.get("lean"),
                int(pick.get("ai_score") or 0),
                pick.get("ai_reason") or "",
                model,
                json.dumps(pick),
                now,
            ),
        )
        n += 1
    conn.commit()
    return n


def load_ai_picks(conn, day: str) -> List[Dict[str, Any]]:
    from dg.leagues import attach_league_display
    from dg.report.results_attach import attach_result_to_prediction, load_result_index

    rows = conn.execute(
        """
        SELECT ap.day, ap.fixture_id, ap.market_key, ap.lean, ap.score, ap.reason,
               ap.model, ap.pick_json, ap.created_at,
               f.league, f.league_id, f.league_country, f.date_utc,
               f.home_name, f.away_name, f.home_id, f.away_id,
               f.home_logo, f.away_logo, f.is_neutral
        FROM ai_pick ap
        JOIN fixture f ON f.fixture_id = ap.fixture_id
        WHERE ap.day = ?
        ORDER BY ap.score DESC, ap.fixture_id
        """,
        (day,),
    ).fetchall()
    result_index = load_result_index(conn)

    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        try:
            payload = json.loads(d.get("pick_json") or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("fixture_id", d["fixture_id"])
        payload.setdefault("market_key", d["market_key"])
        payload.setdefault("lean", d.get("lean"))
        payload.setdefault("league", d.get("league"))
        payload.setdefault("league_id", d.get("league_id"))
        payload.setdefault("league_country", d.get("league_country"))
        payload.setdefault("home_name", d.get("home_name"))
        payload.setdefault("away_name", d.get("away_name"))
        payload.setdefault("date_utc", d.get("date_utc"))
        payload["home_logo"] = d.get("home_logo") or payload.get("home_logo")
        payload["away_logo"] = d.get("away_logo") or payload.get("away_logo")
        payload["is_neutral"] = bool(
            d.get("is_neutral")
            if d.get("is_neutral") is not None
            else payload.get("is_neutral")
        )
        attach_league_display(payload)

        pred = {
            "fixture_id": d["fixture_id"],
            "home_name": d.get("home_name"),
            "away_name": d.get("away_name"),
            "home_id": d.get("home_id"),
            "away_id": d.get("away_id"),
            "date_utc": d.get("date_utc"),
            "markets": payload.get("markets") or {},
        }
        attach_result_to_prediction(pred, result_index)
        from dg.report.best_leans import grade_candidate_result

        candidate = {
            "market_key": payload.get("market_key"),
            "lean": payload.get("lean"),
        }
        rk, rl = grade_candidate_result(pred, candidate)
        payload["completed"] = bool(pred.get("completed"))
        payload["ft_score"] = pred.get("ft_score")
        payload["awaiting_score"] = bool(pred.get("awaiting_score"))
        payload["lean_result_key"] = rk
        payload["lean_result_label"] = rl

        payload["ai_score"] = d["score"]
        payload["ai_reason"] = d.get("reason") or payload.get("ai_reason") or ""
        payload["ai_model"] = d.get("model")
        payload["ai_day"] = d.get("day")
        if payload.get("ai_base_rate") is not None:
            from dg.report.market_reliability import (
                agreement_tier_label_plain,
                band_label_plain,
                market_label_plain,
            )

            payload["ai_base_market_label"] = market_label_plain(
                str(payload.get("ai_base_market") or payload.get("market_key") or "")
            )
            payload["ai_base_band_label"] = band_label_plain(
                str(payload.get("ai_base_band") or "")
            )
            payload["ai_base_tier_label"] = agreement_tier_label_plain(
                str(
                    payload.get("ai_base_tier")
                    or payload.get("ai_agreement_tier")
                    or ""
                )
            )
        # ai_components / ai_score_source / ai_base_* ride along in pick_json when present.
        out.append(payload)
    return out


def _chunked(items: List[Any], size: int) -> List[List[Any]]:
    n = max(1, int(size))
    return [items[i : i + n] for i in range(0, len(items), n)]


def _build_vet_context(day_key: str) -> Dict[str, Any]:
    ctx = load_strongest_day(date=day_key)
    market_hit_rates = None
    if config.STRONGEST_USE_MARKET_HIT_RATES:
        from dg.report.loaders import get_connection

        conn = get_connection()
        try:
            market_hit_rates = get_market_hit_rates(conn)
        finally:
            conn.close()
    groups = build_ai_vet_fixture_groups(
        ctx.get("predictions") or [],
        market_hit_rates=market_hit_rates,
    )
    ctx["vet_groups"] = groups
    ctx["vet_candidates"] = flatten_vet_groups(groups)
    ctx["fallback_picks"] = ctx.get("picks") or build_strongest_picks(
        ctx.get("predictions") or [],
        market_hit_rates=market_hit_rates,
    )
    return ctx


def vet_strongest_for_day(
    conn,
    *,
    day: Optional[str] = None,
    chat_fn=None,
) -> Dict[str, Any]:
    """
    Load top-N gate-passing candidates per fixture, LLM-pick markets, persist approvals.
    chat_fn: injectable (system, user) -> str for tests.
    """
    from dg.report.loaders import today_wat

    day_key = day or today_wat()
    model = config.OPENAI_MODEL
    summary: Dict[str, Any] = {
        "day": day_key,
        "model": model,
        "n_fixtures": 0,
        "n_candidates": 0,
        "n_batches": 0,
        "n_approved": 0,
        "written": 0,
        "skipped_no_key": False,
        "skipped_no_candidates": False,
        "errors": 0,
        "message": None,
        "echo_rate": 0.0,
        "score_min": None,
        "score_median": None,
        "score_max": None,
        "n_flat_score_fallback": 0,
    }

    if not config.OPENAI_API_KEY and chat_fn is None:
        summary["skipped_no_key"] = True
        summary["message"] = "OPENAI_API_KEY is not set — AI Picks skipped"
        logger.warning(summary["message"])
        return summary

    ctx = _build_vet_context(day_key)
    groups = list(ctx.get("vet_groups") or [])
    candidates = list(ctx.get("vet_candidates") or [])
    summary["n_fixtures"] = len(groups)
    summary["n_candidates"] = len(candidates)
    if not groups:
        summary["skipped_no_candidates"] = True
        replace_ai_picks_for_day(conn, day_key, [], model=model)
        summary["message"] = "No strongest picks to vet"
        return summary

    batches = _chunked(groups, config.AI_VET_BATCH_SIZE)
    summary["n_batches"] = len(batches)
    all_scores: List[Dict[str, Any]] = []

    from dg.report.market_reliability import load_market_calibration

    calibration = load_market_calibration(conn)

    try:
        for bi, batch in enumerate(batches, start=1):
            payload = {
                "day": day_key,
                "batch": bi,
                "batchCount": len(batches),
                "fixtures": [fixture_group_payload(g) for g in batch],
            }
            user = (
                f"For each fixture below, pick at most one market candidate to publish "
                f"for {day_key} (batch {bi}/{len(batches)}). "
                f"Return component judgments (coherence, concerns, verdict). "
                f"Use verdict=skip or omit fixtures you would not publish. "
                f"Do not return a 0-100 score. Be selective.\n"
                f"{json.dumps(payload, ensure_ascii=False)}"
            )
            if chat_fn is not None:
                raw = chat_fn(SYSTEM_PROMPT, user)
            else:
                raw = chat_json(
                    system=SYSTEM_PROMPT,
                    user=user,
                    max_tokens=config.AI_VET_MAX_TOKENS,
                )
            batch_scores = parse_screen_response(raw)
            logger.info(
                "AI vet batch %s/%s: %s picks from %s fixtures",
                bi,
                len(batches),
                len(batch_scores),
                len(batch),
            )
            all_scores.extend(batch_scores)

        approved = gate_screen_scores(
            candidates, all_scores, calibration=calibration
        )
        if not approved and all_scores:
            logger.warning("AI vet: LLM returned scores but none passed gate — no fallback")
        written = replace_ai_picks_for_day(conn, day_key, approved, model=model)
        summary["n_approved"] = len(approved)
        summary["written"] = written
        summary.update(_score_telemetry(approved))
        summary["message"] = (
            f"Approved {written} of {len(groups)} fixtures ({len(candidates)} candidates)"
        )
        logger.info("AI vet: %s", summary)
        return summary
    except OpenAIError as exc:
        logger.warning("AI vet failed: %s", exc)
        summary["errors"] = 1
        summary["message"] = str(exc)
        return summary
    except Exception as exc:  # noqa: BLE001
        logger.exception("AI vet unexpected failure")
        summary["errors"] = 1
        summary["message"] = str(exc)
        return summary
