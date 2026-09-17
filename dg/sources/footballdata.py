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
from dg.http import UnsafeRedirectError, fetch

logger = logging.getLogger(__name__)


def _archive_csv(name: str, content: bytes, day: Optional[date] = None) -> Path:
    day = day or datetime.now(timezone.utc).date()
    d = config.RAW_DIR / day.isoformat()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.gz"
    with gzip.open(path, "wb") as f:
        f.write(content)
    return path


def _newest_archive(name: str) -> Optional[Path]:
    """Return newest RAW_DIR/*/name.gz if any exist."""
    if not config.RAW_DIR.is_dir():
        return None
    matches = sorted(
        config.RAW_DIR.glob(f"*/{name}.gz"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def _load_archive(name: str) -> Optional[bytes]:
    path = _newest_archive(name)
    if path is None:
        return None
    with gzip.open(path, "rb") as f:
        content = f.read()
    logger.info("Loaded archived CSV %s (%d bytes)", path, len(content))
    return content


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


def _from_archive_or_none(archive_name: str) -> Optional[List[Dict[str, str]]]:
    content = _load_archive(archive_name)
    if content is None:
        return None
    return _decode_csv(content)


def iter_main_leagues(
    season: str,
    codes: Optional[Iterable[str]] = None,
) -> Iterable[Tuple[str, List[Dict[str, str]]]]:
    codes = tuple(codes) if codes else config.FD_MAIN_CODES
    live_blocked = False
    for code in codes:
        archive_name = f"fd_{season}_{code}.csv"
        if live_blocked:
            rows = _from_archive_or_none(archive_name)
            if rows is not None:
                logger.info(
                    "Fetched %s/%s from archive: %d rows", season, code, len(rows)
                )
                yield code, rows
            else:
                logger.warning("Skip %s/%s: live blocked and no archive", season, code)
            continue
        try:
            rows, _ = fetch_main_league(season, code)
            logger.info("Fetched %s/%s: %d rows", season, code, len(rows))
            yield code, rows
        except UnsafeRedirectError as exc:
            live_blocked = True
            logger.error(
                "football-data.co.uk redirected to a private/loopback host "
                "(%s); skipping remaining live fetches and using archives if any",
                exc,
            )
            rows = _from_archive_or_none(archive_name)
            if rows is not None:
                logger.info(
                    "Fetched %s/%s from archive: %d rows", season, code, len(rows)
                )
                yield code, rows
            else:
                logger.warning("Skip %s/%s: %s (no archive)", season, code, exc)
        except Exception as exc:  # noqa: BLE001 — continue other leagues
            logger.warning("Skip %s/%s: %s", season, code, exc)


def iter_new_countries(
    countries: Optional[Iterable[str]] = None,
) -> Iterable[Tuple[str, List[Dict[str, str]]]]:
    countries = tuple(countries) if countries else config.FD_NEW_COUNTRY
    live_blocked = False
    for country in countries:
        archive_name = f"fd_new_{country}.csv"
        if live_blocked:
            rows = _from_archive_or_none(archive_name)
            if rows is not None:
                logger.info("Fetched new/%s from archive: %d rows", country, len(rows))
                yield country, rows
            else:
                logger.warning("Skip new/%s: live blocked and no archive", country)
            continue
        try:
            rows, _ = fetch_new_country(country)
            logger.info("Fetched new/%s: %d rows", country, len(rows))
            yield country, rows
        except UnsafeRedirectError as exc:
            live_blocked = True
            logger.error(
                "football-data.co.uk redirected to a private/loopback host "
                "(%s); skipping remaining live fetches and using archives if any",
                exc,
            )
            rows = _from_archive_or_none(archive_name)
            if rows is not None:
                logger.info("Fetched new/%s from archive: %d rows", country, len(rows))
                yield country, rows
            else:
                logger.warning("Skip new/%s: %s (no archive)", country, exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skip new/%s: %s", country, exc)
