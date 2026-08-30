"""Shared loaders for latest snapshot + predictions (web UI and reports)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from dg import config
from dg.model.markets import MARKET_ORDER, MARKET_SIDES
from dg.quality.checks import staleness_hours
from dg.report.results_attach import (
    attach_result_to_prediction,
    lean_result,
    load_result_index,
    market_lean_result,
)
from dg.storage.db import connect, init_db, latest_snapshot

_CONF_RANK = {"low": 0, "medium": 1, "high": 2}
DISPLAY_TZ = ZoneInfo("Africa/Lagos")  # WAT (UTC+1, no DST)


def get_connection():
    config.ensure_dirs()
    return init_db(connect())


def parse_market_filters(raw: Optional[Sequence[str]]) -> List[Tuple[str, str]]:
    """
    Parse repeated ``m`` query values of the form ``key:side``.
    Invalid or blank entries are dropped silently.
    """
    out: List[Tuple[str, str]] = []
    seen: set = set()
    for item in raw or []:
        if not item or not isinstance(item, str):
            continue
        if ":" not in item:
            continue
        key, side = item.split(":", 1)
        key, side = key.strip(), side.strip()
        if not key or key not in MARKET_SIDES:
            continue
        if side not in MARKET_SIDES[key]:
            continue
        if key in seen:
            # Last selection for a market wins
            out = [(k, s) for k, s in out if k != key]
        seen.add(key)
        out.append((key, side))
    # Stable order matching MARKET_ORDER
    order = {k: i for i, k in enumerate(MARKET_ORDER)}
    out.sort(key=lambda pair: order.get(pair[0], 99))
    return out


def _conf_ok(confidence: Optional[str], min_conf: Optional[str]) -> bool:
    if not min_conf:
        return True
    need = _CONF_RANK.get(min_conf.lower())
    if need is None:
        return True
    have = _CONF_RANK.get((confidence or "low").lower(), 0)
    return have >= need


def _prob_ok(prob: Any, min_prob: Optional[float]) -> bool:
    if min_prob is None:
        return True
    if prob is None:
        # Missing prob passes rather than hiding fixtures
        return True
    try:
        return float(prob) >= float(min_prob)
    except (TypeError, ValueError):
        return True


def prediction_matches_markets(
    markets: Optional[Dict[str, Any]],
    criteria: Sequence[Tuple[str, str]],
    *,
    mode: str = "all",
    min_prob: Optional[float] = None,
    min_conf: Optional[str] = None,
    pred: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Return True if the prediction satisfies the market criteria.

    When ``criteria`` is empty but a threshold is set, apply thresholds to the
    fixture's main 1X2 lean (via ``pred``).
    """
    markets = markets or {}
    mode = (mode or "all").lower()
    if mode not in ("all", "any"):
        mode = "all"

    if not criteria:
        if min_prob is None and not min_conf:
            return True
        # Thresholds only — use main 1X2 lean
        if not pred:
            return True
        lean = pred.get("lean")
        conf = pred.get("confidence")
        if not _conf_ok(conf, min_conf):
            return False
        probs = pred.get("probs") or {}
        lean_prob = None
        if lean == "Home":
            lean_prob = probs.get("home")
        elif lean == "Away":
            lean_prob = probs.get("away")
        elif lean == "Draw":
            lean_prob = probs.get("draw")
        return _prob_ok(lean_prob, min_prob)

    def _one(key: str, side: str) -> bool:
        m = markets.get(key)
        if not isinstance(m, dict):
            return False
        if m.get("lean") != side:
            return False
        if not _conf_ok(m.get("confidence"), min_conf):
            return False
        if not _prob_ok(m.get("prob"), min_prob):
            return False
        return True

    results = [_one(k, s) for k, s in criteria]
    if mode == "any":
        return any(results)
    return all(results)


