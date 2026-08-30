"""Sync finished scores from API-Football into match_result for board fixtures."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dg import config
from dg.report.results_attach import load_result_index, lookup_result
from dg.sources.apifootball import (
    ApiFootballConfigError,
    fetch_fixtures_by_ids,
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


def sync_fixture_scores(conn) -> Dict[str, Any]:
    """
    Pull finished scores from API-Football for past predicted fixtures missing results.

    Returns counts: skipped_no_key | candidates | fetched | written | not_finished | errors
    """
    if not config.API_FOOTBALL_KEY:
        logger.warning("API_FOOTBALL_KEY not set — skipping score sync")
        return {
            "skipped_no_key": True,
            "candidates": 0,
            "fetched": 0,
            "written": 0,
            "not_finished": 0,
            "errors": 0,
        }

    candidates = fixtures_needing_scores(conn)
    by_id = {int(c["fixture_id"]): c for c in candidates if c.get("fixture_id") is not None}
    ids = list(by_id.keys())
    chunk = max(1, config.API_FOOTBALL_IDS_CHUNK)
    written = 0
    fetched = 0
    not_finished = 0
    errors = 0

    for i in range(0, len(ids), chunk):
        batch = ids[i : i + chunk]
        try:
            items = fetch_fixtures_by_ids(batch)
        except ApiFootballConfigError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("API-Football batch failed (%s): %s", batch[:3], exc)
            errors += 1
            continue
        fetched += len(items)
        seen = set()
        for item in items:
            score = parse_finished_score(item)
            if score is None:
                not_finished += 1
                continue
            fid = score.get("fixture_id")
            if fid is None or fid not in by_id:
                # Map by response fixture id
                fx_block = item.get("fixture") or {}
                fid = fx_block.get("id")
            if fid is None or int(fid) not in by_id:
                continue
            fid = int(fid)
            seen.add(fid)
            upsert_api_result(conn, by_id[fid], score)
            written += 1
        for fid in batch:
            if fid not in seen:
                # No payload or not finished — counted loosely via not_finished
                pass

    conn.commit()
    summary = {
        "skipped_no_key": False,
        "candidates": len(ids),
        "fetched": fetched,
        "written": written,
        "not_finished": not_finished,
        "errors": errors,
    }
    logger.info("Score sync: %s", summary)
    return summary
