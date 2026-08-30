"""API-Football (api-sports.io) client for finished fixture scores."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from dg import config
from dg.http import _polite_wait, get_session

logger = logging.getLogger(__name__)


class ApiFootballConfigError(RuntimeError):
    """Raised when API_FOOTBALL_KEY is missing."""


def _headers() -> Dict[str, str]:
    key = config.API_FOOTBALL_KEY
    if not key:
        raise ApiFootballConfigError(
            "API_FOOTBALL_KEY is not set — add it to the environment to sync scores"
        )
    return {
        "x-apisports-key": key,
        "Accept": "application/json",
    }


def _get_fixtures(params: Dict[str, str]) -> List[Dict[str, Any]]:
    """GET /fixtures with query params; return response list."""
    url = f"{config.API_FOOTBALL_BASE}/fixtures?{urlencode(params)}"
    _polite_wait()
    raw = get_session().get(
        url,
        headers=_headers(),
        timeout=config.REQUEST_TIMEOUT_SEC,
    )
    if raw.status_code >= 400:
        raise RuntimeError(f"API-Football HTTP {raw.status_code}: {raw.text[:200]}")
    data = raw.json()
    errors = data.get("errors")
    if isinstance(errors, dict) and errors:
        raise RuntimeError(f"API-Football errors: {errors}")
    if isinstance(errors, list) and errors:
        raise RuntimeError(f"API-Football errors: {errors}")
    response = data.get("response") or []
    if not isinstance(response, list):
        return []
    return response


def fetch_fixtures_by_date(day: str) -> List[Dict[str, Any]]:
    """GET /fixtures?date=YYYY-MM-DD (free-plan safe)."""
    day = (day or "").strip()[:10]
    if not day:
        return []
    response = _get_fixtures({"date": day})
    logger.info("API-Football date=%s returned %d fixtures", day, len(response))
    return response


def fetch_fixture_by_id(fixture_id: int) -> List[Dict[str, Any]]:
    """GET /fixtures?id={id} — singular id (free-plan safe; not batch ids)."""
    response = _get_fixtures({"id": str(int(fixture_id))})
    logger.info("API-Football id=%s returned %d fixtures", fixture_id, len(response))
    return response


def parse_finished_score(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extract FT score from an API-Football fixture payload.
    Returns None if not finished or score missing.
    """
    fixture = item.get("fixture") or {}
    status = (fixture.get("status") or {}).get("short") or ""
    if status not in config.API_FOOTBALL_FINISHED:
        return None
    goals = item.get("goals") or {}
    score = item.get("score") or {}
    ft = score.get("fulltime") or {}
    ht = score.get("halftime") or {}

    fthg = ft.get("home")
    ftag = ft.get("away")
    if fthg is None:
        fthg = goals.get("home")
    if ftag is None:
        ftag = goals.get("away")
    if fthg is None or ftag is None:
        return None
    try:
        fthg_i, ftag_i = int(fthg), int(ftag)
    except (TypeError, ValueError):
        return None

    if fthg_i > ftag_i:
        ftr = "H"
    elif ftag_i > fthg_i:
        ftr = "A"
    else:
        ftr = "D"

    hthg = ht.get("home")
    htag = ht.get("away")
    try:
        hthg_i = int(hthg) if hthg is not None else None
        htag_i = int(htag) if htag is not None else None
    except (TypeError, ValueError):
        hthg_i, htag_i = None, None

    fid = fixture.get("id")
    return {
        "fixture_id": int(fid) if fid is not None else None,
        "status": status,
        "fthg": fthg_i,
        "ftag": ftag_i,
        "ftr": ftr,
        "hthg": hthg_i,
        "htag": htag_i,
    }
