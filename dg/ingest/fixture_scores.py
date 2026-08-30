"""Sync finished scores from API-Football into match_result for board fixtures."""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from dg import config
from dg.report.results_attach import load_result_index, lookup_result
from dg.sources.apifootball import (
    ApiFootballConfigError,
    fetch_fixture_by_id,
    fetch_fixtures_by_date,
    parse_finished_score,
)

logger = logging.getLogger(__name__)

SOURCE = "api-football"


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
    """
    Predicted fixtures whose kickoff is in the past and that have no joinable
    match_result yet.
    """
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


def upsert_api_result(conn, fixture: Dict[str, Any], score: Dict[str, Any]) -> None:
    day = (fixture.get("date_utc") or "")[:10]
    if not day:
        return
    home = fixture.get("home_name") or ""
    away = fixture.get("away_name") or ""
    league = (fixture.get("league") or "API")[:32]
    season = config.DEFAULT_FD_SEASON
    raw = {
        "fixture_id": fixture.get("fixture_id"),
        "status": score.get("status"),
        "source": SOURCE,
        **{k: score[k] for k in ("fthg", "ftag", "ftr", "hthg", "htag") if k in score},
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
            SOURCE,
            season,
            league,
            day,
            home,
            away,
            fixture.get("home_id"),
            fixture.get("away_id"),
            score.get("fthg"),
            score.get("ftag"),
            score.get("ftr"),
            score.get("hthg"),
            score.get("htag"),
            json.dumps(raw),
        ),
    )


def _apply_items(
    conn,
    items: List[Dict[str, Any]],
    by_id: Dict[int, Dict[str, Any]],
    written_ids: Set[int],
) -> Tuple[int, int, int]:
    """Upsert finished scores that match candidates. Returns (fetched, written, not_finished)."""
    fetched = len(items)
    written = 0
    not_finished = 0
    for item in items:
        score = parse_finished_score(item)
        if score is None:
            not_finished += 1
            continue
        fid = score.get("fixture_id")
        if fid is None:
            fx_block = item.get("fixture") or {}
            fid = fx_block.get("id")
        if fid is None:
            continue
        fid = int(fid)
        if fid not in by_id or fid in written_ids:
            continue
        upsert_api_result(conn, by_id[fid], score)
        written_ids.add(fid)
        written += 1
    return fetched, written, not_finished


def sync_fixture_scores(conn) -> Dict[str, Any]:
    """
    Pull finished scores from API-Football for past predicted fixtures missing results.

    Free-plan safe: fetch by ``date`` (not batch ``ids``), then optional single ``id`` fallback.
    """
    if not config.API_FOOTBALL_KEY:
        logger.warning("API_FOOTBALL_KEY not set — skipping score sync")
        return {
            "skipped_no_key": True,
            "candidates": 0,
            "days_fetched": 0,
            "fetched": 0,
            "written": 0,
            "not_finished": 0,
            "fallback_tried": 0,
            "errors": 0,
        }

    candidates = fixtures_needing_scores(conn)
    by_id = {int(c["fixture_id"]): c for c in candidates if c.get("fixture_id") is not None}
    by_day: Dict[str, List[int]] = defaultdict(list)
    for fid, fx in by_id.items():
        day = (fx.get("date_utc") or "")[:10]
        if day:
            by_day[day].append(fid)

    written_ids: Set[int] = set()
    fetched = 0
    written = 0
    not_finished = 0
    errors = 0
    days_fetched = 0

    for day in sorted(by_day.keys()):
        try:
            items = fetch_fixtures_by_date(day)
        except ApiFootballConfigError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("API-Football date=%s failed: %s", day, exc)
            errors += 1
            continue
        days_fetched += 1
        f, w, nf = _apply_items(conn, items, by_id, written_ids)
        fetched += f
        written += w
        not_finished += nf

    remaining = [fid for fid in by_id if fid not in written_ids]
    fallback_max = max(0, config.API_FOOTBALL_ID_FALLBACK_MAX)
    fallback_tried = 0
    for fid in remaining[:fallback_max]:
        fallback_tried += 1
        try:
            items = fetch_fixture_by_id(fid)
        except ApiFootballConfigError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("API-Football id=%s failed: %s", fid, exc)
            errors += 1
            continue
        f, w, nf = _apply_items(conn, items, by_id, written_ids)
        fetched += f
        written += w
        not_finished += nf

    conn.commit()
    summary = {
        "skipped_no_key": False,
        "candidates": len(by_id),
        "days_fetched": days_fetched,
        "fetched": fetched,
        "written": written,
        "not_finished": not_finished,
        "fallback_tried": fallback_tried,
        "errors": errors,
    }
    logger.info("Score sync: %s", summary)
    return summary
