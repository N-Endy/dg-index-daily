"""League display helpers — country prefix from API-Football league_id."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from dg import config

_LEAGUE_COUNTRIES_PATH = config.CONFIG_DIR / "league_countries.json"
_LABEL_SEP = " – "


@lru_cache(maxsize=1)
def load_league_countries() -> Dict[int, str]:
    """Return league_id → country name from config/league_countries.json."""
    if not _LEAGUE_COUNTRIES_PATH.exists():
        return {}
    try:
        raw = json.loads(_LEAGUE_COUNTRIES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[int, str] = {}
    for key, val in raw.items():
        if val is None:
            continue
        try:
            out[int(key)] = str(val).strip()
        except (TypeError, ValueError):
            continue
    return out


def country_for_league_id(league_id: Any) -> Optional[str]:
    if league_id is None:
        return None
    try:
        lid = int(league_id)
    except (TypeError, ValueError):
        return None
    return load_league_countries().get(lid)


def resolve_league_country(
    *,
    league_id: Any = None,
    league_country: Optional[str] = None,
    feed_country: Optional[str] = None,
) -> Optional[str]:
    """Prefer stored/feed country, else static league_id map."""
    for candidate in (feed_country, league_country):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return country_for_league_id(league_id)


def format_league_label(league_name: Optional[str], *, country: Optional[str] = None) -> str:
    """Return 'COUNTRY – LEAGUE' (uppercase) when country is known."""
    name = (league_name or "").strip()
    if not name:
        return ""
    if not country or not str(country).strip():
        return name
    return f"{str(country).strip().upper()}{_LABEL_SEP}{name.upper()}"


def league_display_for_row(row: Dict[str, Any]) -> str:
    """Compute display label from a fixture/prediction dict."""
    country = resolve_league_country(
        league_id=row.get("league_id"),
        league_country=row.get("league_country"),
    )
    return format_league_label(row.get("league"), country=country)


def attach_league_display(row: Dict[str, Any]) -> Dict[str, Any]:
    row["league_display"] = league_display_for_row(row)
    return row
