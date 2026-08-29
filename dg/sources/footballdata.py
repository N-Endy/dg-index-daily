"""football-data.co.uk CSV client."""
from __future__ import annotations

import csv
import gzip
import io
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from dg import config
from dg.http import fetch

logger = logging.getLogger(__name__)


def _archive_csv(name: str, content: bytes, day: Optional[date] = None) -> Path:
    day = day or datetime.now(timezone.utc).date()
    d = config.RAW_DIR / day.isoformat()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.gz"
    with gzip.open(path, "wb") as f:
        f.write(content)
    return path


def _decode_csv(content: bytes) -> List[Dict[str, str]]:
    text = content.decode("latin-1")
    # Strip BOM-ish Div key if present
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        cleaned = {}
        for k, v in row.items():
            if k is None:
                continue
            key = k.lstrip("\ufeff").replace("ï»¿", "")
            cleaned[key] = v
        rows.append(cleaned)
    return rows


def fetch_main_league(
    season: str,
    code: str,
    *,
    archive: bool = True,
) -> Tuple[List[Dict[str, str]], bytes]:
    url = f"{config.FD_BASE}/mmz4281/{season}/{code}.csv"
    resp = fetch(url)
    if archive:
        _archive_csv(f"fd_{season}_{code}.csv", resp.content)
    return _decode_csv(resp.content), resp.content


def fetch_new_country(
    country: str,
    *,
    archive: bool = True,
) -> Tuple[List[Dict[str, str]], bytes]:
    url = f"{config.FD_BASE}/new/{country}.csv"
    resp = fetch(url)
    if archive:
        _archive_csv(f"fd_new_{country}.csv", resp.content)
    return _decode_csv(resp.content), resp.content


def iter_main_leagues(
    season: str,
    codes: Optional[Iterable[str]] = None,
) -> Iterable[Tuple[str, List[Dict[str, str]]]]:
    codes = tuple(codes) if codes else config.FD_MAIN_CODES
    for code in codes:
        try:
            rows, _ = fetch_main_league(season, code)
            logger.info("Fetched %s/%s: %d rows", season, code, len(rows))
            yield code, rows
        except Exception as exc:  # noqa: BLE001 — continue other leagues
            logger.warning("Skip %s/%s: %s", season, code, exc)


def iter_new_countries(
    countries: Optional[Iterable[str]] = None,
) -> Iterable[Tuple[str, List[Dict[str, str]]]]:
    countries = tuple(countries) if countries else config.FD_NEW_COUNTRY
    for country in countries:
        try:
            rows, _ = fetch_new_country(country)
            logger.info("Fetched new/%s: %d rows", country, len(rows))
            yield country, rows
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skip new/%s: %s", country, exc)
