"""Ingest football-data.co.uk match results into match_result."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from dg.ingest.aliases import dg_name_index, resolve_or_learn, sync_aliases_to_json
from dg.sources import footballdata as fd

logger = logging.getLogger(__name__)


def _int(val: Any) -> Optional[int]:
    if val is None or val == "":
        return None
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


def _float(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _closing_odds(row: Dict[str, str]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    # Prefer Pinnacle closing (PSCH/PSCD/PSCA) then BbMx / B365
    for hk, dk, ak in (
        ("PSCH", "PSCD", "PSCA"),
        ("B365CH", "B365CD", "B365CA"),
        ("B365H", "B365D", "B365A"),
        ("AvgCH", "AvgCD", "AvgCA"),
    ):
        h, d, a = _float(row.get(hk)), _float(row.get(dk)), _float(row.get(ak))
        if h and d and a:
            return h, d, a
    return None, None, None


def ingest_main_rows(
    conn,
    rows: List[Dict[str, str]],
    *,
    season: str,
    league_code: str,
    name_index: Dict[str, Tuple[int, str]],
) -> int:
    n = 0
    for row in rows:
        home = row.get("HomeTeam") or row.get("Home") or ""
        away = row.get("AwayTeam") or row.get("Away") or ""
        date = row.get("Date") or ""
        if not home or not away or not date:
            continue
        hid = resolve_or_learn(conn, home, name_index, league_code=league_code)
        aid = resolve_or_learn(conn, away, name_index, league_code=league_code)
        ch, cd, ca = _closing_odds(row)
        conn.execute(
            """
            INSERT INTO match_result (
                source, season, league_code, date, home_name, away_name,
                home_team_id, away_team_id, fthg, ftag, ftr, hthg, htag,
                hs, as_shots, hst, ast, hc, ac, hy, ay, hr, ar,
                closing_home, closing_draw, closing_away, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, season, league_code, date, home_name, away_name)
            DO UPDATE SET
                home_team_id=excluded.home_team_id,
                away_team_id=excluded.away_team_id,
                fthg=excluded.fthg, ftag=excluded.ftag, ftr=excluded.ftr,
                hthg=COALESCE(excluded.hthg, match_result.hthg),
                htag=COALESCE(excluded.htag, match_result.htag),
                hs=COALESCE(excluded.hs, match_result.hs),
                as_shots=COALESCE(excluded.as_shots, match_result.as_shots),
                hst=COALESCE(excluded.hst, match_result.hst),
                ast=COALESCE(excluded.ast, match_result.ast),
                hc=COALESCE(excluded.hc, match_result.hc),
                ac=COALESCE(excluded.ac, match_result.ac),
                hy=COALESCE(excluded.hy, match_result.hy),
                ay=COALESCE(excluded.ay, match_result.ay),
                hr=COALESCE(excluded.hr, match_result.hr),
                ar=COALESCE(excluded.ar, match_result.ar),
                closing_home=excluded.closing_home,
                closing_draw=excluded.closing_draw,
                closing_away=excluded.closing_away,
                raw_json=excluded.raw_json
            """,
            (
                "football-data.co.uk",
                season,
                league_code,
                date,
                home,
                away,
                hid,
                aid,
                _int(row.get("FTHG") or row.get("HG")),
                _int(row.get("FTAG") or row.get("AG")),
                row.get("FTR") or row.get("Res"),
                _int(row.get("HTHG")),
                _int(row.get("HTAG")),
                _int(row.get("HS")),
                _int(row.get("AS")),
                _int(row.get("HST")),
                _int(row.get("AST")),
                _int(row.get("HC")),
                _int(row.get("AC")),
                _int(row.get("HY")),
                _int(row.get("AY")),
                _int(row.get("HR")),
                _int(row.get("AR")),
                ch,
                cd,
                ca,
                json.dumps(row),
            ),
        )
        n += 1
    return n


def ingest_new_rows(
    conn,
    rows: List[Dict[str, str]],
    *,
    country: str,
    name_index: Dict[str, Tuple[int, str]],
    season_filter: Optional[str] = None,
) -> int:
    n = 0
    for row in rows:
        season = str(row.get("Season") or "")
        if season_filter and season != season_filter and season != f"20{season_filter[:2]}/20{season_filter[2:]}":
            # Allow both "2026" and "2627"-style; keep recent seasons if filter is like 2627
            if season_filter and len(season_filter) == 4 and season.isdigit():
                yy = int(season_filter[:2])
                if int(season) < 2000 + yy - 1:
                    continue
        home = row.get("Home") or ""
        away = row.get("Away") or ""
        date = row.get("Date") or ""
        if not home or not away or not date:
            continue
        league = row.get("League") or country
        hid = resolve_or_learn(conn, home, name_index, league_code=league)
        aid = resolve_or_learn(conn, away, name_index, league_code=league)
        ch = _float(row.get("PSCH"))
        cd = _float(row.get("PSCD"))
        ca = _float(row.get("PSCA"))
        conn.execute(
            """
            INSERT INTO match_result (
                source, season, league_code, date, home_name, away_name,
                home_team_id, away_team_id, fthg, ftag, ftr,
                closing_home, closing_draw, closing_away, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, season, league_code, date, home_name, away_name)
            DO UPDATE SET
                home_team_id=excluded.home_team_id,
                away_team_id=excluded.away_team_id,
                fthg=excluded.fthg, ftag=excluded.ftag, ftr=excluded.ftr,
                closing_home=excluded.closing_home,
                closing_draw=excluded.closing_draw,
                closing_away=excluded.closing_away,
                raw_json=excluded.raw_json
            """,
            (
                "football-data.co.uk",
                season,
                league,
                date,
                home,
                away,
                hid,
                aid,
                _int(row.get("HG")),
                _int(row.get("AG")),
                row.get("Res"),
                ch,
                cd,
                ca,
                json.dumps(row),
            ),
        )
        n += 1
    return n


def backfill_results(
    conn,
    *,
    season: str,
    include_new: bool = True,
) -> Dict[str, int]:
    name_index = dg_name_index(conn)
    if not name_index:
        raise RuntimeError("No DG ratings in DB — ingest ratings before results")

    counts: Dict[str, int] = {}
    for code, rows in fd.iter_main_leagues(season):
        counts[code] = ingest_main_rows(
            conn, rows, season=season, league_code=code, name_index=name_index
        )
    if include_new:
        # Map 2627 -> prefer season year 2026/2027
        year_hint = f"20{season[:2]}" if len(season) == 4 else None
        for country, rows in fd.iter_new_countries():
            counts[f"new/{country}"] = ingest_new_rows(
                conn,
                rows,
                country=country,
                name_index=name_index,
                season_filter=year_hint,
            )
    sync_aliases_to_json(conn)
    total = sum(counts.values())
    logger.info("Backfilled %d match results across %d feeds", total, len(counts))
    return counts
