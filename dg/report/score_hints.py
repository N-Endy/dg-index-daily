"""Flashscore near-miss hints and manual score confirm (MatchPredictor-style)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from dg import config
from dg.ingest.fixture_scores import upsert_score_result
from dg.sources.flashscore import row_fingerprint, team_match_score

logger = logging.getLogger(__name__)

SOURCE_MANUAL = "flashscore-manual"
ORIENTATION_EPS = 5  # prefer flipped only if clearly better


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
    n = 0
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
        conn.execute(
            """
            INSERT INTO flashscore_row (
                scraped_at, day_offset, league, home, away, fthg, ftag, kickoff_hint, fingerprint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fingerprint) DO UPDATE SET
                scraped_at=excluded.scraped_at,
                day_offset=COALESCE(excluded.day_offset, flashscore_row.day_offset),
                kickoff_hint=COALESCE(excluded.kickoff_hint, flashscore_row.kickoff_hint)
            """,
            (
                now,
                off,
                row.get("league") or "",
                home,
                away,
                fthg,
                ftag,
                row.get("kickoff_hint") or "",
                fp,
            ),
        )
        n += 1
    return n


def load_recent_flashscore_rows(conn, *, limit: int = 4000) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, scraped_at, day_offset, league, home, away, fthg, ftag, kickoff_hint
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
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Soft candidates for an unscored fixture (MatchPredictor hint band)."""
    side_floor = int(min_side if min_side is not None else config.FLASHSCORE_HINT_MIN_SIDE)
    avg_floor = int(min_avg if min_avg is not None else config.FLASHSCORE_HINT_MIN_AVG)
    fx_home = fixture.get("home_name") or ""
    fx_away = fixture.get("away_name") or ""
    if not fx_home or not fx_away:
        return []

    ranked: List[Tuple[float, Dict[str, Any]]] = []
    for row in scraped_rows:
        h_sc, a_sc, flipped = _orientation_scores(
            fx_home, fx_away, row.get("home") or "", row.get("away") or ""
        )
        if h_sc < side_floor or a_sc < side_floor:
            continue
        avg = (h_sc + a_sc) / 2.0
        if avg < avg_floor:
            continue
        # Prefer stronger name match; slight penalty if flipped
        rank = avg - (2.0 if flipped else 0.0)
        fthg, ftag = int(row["fthg"]), int(row["ftag"])
        if flipped:
            fthg, ftag = ftag, fthg
            display_home, display_away = row.get("away"), row.get("home")
        else:
            display_home, display_away = row.get("home"), row.get("away")
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
                    "reason": (
                        f"Name similarity {int(avg)}"
                        + (" (teams flipped)" if flipped else "")
                    ),
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
        SELECT id, scraped_at, day_offset, league, home, away, fthg, ftag, kickoff_hint
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
