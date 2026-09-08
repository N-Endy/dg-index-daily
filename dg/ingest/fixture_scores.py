"""Sync finished scores into match_result (Flashscore primary, API-Football optional)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from dg import config
from dg.report.results_attach import load_result_index, lookup_result
from dg.sources.flashscore import (
    FlashscoreBlockedError,
    FlashscoreCooldownError,
    FlashscoreUnavailableError,
    league_match_score,
    scrape_finished_scores,
    team_match_score,
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

    def _stat(key: str) -> Optional[int]:
        raw = score.get(key)
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    stats = {
        "hs": _stat("hs"),
        "as_shots": _stat("as_shots"),
        "hst": _stat("hst"),
        "ast": _stat("ast"),
        "hc": _stat("hc"),
        "ac": _stat("ac"),
        "hy": _stat("hy"),
        "ay": _stat("ay"),
        "hr": _stat("hr"),
        "ar": _stat("ar"),
    }
    raw = {
        "fixture_id": fixture.get("fixture_id"),
        "source": source,
        "scraped_home": score.get("home"),
        "scraped_away": score.get("away"),
        "scraped_league": score.get("league"),
        "match_id": score.get("match_id"),
        "fthg": fthg,
        "ftag": ftag,
        "ftr": ftr,
        **{k: v for k, v in stats.items() if v is not None},
    }
    conn.execute(
        """
        INSERT INTO match_result (
            source, season, league_code, date, home_name, away_name,
            home_team_id, away_team_id, fthg, ftag, ftr, hthg, htag,
            hs, as_shots, hst, ast, hc, ac, hy, ay, hr, ar,
            raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, season, league_code, date, home_name, away_name)
        DO UPDATE SET
            home_team_id=excluded.home_team_id,
            away_team_id=excluded.away_team_id,
            fthg=excluded.fthg, ftag=excluded.ftag, ftr=excluded.ftr,
            hthg=excluded.hthg, htag=excluded.htag,
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
            stats["hs"],
            stats["as_shots"],
            stats["hst"],
            stats["ast"],
            stats["hc"],
            stats["ac"],
            stats["hy"],
            stats["ay"],
            stats["hr"],
            stats["ar"],
            json.dumps(raw),
        ),
    )


def _fixture_day(fx: Dict[str, Any]) -> Optional[str]:
    return (fx.get("date_utc") or "")[:10] or None