def market_filter_options(
    active: Optional[Sequence[Tuple[str, str]]] = None,
) -> List[Dict[str, Any]]:
    """Build select option metadata for the dashboard form."""
    from dg.web.plain_language import market_chip_label

    active_map = {k: s for k, s in (active or [])}
    options = []
    for key in MARKET_ORDER:
        sides = MARKET_SIDES[key]
        options.append(
            {
                "key": key,
                "label": market_chip_label(key),
                "sides": list(sides),
                "selected": active_map.get(key),
            }
        )
    return options


def load_dashboard_context(
    *,
    date_filter: Optional[str] = None,
    league_filter: Optional[str] = None,
    market_filters: Optional[Sequence[Tuple[str, str]]] = None,
    match_mode: str = "all",
    min_prob: Optional[float] = None,
    min_conf: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Load latest snapshot metadata and the newest prediction per fixture
    for the dashboard.
    """
    market_filters = list(market_filters or [])
    match_mode = (match_mode or "all").lower()
    if match_mode not in ("all", "any"):
        match_mode = "all"
    if min_conf:
        min_conf = min_conf.lower()
        if min_conf not in _CONF_RANK:
            min_conf = None

    empty_base = {
        "ok": False,
        "empty": True,
        "message": "No data yet. The daily refresh has not run.",
        "generated_at": None,
        "generated_at_display": "—",
        "staleness_hours": None,
        "stale": False,
        "status": "empty",
        "n_teams": 0,
        "snapshot_id": None,
        "model_version": None,
        "predictions": [],
        "dates": [],
        "leagues": [],
        "date_filter": date_filter,
        "league_filter": league_filter,
        "market_filters": market_filters,
        "match_mode": match_mode,
        "min_prob": min_prob,
        "min_conf": min_conf,
        "market_filter_options": market_filter_options(market_filters),
        "n_market_filters": len(market_filters),
    }

    conn = get_connection()
    try:
        snap = latest_snapshot(conn)
        if snap is None:
            return empty_base

        generated_at = snap["generated_at"]
        age = staleness_hours(generated_at)
        stale = age == age and age > config.STALE_HOURS_THRESHOLD
        status = "stale" if stale else "ok"

        # Latest prediction per fixture
        rows = conn.execute(
            """
            SELECT p.*, f.date_utc, f.league, f.home_name, f.away_name,
                   f.home_id, f.away_id
            FROM prediction p
            JOIN fixture f ON f.fixture_id = p.fixture_id
            WHERE p.id IN (
                SELECT MAX(id) FROM prediction GROUP BY fixture_id
            )
            ORDER BY f.date_utc
            """
        ).fetchall()

        result_index = load_result_index(conn)

        predictions: List[Dict[str, Any]] = []
        dates: set = set()
        leagues: set = set()
        for r in rows:
            d = dict(r)
            day = (d.get("date_utc") or "")[:10]
            league = d.get("league") or ""
            if day:
                dates.add(day)
            if league:
                leagues.add(league)
            # Collect dates/leagues before filtering so dropdowns stay complete
            if date_filter and day != date_filter:
                continue
            if league_filter and league != league_filter:
                continue
            try:
                d["drivers"] = json.loads(d.get("drivers_json") or "[]")
            except json.JSONDecodeError:
                d["drivers"] = []
            try:
                d["scores"] = json.loads(d.get("scores_json") or "{}")
            except json.JSONDecodeError:
                d["scores"] = {}
            try:
                d["markets"] = json.loads(d.get("markets_json") or "{}")
            except json.JSONDecodeError:
                d["markets"] = {}
            try:
                d["probs"] = json.loads(d.get("probs_json") or "{}")
            except json.JSONDecodeError:
                d["probs"] = {}

            if market_filters or min_prob is not None or min_conf:
                if not prediction_matches_markets(
                    d.get("markets"),
                    market_filters,
                    mode=match_mode,
                    min_prob=min_prob,
                    min_conf=min_conf,
                    pred=d,
                ):
                    continue

            # Attach latest projection baselines if available
            proj = conn.execute(
                """
                SELECT home_win_pct, draw_pct, away_win_pct, book_odds_json,
                       sim_xg_home, sim_xg_away
                FROM fixture_projection
                WHERE fixture_id = ?
                ORDER BY observed_at DESC LIMIT 1
                """,
                (d["fixture_id"],),
            ).fetchone()
            if proj:
                d["sim_xg_home"] = proj["sim_xg_home"]
                d["sim_xg_away"] = proj["sim_xg_away"]
                book = {}
                try:
                    book = json.loads(proj["book_odds_json"] or "{}")
                except json.JSONDecodeError:
                    book = {}
                from dg.features.matchup import book_lean, sim_lean

                d["dg_sim_lean"] = sim_lean(
                    proj["home_win_pct"], proj["draw_pct"], proj["away_win_pct"]
                )
                d["book_lean"] = book_lean(book)
            else:
                d.setdefault("dg_sim_lean", None)
                d.setdefault("book_lean", None)

            attach_result_to_prediction(d, result_index)
            predictions.append(d)

        mv_row = conn.execute(
            "SELECT model_version FROM prediction ORDER BY id DESC LIMIT 1"
        ).fetchone()

        return {
            "ok": True,
            "empty": False,
            "message": None,
            "generated_at": generated_at,
            "generated_at_display": format_generated_at(generated_at),
            "staleness_hours": age,
            "stale": stale,
            "status": status,
            "n_teams": int(snap["n_teams"]),
            "snapshot_id": int(snap["id"]),
            "model_version": mv_row["model_version"] if mv_row else None,
            "predictions": predictions,
            "dates": sorted(dates),
            "leagues": sorted(leagues),
            "date_filter": date_filter,
            "league_filter": league_filter,
            "market_filters": market_filters,
            "match_mode": match_mode,
            "min_prob": min_prob,
            "min_conf": min_conf,
            "market_filter_options": market_filter_options(market_filters),
            "n_market_filters": len(market_filters),
        }
    finally:
        conn.close()


def enrich_prediction_for_display(pred: Dict[str, Any]) -> Dict[str, Any]:
    """Add plain-language fields used by the web UI."""
    from dg.web.plain_language import (
        agreement_hint,
        confidence_blurb,
        driver_plain,
        lean_plain,
        match_style_plain,
        probability_plain,
        side_plain,
        strength_gap_plain,
    )

    out = dict(pred)
    out["lean_label"] = lean_plain(pred.get("lean"))
    out["confidence_blurb"] = confidence_blurb(pred.get("confidence"))
    out["confidence_key"] = (pred.get("confidence") or "low").lower()
    out["style_label"] = match_style_plain(pred.get("match_character"))
    out["dg_sim_label"] = side_plain(pred.get("dg_sim_lean"), prefix="DataGaffer model says")
    out["book_label"] = side_plain(pred.get("book_lean"), prefix="Betting market favours")
    hint = agreement_hint(pred.get("lean"), pred.get("dg_sim_lean"), pred.get("book_lean"))
    out["agreement_key"] = hint["key"]
    out["agreement_label"] = hint["label"]
    out["why"] = [driver_plain(d) for d in (pred.get("drivers") or [])]
    out["kickoff_display"] = _format_kickoff(pred.get("date_utc"))

    # Probabilities (1X2 + strength)
    probs = pred.get("probs")
    if probs is None and pred.get("probs_json"):
        try:
            probs = json.loads(pred["probs_json"])
        except (json.JSONDecodeError, TypeError):
            probs = {}
    probs = probs or {}
    out["probs"] = probs

    lean = pred.get("lean")
    lean_prob = None
    if lean == "Home":
        lean_prob = probs.get("home")
    elif lean == "Away":
        lean_prob = probs.get("away")
    elif lean == "Draw":
        lean_prob = probs.get("draw")
    out["lean_prob"] = lean_prob
    out["lean_prob_plain"] = probability_plain(lean_prob)

    dgrtg_h = probs.get("dgrtg_home", pred.get("dgrtg_home"))
    dgrtg_a = probs.get("dgrtg_away", pred.get("dgrtg_away"))
    gap = probs.get("rating_gap", pred.get("rating_gap"))
    out["strength_label"] = strength_gap_plain(
        pred.get("home_name"),
        pred.get("away_name"),
        dgrtg_h,
        dgrtg_a,
        gap,
    )

    # Parse markets from DB JSON if needed
    markets = pred.get("markets")
    if markets is None and pred.get("markets_json"):
        try:
            markets = json.loads(pred["markets_json"])
        except (json.JSONDecodeError, TypeError):
            markets = {}
    markets = markets or {}

    from dg.model.markets import MARKET_ORDER
    from dg.web.plain_language import market_chip_label, market_lean_plain

    chips = []
    market_labels: Dict[str, Any] = {}
    if pred.get("completed") and pred.get("result_row"):
        from dg.model.evaluate import _market_labels

        market_labels = _market_labels(pred["result_row"])

    for key in MARKET_ORDER:
        m = markets.get(key)
        if not isinstance(m, dict):
            continue
        r_key, r_label = market_lean_result(m.get("lean"), market_labels.get(key))
        chips.append(
            {
                **m,
                "lean_plain": market_lean_plain(m.get("lean"), key),
                "chip_label": market_chip_label(key, m.get("label")),
                "confidence_key": (m.get("confidence") or "low").lower(),
                "prob_plain": probability_plain(m.get("prob")),
                "result_key": r_key,
                "result_label": r_label,
                "actual_label": market_labels.get(key),
            }
        )
    out["market_chips"] = chips
    out["markets"] = markets

    # FT score + match-winner lean outcome
    out["completed"] = bool(pred.get("completed"))
    out["ft_score"] = pred.get("ft_score")
    out["ft_home"] = pred.get("ft_home")
    out["ft_away"] = pred.get("ft_away")
    out["ftr"] = pred.get("ftr")
    if out["completed"]:
        lr_key, lr_label = lean_result(pred.get("lean"), pred.get("ftr"))
        out["lean_result_key"] = lr_key
        out["lean_result_label"] = lr_label
        out["awaiting_score"] = False
    else:
        out["lean_result_key"] = "pending"
        out["lean_result_label"] = ""
        out["awaiting_score"] = _kickoff_in_past(pred.get("date_utc"))

    out.setdefault("score_hint_candidates", [])
    return out


def _parse_utc(date_utc: Optional[str]) -> Optional[datetime]:
    if not date_utc:
        return None
    try:
        dt = datetime.fromisoformat(date_utc.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _to_wat(dt: datetime) -> datetime:
    return dt.astimezone(DISPLAY_TZ)


def _kickoff_in_past(date_utc: Optional[str]) -> bool:
    kickoff = _parse_utc(date_utc)
    if kickoff is None:
        return False
    return kickoff <= datetime.now(timezone.utc)


def _format_kickoff(date_utc: Optional[str]) -> str:
    dt = _parse_utc(date_utc)
    if dt is None:
        return date_utc or "—"
    local = _to_wat(dt)
    return local.strftime("%a %d %b · %H:%M WAT")


def format_generated_at(generated_at: Optional[str]) -> str:
    """Human-readable DG snapshot time in Nigerian (WAT) time."""
    dt = _parse_utc(generated_at)
    if dt is None:
        return generated_at or "—"
    local = _to_wat(dt)
    return local.strftime("%a %d %b %Y · %H:%M WAT")


def _fixture_sort_key(pred: Dict[str, Any]) -> Tuple[str, str]:
    return ((pred.get("league") or "").lower(), pred.get("date_utc") or "")


def group_predictions_by_date(
    predictions: List[Dict[str, Any]],
) -> List[Tuple[str, List[Dict[str, Any]]]]:
    by_day: Dict[str, List[Dict[str, Any]]] = {}
    for p in predictions:
        day = (p.get("date_utc") or "")[:10] or "unknown"
        by_day.setdefault(day, []).append(p)
    out: List[Tuple[str, List[Dict[str, Any]]]] = []
    for d in sorted(by_day):
        items = sorted(by_day[d], key=_fixture_sort_key)
        out.append((d, items))
    return out
