"""Flashscore near-miss hints and manual score confirm (MatchPredictor-style)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from dg import config
from dg.ingest.fixture_scores import upsert_score_result
from dg.sources.flashscore import league_match_score, row_fingerprint, team_match_score

logger = logging.getLogger(__name__)

SOURCE_MANUAL = "flashscore-manual"
SOURCE_OPERATOR_MANUAL = "manual"
ORIENTATION_EPS = 5  # prefer flipped only if clearly better
LEAGUE_RANK_BOOST = 5.0  # small boost so same-league ties win


def auto_promote_soft_near_misses(
    conn,
    candidates: List[Dict[str, Any]],
    scraped_rows: List[Dict[str, Any]],
) -> int:
    """
    Upsert unique high-confidence soft near-misses as flashscore.
    Requires side/avg/league floors and a clear gap over the runner-up.
    """
    from dg.ingest.fixture_scores import SOURCE_FLASHSCORE
    from dg.sources.flashscore import row_fingerprint

    if not candidates or not scraped_rows:
        return 0
    side_floor = int(config.FLASHSCORE_AUTO_SOFT_MIN_SIDE)
    avg_floor = int(config.FLASHSCORE_AUTO_SOFT_MIN_AVG)
    league_floor = float(config.FLASHSCORE_AUTO_SOFT_MIN_LEAGUE)
    gap_min = float(config.FLASHSCORE_AUTO_SOFT_UNIQUE_GAP)
    used_fps: set = set()
    written = 0
    for fx in candidates:
        hits = find_score_near_misses(
            fx,
            scraped_rows,
            min_side=side_floor,
            min_avg=avg_floor,
            min_league=league_floor,
            limit=2,
        )
        if not hits:
            continue
        best = hits[0]
        if (
            int(best.get("home_score") or 0) < side_floor
            or int(best.get("away_score") or 0) < side_floor
        ):
            continue
        avg = (float(best["home_score"]) + float(best["away_score"])) / 2.0
        if avg < avg_floor:
            continue
        if float(best.get("league_score") or 0.0) < league_floor:
            continue
        if len(hits) >= 2:
            second = hits[1]
            second_avg = (
                float(second["home_score"]) + float(second["away_score"])
            ) / 2.0
            if avg - second_avg < gap_min:
                continue
        # Prefer DB row id when present; otherwise match by scraped names/score.
        row = None
        rid = best.get("id")
        if rid is not None:
            row = next((r for r in scraped_rows if r.get("id") == rid), None)
        if row is None:
            for r in scraped_rows:
                if (
                    r.get("home") == best.get("scraped_home")
                    and r.get("away") == best.get("scraped_away")
                    and int(r.get("fthg", -1)) == int(best.get("fthg", -2))
                    and int(r.get("ftag", -1)) == int(best.get("ftag", -2))
                ):
                    row = r
                    break
        if row is None:
            continue
        fp = row_fingerprint(row)
        if fp in used_fps:
            continue
        score = {
            "home": best.get("scraped_home") or best.get("home"),
            "away": best.get("scraped_away") or best.get("away"),
            "league": best.get("league"),
            "fthg": best["fthg"],
            "ftag": best["ftag"],
            "match_id": row.get("match_id"),
        }
        upsert_score_result(conn, fx, score, source=SOURCE_FLASHSCORE)
        used_fps.add(fp)
        written += 1
    return written


def submit_manual_score(
    conn,
    fixture_id: int,
    fthg: int,
    ftag: int,
) -> Dict[str, Any]:
    """
    Operator FT escape hatch for past predicted fixtures without a joinable result.
    """
    from datetime import datetime, timezone

    from dg.report.results_attach import load_result_index, lookup_result

    if int(fthg) < 0 or int(ftag) < 0:
        raise ValueError("scores must be non-negative integers")
    fx = load_fixture_for_confirm(conn, fixture_id)
    if not fx:
        raise ValueError("fixture not found")
    # Must have a prediction (board fixture).
    has_pred = conn.execute(
        "SELECT 1 FROM prediction WHERE fixture_id = ? LIMIT 1",
        (int(fixture_id),),
    ).fetchone()
    if not has_pred:
        raise ValueError("fixture has no prediction — not a board fixture")
    date_utc = fx.get("date_utc") or ""
    try:
        kickoff = datetime.fromisoformat(date_utc.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("fixture has invalid kickoff") from exc
    if kickoff > datetime.now(timezone.utc):
        raise ValueError("fixture kickoff is still in the future")
    index = load_result_index(conn)
    if lookup_result(
        index,
        home_id=fx.get("home_id"),
        away_id=fx.get("away_id"),
        date_utc=date_utc,
        fixture_id=fx.get("fixture_id"),
    ):
        raise ValueError("fixture already has a final score")
    score = {"fthg": int(fthg), "ftag": int(ftag)}
    upsert_score_result(conn, fx, score, source=SOURCE_OPERATOR_MANUAL)
    conn.commit()
    return {
        "fixture_id": int(fixture_id),
        "ft_score": f"{int(fthg)}–{int(ftag)}",
        "fthg": int(fthg),
        "ftag": int(ftag),
        "home_name": fx.get("home_name"),
        "away_name": fx.get("away_name"),
    }


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def persist_flashscore_rows(
    conn,
    rows: List[Dict[str, Any]],
    *,
    day_offset: Optional[int] = None,
) -> int:
    """Upsert finished scrape rows by fingerprint. Returns rows written/updated."""
    now = _utcnow_iso()
    batch_size = max(1, int(config.FLASHSCORE_PERSIST_BATCH))
    sql = """
        INSERT INTO flashscore_row (
            scraped_at, day_offset, league, home, away, fthg, ftag,
            kickoff_hint, match_id, fingerprint
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(fingerprint) DO UPDATE SET
            scraped_at=excluded.scraped_at,
            day_offset=COALESCE(excluded.day_offset, flashscore_row.day_offset),
            kickoff_hint=COALESCE(excluded.kickoff_hint, flashscore_row.kickoff_hint),
            match_id=COALESCE(excluded.match_id, flashscore_row.match_id)
        """
    pending: List[tuple] = []
    n = 0

    def flush() -> None:
        if not pending:
            return
        conn.executemany(sql, pending)
        conn.commit()
        pending.clear()

    for row in rows:
        home = (row.get("home") or "").strip()
        away = (row.get("away") or "").strip()
        if not home or not away:
            continue
        try:
            fthg = int(row["fthg"])
            ftag = int(row["ftag"])
        except (KeyError, TypeError, ValueError):
            continue
        fp = row_fingerprint(row)
        off = row.get("day_offset", day_offset)
        mid = (row.get("match_id") or "").strip() or None
        pending.append(
            (
                now,
                off,
                row.get("league") or "",
                home,
                away,
                fthg,
                ftag,
                row.get("kickoff_hint") or "",
                mid,
                fp,
            )
        )
        n += 1
        if len(pending) >= batch_size:
            flush()
    flush()
    return n


def load_recent_flashscore_rows(conn, *, limit: int = 4000) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, scraped_at, day_offset, league, home, away, fthg, ftag, kickoff_hint, match_id
        FROM flashscore_row
        ORDER BY scraped_at DESC, id DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def _orientation_scores(
    fx_home: str,
    fx_away: str,
    sc_home: str,
    sc_away: str,
) -> Tuple[int, int, bool]:
    """Return (home_score, away_score, flipped)."""
    direct_h = team_match_score(fx_home, sc_home)
    direct_a = team_match_score(fx_away, sc_away)
    flip_h = team_match_score(fx_home, sc_away)
    flip_a = team_match_score(fx_away, sc_home)
    direct_avg = (direct_h + direct_a) / 2.0
    flip_avg = (flip_h + flip_a) / 2.0
    if flip_avg >= direct_avg + ORIENTATION_EPS:
        return flip_h, flip_a, True
    return direct_h, direct_a, False


def find_score_near_misses(
    fixture: Dict[str, Any],
    scraped_rows: List[Dict[str, Any]],
    *,
    min_side: Optional[int] = None,
    min_avg: Optional[int] = None,
    min_league: Optional[float] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Soft candidates for an unscored fixture (MatchPredictor hint band)."""
    side_floor = int(min_side if min_side is not None else config.FLASHSCORE_HINT_MIN_SIDE)
    avg_floor = int(min_avg if min_avg is not None else config.FLASHSCORE_HINT_MIN_AVG)
    league_floor = float(
        min_league if min_league is not None else config.FLASHSCORE_HINT_MIN_LEAGUE
    )
    fx_home = fixture.get("home_name") or ""
    fx_away = fixture.get("away_name") or ""
    fx_league = (fixture.get("league") or "").strip()
    if not fx_home or not fx_away:
        return []

    ranked: List[Tuple[float, Dict[str, Any]]] = []
    for row in scraped_rows:
        sc_league = (row.get("league") or "").strip()
        lg_sc = 0.0
        if fx_league and sc_league:
            lg_sc = league_match_score(fx_league, sc_league)
            if lg_sc < league_floor:
                continue
        h_sc, a_sc, flipped = _orientation_scores(
            fx_home, fx_away, row.get("home") or "", row.get("away") or ""
        )
        if h_sc < side_floor or a_sc < side_floor:
            continue
        avg = (h_sc + a_sc) / 2.0
        if avg < avg_floor:
            continue
        # Prefer stronger name match; slight penalty if flipped; boost same league
        rank = avg - (2.0 if flipped else 0.0) + (LEAGUE_RANK_BOOST * lg_sc)
        fthg, ftag = int(row["fthg"]), int(row["ftag"])
        if flipped:
            fthg, ftag = ftag, fthg
            display_home, display_away = row.get("away"), row.get("home")
        else:
            display_home, display_away = row.get("home"), row.get("away")
        reason = f"Name similarity {int(avg)}"
        if flipped:
            reason += " (teams flipped)"
        if fx_league and sc_league:
            reason += f" · league {int(round(lg_sc * 100))}"
        ranked.append(
            (
                rank,
                {
                    "id": row.get("id"),
                    "home": display_home,
                    "away": display_away,
                    "scraped_home": row.get("home"),
                    "scraped_away": row.get("away"),
                    "fthg": fthg,
                    "ftag": ftag,
                    "score": f"{fthg}–{ftag}",
                    "league": row.get("league") or "",
                    "flipped": flipped,
                    "home_score": h_sc,
                    "away_score": a_sc,
                    "league_score": lg_sc,
                    "reason": reason,
                },
            )
        )
    ranked.sort(key=lambda x: x[0], reverse=True)
    out = []
    seen_ids = set()
    for _, cand in ranked:
        cid = cand.get("id")
        if cid is None or cid in seen_ids:
            continue
        seen_ids.add(cid)
        out.append(cand)
        if len(out) >= limit:
            break
    return out


def attach_score_hints(
    predictions: List[Dict[str, Any]],
    scraped_rows: List[Dict[str, Any]],
) -> None:
    """Mutate awaiting predictions with score_hint_candidates."""
    if not scraped_rows:
        for p in predictions:
            p.setdefault("score_hint_candidates", [])
        return
    for p in predictions:
        if p.get("completed") or not p.get("awaiting_score"):
            p["score_hint_candidates"] = []
            continue
        p["score_hint_candidates"] = find_score_near_misses(p, scraped_rows)


def load_fixture_for_confirm(conn, fixture_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        """
        SELECT fixture_id, date_utc, league, home_name, away_name, home_id, away_id
        FROM fixture WHERE fixture_id = ?
        """,
        (int(fixture_id),),
    ).fetchone()
    return dict(row) if row else None


def load_flashscore_row(conn, row_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        """
        SELECT id, scraped_at, day_offset, league, home, away, fthg, ftag, kickoff_hint, match_id
        FROM flashscore_row WHERE id = ?
        """,
        (int(row_id),),
    ).fetchone()
    return dict(row) if row else None


def confirm_score_link(conn, fixture_id: int, flashscore_row_id: int) -> Dict[str, Any]:
    """
    Soft-validate and upsert match_result. Raises ValueError on soft-match failure.
    """
    fx = load_fixture_for_confirm(conn, fixture_id)
    if not fx:
        raise ValueError("fixture not found")
    row = load_flashscore_row(conn, flashscore_row_id)
    if not row:
        raise ValueError("flashscore row not found")

    hits = find_score_near_misses(fx, [row], limit=1)
    if not hits or hits[0].get("id") != row["id"]:
        raise ValueError("scraped row is not a soft match for this fixture")

    hit = hits[0]
    score = {
        "home": hit.get("scraped_home") or hit.get("home"),
        "away": hit.get("scraped_away") or hit.get("away"),
        "league": hit.get("league"),
        "fthg": hit["fthg"],
        "ftag": hit["ftag"],
    }
    upsert_score_result(conn, fx, score, source=SOURCE_MANUAL)
    conn.commit()
    return {
        "fixture_id": int(fixture_id),
        "ft_score": f"{hit['fthg']}–{hit['ftag']}",
        "fthg": hit["fthg"],
        "ftag": hit["ftag"],
        "home_name": fx.get("home_name"),
        "away_name": fx.get("away_name"),
    }


def apply_score_hints_to_predictions(predictions: List[Dict[str, Any]]) -> None:
    """Load recent flashscore_row scrapes and attach soft near-miss candidates."""
    if not predictions:
        return
    from dg.report.loaders import get_connection

    conn = get_connection()
    try:
        rows = load_recent_flashscore_rows(conn)
        attach_score_hints(predictions, rows)
    finally:
        conn.close()
