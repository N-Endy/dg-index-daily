"""DataGaffer public JSON source clients."""
from __future__ import annotations

import gzip
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dg import config
from dg.http import fetch

logger = logging.getLogger(__name__)


@dataclass
class MetaInfo:
    generated_at: str
    raw: Dict[str, Any]


@dataclass
class ArchivedPayload:
    name: str
    path: Path
    sha256: str
    data: Any


def _archive_dir(day: Optional[date] = None) -> Path:
    day = day or datetime.now(timezone.utc).date()
    d = config.RAW_DIR / day.isoformat()
    d.mkdir(parents=True, exist_ok=True)
    return d


def archive_bytes(name: str, content: bytes, day: Optional[date] = None) -> Path:
    """Gzip-write raw bytes under data/raw/YYYY-MM-DD/."""
    path = _archive_dir(day) / f"{name}.gz"
    with gzip.open(path, "wb") as f:
        f.write(content)
    logger.info("Archived %s (%d bytes) -> %s", name, len(content), path)
    return path


def fetch_meta() -> Tuple[MetaInfo, bytes, str]:
    resp = fetch(config.DG_META_URL)
    data = json.loads(resp.text)
    if "generated_at" not in data:
        raise ValueError("dg_meta.json missing generated_at")
    return MetaInfo(generated_at=data["generated_at"], raw=data), resp.content, resp.sha256


def fetch_ratings(*, archive: bool = True, day: Optional[date] = None) -> ArchivedPayload:
    resp = fetch(config.DG_RATINGS_URL)
    data = json.loads(resp.text)
    if not isinstance(data, list):
        raise ValueError("dg_ratings.json must be a list")
    path = archive_bytes("dg_ratings.json", resp.content, day) if archive else Path()
    return ArchivedPayload(
        name="dg_ratings.json",
        path=path,
        sha256=resp.sha256,
        data=data,
    )


def fetch_fixture_feed(
    filename: str,
    *,
    archive: bool = True,
    day: Optional[date] = None,
) -> ArchivedPayload:
    url = f"{config.DG_BASE}/{filename}"
    resp = fetch(url)
    data = json.loads(resp.text)
    if not isinstance(data, list):
        raise ValueError(f"{filename} must be a list")
    path = archive_bytes(filename, resp.content, day) if archive else Path()
    return ArchivedPayload(
        name=filename,
        path=path,
        sha256=resp.sha256,
        data=data,
    )


def fetch_all_fixtures(
    *,
    archive: bool = True,
    day: Optional[date] = None,
) -> List[ArchivedPayload]:
    out: List[ArchivedPayload] = []
    for name in config.DG_FIXTURE_FEEDS:
        out.append(fetch_fixture_feed(name, archive=archive, day=day))
    return out


def fetch_teams(*, archive: bool = True, day: Optional[date] = None) -> ArchivedPayload:
    resp = fetch(config.DG_TEAMS_URL)
    data = json.loads(resp.text)
    path = archive_bytes("teams.json", resp.content, day) if archive else Path()
    return ArchivedPayload(name="teams.json", path=path, sha256=resp.sha256, data=data)


def load_archived_json(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)
