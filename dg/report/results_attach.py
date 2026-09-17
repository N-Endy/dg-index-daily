"""Attach match_result FT scores to fixture/prediction rows for the web UI."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

_FTR_TO_LEAN = {"H": "Home", "D": "Draw", "A": "Away"}


def normalize_result_day(date_str: Optional[str]) -> Optional[str]:
    """
    Normalize football-data or ISO date strings to YYYY-MM-DD.
    FD CSVs typically use DD/MM/YYYY.
    """
    if not date_str or not isinstance(date_str, str):
        return None
    raw = date_str.strip()
    if not raw:
        return None
    # Already ISO day or datetime prefix
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    # DD/MM/YYYY or D/M/YYYY
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    # ISO with time / Z
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def fixture_day(date_utc: Optional[str]) -> Optional[str]:
    return normalize_result_day(date_utc)


def ft_score_display(fthg: Any, ftag: Any) -> Optional[str]:
    if fthg is None or ftag is None:
        return None
    try:
        return f"{int(fthg)}–{int(ftag)}"
    except (TypeError, ValueError):
        return None


def ftr_to_lean(ftr: Optional[str]) -> Optional[str]:
    if not ftr:
        return None
    return _FTR_TO_LEAN.get(str(ftr).strip().upper())


def lean_result(lean: Optional[str], ftr: Optional[str]) -> Tuple[str, str]:
    """Return (key, label) for match-winner lean vs FT result."""
    actual = ftr_to_lean(ftr)
    if not actual or not lean:
        return "pending", ""
    if lean == actual:
        return "hit", "Lean hit"
    return "miss", "Lean miss"


def market_lean_result(lean: Optional[str], label: Optional[str]) -> Tuple[str, str]:
    if label is None or not lean:
        return "pending", ""
    if lean == label:
        return "hit", "Hit"
    return "miss", "Miss"


def result_fields_from_row(mr: Any) -> Dict[str, Any]:
    """Normalize a match_result sqlite row into attachable fields."""
    if mr is None:
        return {
            "completed": False,
            "ft_home": None,
            "ft_away": None,
            "ftr": None,
            "ft_score": None,
            "result_row": None,
        }
    # sqlite3.Row or mapping
    get = mr.__getitem__ if not isinstance(mr, dict) else mr.get

    def _g(key: str) -> Any:
        try:
            return get(key)
        except (KeyError, IndexError, TypeError):
            return None

    fthg, ftag, ftr = _g("fthg"), _g("ftag"), _g("ftr")
    row = {
        "fthg": fthg,
        "ftag": ftag,
        "ftr": ftr,
        "hthg": _g("hthg"),
        "htag": _g("htag"),
        "hs": _g("hs"),
        "as_shots": _g("as_shots"),
        "hst": _g("hst"),
        "ast": _g("ast"),
        "hc": _g("hc"),
        "ac": _g("ac"),
        "hy": _g("hy"),
        "ay": _g("ay"),
        "hr": _g("hr"),
        "ar": _g("ar"),
        "fixture_id": _g("fixture_id"),
    }
    return {
        "completed": bool(ftr),
        "ft_home": fthg,
        "ft_away": ftag,
        "ftr": ftr,
        "ft_score": ft_score_display(fthg, ftag),
        "result_row": row,
    }


_STAT_KEYS = ("hs", "as_shots", "hst", "ast", "hc", "ac", "hy", "ay", "hr", "ar")


def _stat_richness(mr: Any) -> int:
    """Count of non-null match-stat columns (corners/shots/cards)."""
    get = mr.__getitem__ if not isinstance(mr, dict) else mr.get
    n = 0
    for k in _STAT_KEYS:
        try:
            if get(k) is not None:
                n += 1
        except (KeyError, IndexError, TypeError):
            continue
    return n


@dataclass
class ResultIndex:
    """Joinable FT results by fixture_id (preferred) and team-day key."""

    by_teams: Dict[Tuple[int, int, str], Any] = field(default_factory=dict)
    by_fixture: Dict[int, Any] = field(default_factory=dict)

    def get(self, key: Tuple[int, int, str], default: Any = None) -> Any:
        """Dict-like access for callers that index by team-day tuples."""
        return self.by_teams.get(key, default)

    def __contains__(self, key: object) -> bool:
        return key in self.by_teams

    def __getitem__(self, key: Tuple[int, int, str]) -> Any:
        return self.by_teams[key]


def build_result_index(rows: List[Any]) -> ResultIndex:
    """
    Index match_result rows by fixture_id and (home_team_id, away_team_id, day).

    When multiple sources collide on the same key, prefer the row with more
    non-null match stats. Equal richness keeps the incumbent (stable).
    """
    by_teams: Dict[Tuple[int, int, str], Any] = {}
    by_fixture: Dict[int, Any] = {}
    for mr in rows:
        get = mr.__getitem__ if not isinstance(mr, dict) else mr.get
        try:
            if not get("ftr"):
                continue
        except (KeyError, IndexError, TypeError):
            continue

        try:
            fid = get("fixture_id")
        except (KeyError, IndexError, TypeError):
            fid = None
        if fid is not None:
            try:
                fid_i = int(fid)
            except (TypeError, ValueError):
                fid_i = None
            if fid_i is not None:
                existing_f = by_fixture.get(fid_i)
                if existing_f is None or _stat_richness(mr) > _stat_richness(existing_f):
                    by_fixture[fid_i] = mr

        try:
            hid, aid = get("home_team_id"), get("away_team_id")
            day = normalize_result_day(get("date"))
        except (KeyError, IndexError, TypeError):
            continue
        if hid is None or aid is None or not day:
            continue
        key = (int(hid), int(aid), day)
        existing = by_teams.get(key)
        if existing is None or _stat_richness(mr) > _stat_richness(existing):
            by_teams[key] = mr
    return ResultIndex(by_teams=by_teams, by_fixture=by_fixture)


def lookup_result(
    index: Union[ResultIndex, Dict[Tuple[int, int, str], Any]],
    *,
    home_id: Any,
    away_id: Any,
    date_utc: Optional[str],
    fixture_id: Any = None,
) -> Optional[Any]:
    by_fixture = getattr(index, "by_fixture", None)
    if by_fixture is not None and fixture_id is not None:
        try:
            hit = by_fixture.get(int(fixture_id))
            if hit is not None:
                return hit
        except (TypeError, ValueError):
            pass

    by_teams = getattr(index, "by_teams", index)
    day = fixture_day(date_utc)
    if home_id is None or away_id is None or not day:
        return None
    try:
        return by_teams.get((int(home_id), int(away_id), day))
    except (TypeError, ValueError):
        return None


def attach_result_to_prediction(
    pred: Dict[str, Any],
    index: Union[ResultIndex, Dict[Tuple[int, int, str], Any]],
) -> Dict[str, Any]:
    mr = lookup_result(
        index,
        home_id=pred.get("home_id"),
        away_id=pred.get("away_id"),
        date_utc=pred.get("date_utc"),
        fixture_id=pred.get("fixture_id"),
    )
    pred.update(result_fields_from_row(mr))
    return pred


def load_result_index(conn: Any) -> ResultIndex:
    rows = conn.execute(
        """
        SELECT home_team_id, away_team_id, date, fthg, ftag, ftr,
               hthg, htag, hs, as_shots, hst, ast, hc, ac, hy, ay, hr, ar,
               fixture_id
        FROM match_result
        WHERE ftr IS NOT NULL
        """
    ).fetchall()
    return build_result_index(list(rows))
