"""Screen Strongest leans with an LLM and persist approved AI picks."""
from __future__ import annotations

import json
import logging
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

SYSTEM_PROMPT = (
    "You are a conservative football analyst reviewing pre-filtered directional leans "
    "from a rule-based ratings model (DataGaffer DG Index). "
    "For each fixture you may see multiple market candidates that already passed hard gates. "
    "Pick at most ONE candidate per fixture to publish (or none). "
    "Choose the market whose lean is most coherent given ONLY the provided fields "
    "(probability, confidence, DG/book agreement, drivers). "
    "Do not invent stats, injuries, lineups, or odds. "
    "Set approve=true only when you would stand behind publishing that lean; be selective. "
    "Respond with JSON only: "
    '{"picks":[{"fixtureId":123,"marketKey":"goals_2_5","score":0-100,'
    '"approve":true,"reason":"one short plain sentence"}]}.'
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    return {
        "fixtureId": group.get("fixture_id"),
        "homeTeam": group.get("home_name"),
        "awayTeam": group.get("away_name"),
        "league": group.get("league_display") or group.get("league"),
        "kickoff": group.get("kickoff_display") or group.get("date_utc"),
        "candidates": [candidate_payload(c) for c in (group.get("candidates") or [])],
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


def parse_screen_response(raw: str) -> List[Dict[str, Any]]:
    """Parse LLM JSON into a list of score rows (resilient to key casing)."""
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
        score = row.get("score", row.get("Score", row.get("rating")))
        approve = row.get("approve", row.get("Approve", row.get("passed")))
        reason = row.get("reason", row.get("Reason", "")) or ""
        try:
            fid_i = int(fid)
        except (TypeError, ValueError):
            continue
        if not mkey:
            continue
        try:
            score_i = int(round(float(score)))
        except (TypeError, ValueError):
            continue
        score_i = max(0, min(100, score_i))
        if isinstance(approve, str):
            approve_b = approve.strip().lower() in ("1", "true", "yes", "y")
        else:
            approve_b = bool(approve)
        out.append(
            {
                "fixture_id": fid_i,
                "market_key": str(mkey),
                "score": score_i,
                "approve": approve_b,
                "reason": str(reason).strip()[:512],
            }
        )
    return out


def gate_screen_scores(
    candidates: List[Dict[str, Any]],
    scores: List[Dict[str, Any]],
    *,
    min_score: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Keep approved scores >= min_score that match a known candidate.
    One pick per fixture (highest score wins).
    """
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
        if int(s["score"]) < threshold:
            continue
        fid = int(s["fixture_id"])
        merged = {
            **cand,
            "ai_score": int(s["score"]),
            "ai_reason": s.get("reason") or "",
            "ai_approve": True,
        }
        prev = best_by_fixture.get(fid)
        if prev is None or int(s["score"]) > prev[0]:
            best_by_fixture[fid] = (int(s["score"]), merged)
    approved = [item for _, item in best_by_fixture.values()]
    approved.sort(
        key=lambda p: (
            -int(p.get("ai_score") or 0),
            (p.get("league_display") or p.get("league") or "").lower(),
            p.get("date_utc") or "",
        )
    )
    return approved


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
               f.home_name, f.away_name, f.home_id, f.away_id
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

    try:
        for bi, batch in enumerate(batches, start=1):
            payload = {
                "day": day_key,
                "batch": bi,
                "batchCount": len(batches),
                "minScoreHint": config.AI_VET_MIN_SCORE,
                "fixtures": [fixture_group_payload(g) for g in batch],
            }
            user = (
                f"For each fixture below, pick at most one market candidate to publish "
                f"for {day_key} (batch {bi}/{len(batches)}). "
                f"Use approve=false or omit fixtures you would not publish. "
                f"Be selective; only approve when confident.\n"
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

        approved = gate_screen_scores(candidates, all_scores)
        if not approved and all_scores:
            logger.warning("AI vet: LLM returned scores but none passed gate — no fallback")
        written = replace_ai_picks_for_day(conn, day_key, approved, model=model)
        summary["n_approved"] = len(approved)
        summary["written"] = written
        summary["message"] = f"Approved {written} of {len(groups)} fixtures ({len(candidates)} candidates)"
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
