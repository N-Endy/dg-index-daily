"""Sync finished scores into match_result (Flashscore primary, API-Football optional)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from dg import config
from dg.report.results_attach import load_result_index, lookup_result
from dg.sources.flashscore import (
    FlashscoreBlockedError,
    FlashscoreCooldownError,
    FlashscoreUnavailableError,
    scrape_finished_scores,
    teams_match,
)

logger = logging.getLogger(__name__)

SOURCE_FLASHSCORE = "flashscore"
SOURCE_API = "api-football"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_kickoff(date_utc: Optional[str]) -> Optional[datetime]:
    if not date_utc:
        return None
    try:
        return datetime.fromisoformat(date_utc.replace("Z", "+00:00"))
    except ValueError:
        return None


def fixtures_needing_scores(conn) -> List[Dict[str, Any]]:
    """Predicted fixtures whose kickoff is past and that have no joinable result."""
    rows = conn.execute(
        """
        SELECT f.fixture_id, f.date_utc, f.league, f.home_name, f.away_name,
               f.home_id, f.away_id
        FROM fixture f
        WHERE f.fixture_id IN (SELECT DISTINCT fixture_id FROM prediction)
        ORDER BY f.date_utc
        """
    ).fetchall()
    index = load_result_index(conn)
    now = _utcnow()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        kickoff = _parse_kickoff(d.get("date_utc"))
        if kickoff is None or kickoff > now:
            continue
        if lookup_result(
            index,
            home_id=d.get("home_id"),
            away_id=d.get("away_id"),
            date_utc=d.get("date_utc"),
        ):
            continue
        out.append(d)
    return out


def upsert_score_result(
    conn,
    fixture: Dict[str, Any],
    score: Dict[str, Any],
    *,
    source: str,
) -> None:
    day = (fixture.get("date_utc") or "")[:10]
    if not day:
        return
    home = fixture.get("home_name") or ""
    away = fixture.get("away_name") or ""
    league = (fixture.get("league") or source)[:32]
    season = config.DEFAULT_FD_SEASON
    fthg, ftag = score.get("fthg"), score.get("ftag")
    ftr = score.get("ftr")
    if ftr is None and fthg is not None and ftag is not None:
        if int(fthg) > int(ftag):
            ftr = "H"
        elif int(ftag) > int(fthg):
            ftr = "A"
        else:
            ftr = "D"
    raw = {
        "fixture_id": fixture.get("fixture_id"),
        "source": source,
        "scraped_home": score.get("home"),
        "scraped_away": score.get("away"),
        "scraped_league": score.get("league"),
        "fthg": fthg,
        "ftag": ftag,
        "ftr": ftr,
    }
    conn.execute(
        """
        INSERT INTO match_result (
            source, season, league_code, date, home_name, away_name,
            home_team_id, away_team_id, fthg, ftag, ftr, hthg, htag,
            raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, season, league_code, date, home_name, away_name)
        DO UPDATE SET
            home_team_id=excluded.home_team_id,
            away_team_id=excluded.away_team_id,
            fthg=excluded.fthg, ftag=excluded.ftag, ftr=excluded.ftr,
            hthg=excluded.hthg, htag=excluded.htag,
            raw_json=excluded.raw_json
        """,
        (
            source,
            season,
            league,
            day,
            home,
            away,
            fixture.get("home_id"),
            fixture.get("away_id"),
            fthg,
            ftag,
            ftr,
            score.get("hthg"),
            score.get("htag"),
            json.dumps(raw),
        ),
    )


def _fixture_day(fx: Dict[str, Any]) -> Optional[str]:
    return (fx.get("date_utc") or "")[:10] or None


def day_offsets_for_candidates(candidates: List[Dict[str, Any]]) -> List[int]:
    """
    Map candidate fixture UTC days to flashscore.mobi ?d= offsets.
    Always include today (0); clamp older days to [-3, 0].
    """
    today = _utcnow().date()
    offsets: Set[int] = {0}
    for fx in candidates:
        day = _fixture_day(fx)
        if not day:
            continue
        try:
            fday = datetime.strptime(day, "%Y-%m-%d").date()
        except ValueError:
            continue
        raw = (fday - today).days
        offsets.add(max(-3, min(0, raw)))
    return sorted(offsets, reverse=True)  # 0, -1, -2, …


def match_flashscore_row_to_fixture(
    row: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    used_ids: Set[int],
) -> Optional[Dict[str, Any]]:
    """
    Find best fixture for a scraped finished score.
    Day tolerance ±1 (Flashscore mixes prior-day finished with today's slate).
    """
    home, away = row.get("home") or "", row.get("away") or ""
    best: Optional[Dict[str, Any]] = None
    best_score = -1
    from thefuzz import fuzz
    from dg.sources.flashscore import normalize_team_name

    today = _utcnow().date()
    for fx in candidates:
        fid = fx.get("fixture_id")
        if fid is None or int(fid) in used_ids:
            continue
        day = _fixture_day(fx)
        if not day:
            continue
        try:
            fday = datetime.strptime(day, "%Y-%m-%d").date()
        except ValueError:
            continue
        if not teams_match(home, fx.get("home_name") or ""):
            continue
        if not teams_match(away, fx.get("away_name") or ""):
            continue
        # Prefer closer calendar day (±1 mixes prior-day finished with today's slate)
        day_penalty = abs((fday - today).days)
        name_score = fuzz.token_sort_ratio(
            normalize_team_name(home) + " " + normalize_team_name(away),
            normalize_team_name(fx.get("home_name") or "")
            + " "
            + normalize_team_name(fx.get("away_name") or ""),
        )
        rank = name_score - day_penalty
        if rank > best_score:
            best_score = rank
            best = fx
    return best


def _api_football_unavailable(message: str) -> bool:
    lower = (message or "").lower()
    markers = (
        "suspend",
        "unauthorized",
        "forbidden",
        "invalid api key",
        "token",
        "not allowed",
        "plan",
        "403",
    )
    return any(m in lower for m in markers)


def sync_flashscore_scores(
    conn,
    *,
    scraped_rows: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Match scraped Flashscore rows onto fixtures needing scores."""
    candidates = fixtures_needing_scores(conn)
    offsets = day_offsets_for_candidates(candidates)
    summary: Dict[str, Any] = {
        "source": SOURCE_FLASHSCORE,
        "candidates": len(candidates),
        "day_offsets": offsets,
        "scraped": 0,
        "written": 0,
        "unmatched": 0,
        "errors": 0,
        "skipped_cooldown": False,
        "skipped_blocked": False,
        "skipped_unavailable": False,
    }
    if not candidates:
        return summary

    try:
        rows = (
            scraped_rows
            if scraped_rows is not None
            else scrape_finished_scores(day_offsets=offsets)
        )
    except FlashscoreCooldownError as exc:
        logger.warning("%s", exc)
        summary["skipped_cooldown"] = True
        summary["errors"] = 1
        return summary
    except FlashscoreBlockedError as exc:
        logger.warning("Flashscore blocked: %s", exc)
        summary["skipped_blocked"] = True
        summary["errors"] = 1
        return summary
    except FlashscoreUnavailableError as exc:
        logger.warning("Flashscore unavailable: %s", exc)
        summary["skipped_unavailable"] = True
        summary["errors"] = 1
        return summary

    summary["scraped"] = len(rows)
    used: Set[int] = set()
    for row in rows:
        fx = match_flashscore_row_to_fixture(row, candidates, used)
        if fx is None:
            summary["unmatched"] += 1
            continue
        upsert_score_result(conn, fx, row, source=SOURCE_FLASHSCORE)
        used.add(int(fx["fixture_id"]))
        summary["written"] += 1

    conn.commit()
    if summary["written"] == 0 and candidates:
        sample = [
            f"{c.get('home_name')} vs {c.get('away_name')} ({(c.get('date_utc') or '')[:10]})"
            for c in candidates[:8]
        ]
        logger.warning(
            "Flashscore wrote 0/%d candidates (scraped=%d, offsets=%s); sample: %s",
            len(candidates),
            len(rows),
            offsets,
            sample,
        )
    logger.info("Flashscore sync: %s", summary)
    return summary


def sync_fixture_scores(conn) -> Dict[str, Any]:
    """
    Default timely score sync: Flashscore.mobi first.
    Optional API-Football fill for leftovers if key is set and not suspended.
    """
    flash = sync_flashscore_scores(conn)
    summary: Dict[str, Any] = {
        "flashscore": flash,
        "api_football": None,
        "written": int(flash.get("written") or 0),
        "candidates": int(flash.get("candidates") or 0),
        "skipped_no_key": False,
    }

    remaining = fixtures_needing_scores(conn)
    if not remaining or not config.API_FOOTBALL_KEY:
        if not config.API_FOOTBALL_KEY:
            summary["skipped_no_key"] = True
        logger.info("Score sync summary: %s", summary)
        return summary

    # Optional API-Football for leftovers — abort once if account suspended
    try:
        from dg.sources.apifootball import (
            fetch_fixture_by_id,
            fetch_fixtures_by_date,
            parse_finished_score,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("API-Football import failed: %s", exc)
        return summary

    by_id = {int(c["fixture_id"]): c for c in remaining if c.get("fixture_id") is not None}
    by_day: Dict[str, List[int]] = {}
    for fid, fx in by_id.items():
        day = _fixture_day(fx)
        if day:
            by_day.setdefault(day, []).append(fid)

    api_written = 0
    api_errors = 0
    days_fetched = 0
    written_ids: Set[int] = set()

    for day in sorted(by_day.keys()):
        try:
            items = fetch_fixtures_by_date(day)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            logger.warning("API-Football date=%s failed: %s", day, msg)
            api_errors += 1
            if _api_football_unavailable(msg):
                logger.warning(
                    "API-Football key rejected or account suspended — skipping further API calls; "
                    "scores will rely on Flashscore / football-data"
                )
                summary["api_football"] = {
                    "skipped_api_unavailable": True,
                    "written": api_written,
                    "errors": api_errors,
                }
                logger.info("Score sync summary: %s", summary)
                return summary
            continue
        days_fetched += 1
        for item in items:
            score = parse_finished_score(item)
            if not score:
                continue
            fid = score.get("fixture_id")
            if fid is None or int(fid) not in by_id or int(fid) in written_ids:
                continue
            fid = int(fid)
            upsert_score_result(conn, by_id[fid], score, source=SOURCE_API)
            written_ids.add(fid)
            api_written += 1

    remaining_ids = [fid for fid in by_id if fid not in written_ids]
    for fid in remaining_ids[: max(0, config.API_FOOTBALL_ID_FALLBACK_MAX)]:
        try:
            items = fetch_fixture_by_id(fid)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            api_errors += 1
            if _api_football_unavailable(msg):
                logger.warning(
                    "API-Football key rejected or account suspended — stopping API fallback"
                )
                break
            continue
        for item in items:
            score = parse_finished_score(item)
            if not score:
                continue
            upsert_score_result(conn, by_id[fid], score, source=SOURCE_API)
            written_ids.add(fid)
            api_written += 1

    if api_written:
        conn.commit()
    summary["api_football"] = {
        "skipped_api_unavailable": False,
        "days_fetched": days_fetched,
        "written": api_written,
        "errors": api_errors,
    }
    summary["written"] = int(flash.get("written") or 0) + api_written
    logger.info("Score sync summary: %s", summary)
    return summary


# Back-compat alias used by older tests
def upsert_api_result(conn, fixture: Dict[str, Any], score: Dict[str, Any]) -> None:
    upsert_score_result(conn, fixture, score, source=SOURCE_API)