def day_offsets_for_candidates(candidates: List[Dict[str, Any]]) -> List[int]:
    """
    Map candidate fixture UTC days to flashscore.mobi ?d= offsets.
    Always include today (0); clamp older days to [-lookback, 0].
    When many distinct days exist, keep newest + oldest (max_offsets).
    """
    lookback = max(0, int(config.FLASHSCORE_SCORE_LOOKBACK_DAYS))
    max_offsets = max(1, int(config.FLASHSCORE_SCORE_MAX_OFFSETS))
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
        if raw > 0:
            continue
        offsets.add(max(-lookback, raw))
    ordered = sorted(offsets, reverse=True)  # 0, -1, -2, …
    if len(ordered) <= max_offsets:
        return ordered
    newest_n = max(1, max_offsets // 2)
    oldest_n = max_offsets - newest_n
    selected = set(ordered[:newest_n]) | set(ordered[-oldest_n:])
    return sorted(selected, reverse=True)


def _score_fixture_row_pair(
    fx: Dict[str, Any],
    row: Dict[str, Any],
    *,
    today: Optional[Any] = None,
) -> Optional[Tuple[float, bool]]:
    """
    Rank a single fixture vs scraped row for auto-match.
    Returns (rank, flipped) or None if below thresholds.
    """
    home, away = row.get("home") or "", row.get("away") or ""
    sc_league = (row.get("league") or "").strip()
    min_name = int(config.FLASHSCORE_NAME_MATCH_MIN)
    min_league = float(config.FLASHSCORE_AUTO_MIN_LEAGUE)
    strong_name_min = int(config.FLASHSCORE_STRONG_NAME_MIN)
    strong_league_floor = float(config.FLASHSCORE_STRONG_NAME_MIN_LEAGUE)
    day_penalty_max = int(config.FLASHSCORE_DAY_PENALTY_MAX)
    scrape_off = row.get("day_offset")
    try:
        scrape_off_i = int(scrape_off) if scrape_off is not None else None
    except (TypeError, ValueError):
        scrape_off_i = None

    day = _fixture_day(fx)
    if not day:
        return None
    try:
        fday = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        return None

    h_direct = team_match_score(home, fx.get("home_name") or "")
    a_direct = team_match_score(away, fx.get("away_name") or "")
    h_flip = team_match_score(home, fx.get("away_name") or "")
    a_flip = team_match_score(away, fx.get("home_name") or "")
    direct_ok = h_direct >= min_name and a_direct >= min_name
    flip_ok = h_flip >= min_name and a_flip >= min_name
    if not direct_ok and not flip_ok:
        return None
    direct_avg = (h_direct + a_direct) / 2.0
    flip_avg = (h_flip + a_flip) / 2.0
    if flip_ok and (not direct_ok or flip_avg >= direct_avg + 5):
        name_avg = flip_avg
        flipped = True
        side_min = min(h_flip, a_flip)
    else:
        name_avg = direct_avg
        flipped = False
        side_min = min(h_direct, a_direct)
    strong_name = side_min >= strong_name_min

    fx_league = (fx.get("league") or "").strip()
    lg_sc = 0.0
    if fx_league and sc_league:
        lg_sc = league_match_score(fx_league, sc_league)
        if lg_sc < min_league:
            # Strong names may pass a weaker league floor (still blocks youth/country ~0.25).
            if not (strong_name and lg_sc >= strong_league_floor):
                return None

    ref_day = today if today is not None else _utcnow().date()
    if scrape_off_i is not None:
        expected = ref_day.toordinal() + scrape_off_i
        day_penalty = abs(fday.toordinal() - expected)
        if day_penalty > day_penalty_max:
            return None
    else:
        day_penalty = abs((fday - ref_day).days)

    rank = name_avg + (10.0 * lg_sc) - day_penalty - (2.0 if flipped else 0.0)
    return rank, flipped


def find_flashscore_row_for_fixture(
    fixture: Dict[str, Any],
    rows: List[Dict[str, Any]],
    used_fingerprints: Set[str],
) -> Optional[Tuple[Dict[str, Any], bool]]:
    """Best unused scraped row for one awaiting fixture (fixture-first sync)."""
    from dg.sources.flashscore import row_fingerprint

    today = _utcnow().date()
    best_row: Optional[Dict[str, Any]] = None
    best_flipped = False
    best_score = -1.0
    for row in rows:
        fp = row_fingerprint(row)
        if fp in used_fingerprints:
            continue
        scored = _score_fixture_row_pair(fixture, row, today=today)
        if scored is None:
            continue
        rank, flipped = scored
        if rank > best_score:
            best_score = rank
            best_row = row
            best_flipped = flipped
    if best_row is None:
        return None
    return best_row, best_flipped


def match_flashscore_row_to_fixture(
    row: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    used_ids: Set[int],
) -> Optional[Tuple[Dict[str, Any], bool]]:
    """
    Find best fixture for a scraped finished score.
    Returns (fixture, flipped) or None. When flipped, swap fthg/ftag before upsert.
    """
    today = _utcnow().date()
    best: Optional[Dict[str, Any]] = None
    best_flipped = False
    best_score = -1.0

    for fx in candidates:
        fid = fx.get("fixture_id")
        if fid is None or int(fid) in used_ids:
            continue
        scored = _score_fixture_row_pair(fx, row, today=today)
        if scored is None:
            continue
        rank, flipped = scored
        if rank > best_score:
            best_score = rank
            best = fx
            best_flipped = flipped
    if best is None:
        return None
    return best, best_flipped


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


def _write_flashscore_matches(
    conn,
    candidates: List[Dict[str, Any]],
    rows: List[Dict[str, Any]],
    used_fps: Set[str],
) -> int:
    """Fixture-first auto-match; returns number of match_result writes."""
    from dg.sources.flashscore import row_fingerprint

    written = 0
    for fx in candidates:
        matched = find_flashscore_row_for_fixture(fx, rows, used_fps)
        if matched is None:
            continue
        row, flipped = matched
        score = dict(row)
        if flipped:
            score["fthg"], score["ftag"] = score.get("ftag"), score.get("fthg")
        upsert_score_result(conn, fx, score, source=SOURCE_FLASHSCORE)
        used_fps.add(row_fingerprint(row))
        written += 1
    return written


def _near_miss_diagnostics(
    candidates: List[Dict[str, Any]],
    rows: List[Dict[str, Any]],
    *,
    limit: int = 8,
) -> List[Dict[str, Any]]:
    """Best soft name/league ranks for still-awaiting fixtures (ops logging)."""
    out: List[Dict[str, Any]] = []
    for fx in candidates[: max(limit * 2, limit)]:
        best: Optional[Dict[str, Any]] = None
        for row in rows:
            home, away = row.get("home") or "", row.get("away") or ""
            h_d = team_match_score(home, fx.get("home_name") or "")
            a_d = team_match_score(away, fx.get("away_name") or "")
            h_f = team_match_score(home, fx.get("away_name") or "")
            a_f = team_match_score(away, fx.get("home_name") or "")
            direct_avg = (h_d + a_d) / 2.0
            flip_avg = (h_f + a_f) / 2.0
            if flip_avg >= direct_avg + 5:
                name_avg = flip_avg
                flipped = True
            else:
                name_avg = direct_avg
                flipped = False
            fx_lg = (fx.get("league") or "").strip()
            sc_lg = (row.get("league") or "").strip()
            lg_sc = league_match_score(fx_lg, sc_lg) if fx_lg and sc_lg else 0.0
            cand = {
                "fixture": f"{fx.get('home_name')} vs {fx.get('away_name')}",
                "date": _fixture_day(fx),
                "name_avg": round(name_avg, 1),
                "league_score": round(lg_sc, 2),
                "scraped": f"{home} vs {away}",
                "scraped_league": sc_lg or None,
                "flipped": flipped,
            }
            if best is None or cand["name_avg"] > best["name_avg"]:
                best = cand
        if best:
            out.append(best)
        if len(out) >= limit:
            break
    return out


def sync_flashscore_scores(
    conn,
    *,
    scraped_rows: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Match scraped Flashscore rows onto fixtures needing scores."""
    from dg.report.score_hints import load_recent_flashscore_rows, persist_flashscore_rows
    from dg.sources.flashscore import row_fingerprint

    initial_candidates = fixtures_needing_scores(conn)
    summary: Dict[str, Any] = {
        "source": SOURCE_FLASHSCORE,
        "candidates": len(initial_candidates),
        "day_offsets": [],
        "scraped": 0,
        "written": 0,
        "rematch_written": 0,
        "unmatched": 0,
        "near_misses": [],
        "errors": 0,
        "skipped_cooldown": False,
        "skipped_blocked": False,
        "skipped_unavailable": False,
    }
    if not initial_candidates:
        return summary

    used_fps: Set[str] = set()
    candidates = initial_candidates

    # Rematch from persisted scrapes before a live Playwright fetch (skip when
    # caller injects scraped_rows for tests / offline runs).
    if scraped_rows is None:
        stored = load_recent_flashscore_rows(conn, limit=8000)
        if stored:
            rematch_n = _write_flashscore_matches(conn, candidates, stored, used_fps)
            if rematch_n:
                conn.commit()
            summary["rematch_written"] = rematch_n
            summary["written"] += rematch_n
            candidates = fixtures_needing_scores(conn)
            if not candidates:
                summary["unmatched"] = 0
                logger.info("Flashscore sync: %s", summary)
                return summary

    offsets = day_offsets_for_candidates(candidates)
    summary["day_offsets"] = offsets

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
        # Still report near-misses vs stored rows when scrape blocked.
        stored = load_recent_flashscore_rows(conn, limit=4000)
        if candidates and stored:
            summary["near_misses"] = _near_miss_diagnostics(candidates, stored)
            logger.warning("Flashscore near-misses (cooldown): %s", summary["near_misses"])
        return summary
    except FlashscoreBlockedError as exc:
        logger.warning("Flashscore blocked: %s", exc)
        summary["skipped_blocked"] = True
        summary["errors"] = 1
        stored = load_recent_flashscore_rows(conn, limit=4000)
        if candidates and stored:
            summary["near_misses"] = _near_miss_diagnostics(candidates, stored)
            logger.warning("Flashscore near-misses (blocked): %s", summary["near_misses"])
        return summary
    except FlashscoreUnavailableError as exc:
        logger.warning("Flashscore unavailable: %s", exc)
        summary["skipped_unavailable"] = True
        summary["errors"] = 1
        return summary

    summary["scraped"] = len(rows)
    persisted = persist_flashscore_rows(conn, rows)
    summary["persisted"] = persisted
    conn.commit()

    live_written = _write_flashscore_matches(conn, candidates, rows, used_fps)
    summary["written"] += live_written
    summary["unmatched"] = max(0, len(rows) - sum(
        1 for r in rows if row_fingerprint(r) in used_fps
    ))

    conn.commit()
    still_need = fixtures_needing_scores(conn)
    if still_need and rows:
        summary["near_misses"] = _near_miss_diagnostics(still_need, rows)
    if summary["written"] == 0 and initial_candidates:
        sample = [
            f"{c.get('home_name')} vs {c.get('away_name')} ({(c.get('date_utc') or '')[:10]})"
            for c in still_need[:8]
        ]
        logger.warning(
            "Flashscore wrote 0/%d candidates (scraped=%d, offsets=%s); sample: %s; near_misses: %s",
            len(initial_candidates),
            len(rows),
            offsets,
            sample,
            summary.get("near_misses"),
        )
    elif still_need and summary.get("near_misses"):
        logger.info(
            "Flashscore still awaiting %d; near_misses: %s",
            len(still_need),
            summary["near_misses"],
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


_STAT_PAIR_KEYS = (
    ("hs", "as_shots"),
    ("hst", "ast"),
    ("hc", "ac"),
    ("hy", "ay"),
    ("hr", "ar"),
)


def _fixtures_missing_stats(conn) -> List[Dict[str, Any]]:
    """Predicted past fixtures whose joined result has no corner stats."""
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
        mr = lookup_result(
            index,
            home_id=d.get("home_id"),
            away_id=d.get("away_id"),
            date_utc=d.get("date_utc"),
        )
        if mr is None:
            continue
        get = mr.__getitem__ if not isinstance(mr, dict) else mr.get
        try:
            hc = get("hc")
        except (KeyError, IndexError, TypeError):
            hc = None
        if hc is not None:
            continue
        out.append(d)
    return out


def _swap_stat_pairs(score: Dict[str, Any]) -> None:
    for a, b in _STAT_PAIR_KEYS:
        if a in score or b in score:
            score[a], score[b] = score.get(b), score.get(a)


def sync_match_stats(conn) -> Dict[str, Any]:
    """
    Fetch Flashscore ?t=stats for finished fixtures whose joined result
    still lacks match stats. Isolated from sync-scores so a block cannot
    disable goal sync.
    """
    summary: Dict[str, Any] = {
        "enabled": bool(config.FLASHSCORE_STATS_ENABLED),
        "candidates": 0,
        "with_match_id": 0,
        "ids_bootstrapped": 0,
        "fetched": 0,
        "written": 0,
        "skipped_disabled": False,
        "skipped_cooldown": False,
        "skipped_blocked": False,
        "skipped_unavailable": False,
        "errors": 0,
    }
    if not config.FLASHSCORE_STATS_ENABLED:
        summary["skipped_disabled"] = True
        return summary

    candidates = _fixtures_missing_stats(conn)
    summary["candidates"] = len(candidates)
    if not candidates:
        return summary

    from dg.report.score_hints import load_recent_flashscore_rows, persist_flashscore_rows
    from dg.sources.flashscore import fetch_match_stats

    scraped = load_recent_flashscore_rows(conn, limit=8000)
    scraped_with_id = [r for r in scraped if (r.get("match_id") or "").strip()]
    if not scraped_with_id:
        # Score sync exits early once goals exist, so match_id may never have
        # been written. Scrape day pages here solely to bootstrap IDs.
        offsets = day_offsets_for_candidates(candidates)
        try:
            bootstrap_rows = scrape_finished_scores(day_offsets=offsets)
        except FlashscoreCooldownError as exc:
            logger.warning("%s", exc)
            summary["skipped_cooldown"] = True
            summary["errors"] = 1
            return summary
        except FlashscoreBlockedError as exc:
            logger.warning("Flashscore blocked while bootstrapping match_ids: %s", exc)
            summary["skipped_blocked"] = True
            summary["errors"] = 1
            return summary
        except FlashscoreUnavailableError as exc:
            logger.warning(
                "Flashscore unavailable while bootstrapping match_ids: %s", exc
            )
            summary["skipped_unavailable"] = True
            summary["errors"] = 1
            return summary
        persist_flashscore_rows(conn, bootstrap_rows)
        conn.commit()
        summary["ids_bootstrapped"] = sum(
            1 for r in bootstrap_rows if (r.get("match_id") or "").strip()
        )
        scraped = load_recent_flashscore_rows(conn, limit=8000)
        scraped_with_id = [r for r in scraped if (r.get("match_id") or "").strip()]
        if not scraped_with_id:
            logger.info("sync-match-stats: no flashscore_row match_ids after bootstrap")
            return summary

    planned: List[Tuple[Dict[str, Any], Dict[str, Any], bool]] = []
    used_fps: Set[str] = set()
    from dg.sources.flashscore import row_fingerprint

    for fx in candidates:
        matched = find_flashscore_row_for_fixture(fx, scraped_with_id, used_fps)
        if matched is None:
            continue
        row, flipped = matched
        mid = (row.get("match_id") or "").strip()
        if not mid:
            continue
        planned.append((fx, row, flipped))
        used_fps.add(row_fingerprint(row))

    summary["with_match_id"] = len(planned)
    if not planned:
        return summary

    ids = [(r.get("match_id") or "").strip() for _, r, _ in planned]
    try:
        stats_by_id = fetch_match_stats(ids)
    except FlashscoreCooldownError as exc:
        logger.warning("%s", exc)
        summary["skipped_cooldown"] = True
        summary["errors"] = 1
        return summary
    except FlashscoreBlockedError as exc:
        logger.warning("Flashscore stats blocked: %s", exc)
        summary["skipped_blocked"] = True
        summary["errors"] = 1
        return summary
    except FlashscoreUnavailableError as exc:
        logger.warning("Flashscore stats unavailable: %s", exc)
        summary["skipped_unavailable"] = True
        summary["errors"] = 1
        return summary

    summary["fetched"] = len(stats_by_id)
    for fx, row, flipped in planned:
        mid = (row.get("match_id") or "").strip()
        stats = stats_by_id.get(mid)
        if not stats:
            continue
        score = {
            "home": row.get("home"),
            "away": row.get("away"),
            "league": row.get("league"),
            "match_id": mid,
            "fthg": row.get("fthg"),
            "ftag": row.get("ftag"),
            **stats,
        }
        if flipped:
            score["fthg"], score["ftag"] = score.get("ftag"), score.get("fthg")
            _swap_stat_pairs(score)
        upsert_score_result(conn, fx, score, source=SOURCE_FLASHSCORE)
        summary["written"] += 1

    if summary["written"]:
        conn.commit()
    logger.info("Match stats sync: %s", summary)
    return summary


# Back-compat alias used by older tests
def upsert_api_result(conn, fixture: Dict[str, Any], score: Dict[str, Any]) -> None:
    upsert_score_result(conn, fixture, score, source=SOURCE_API)
