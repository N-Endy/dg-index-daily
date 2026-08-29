"""Team name alias resolution between football-data.co.uk and DG team_id."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from thefuzz import fuzz, process

from dg import config

logger = logging.getLogger(__name__)

SOURCE = "football-data.co.uk"

# Hand-curated seeds for common mismatches
SEED_ALIASES: Dict[str, str] = {
    "Man City": "Manchester City",
    "Man United": "Manchester United",
    "Man Utd": "Manchester United",
    "Nott'm Forest": "Nottingham Forest",
    "Nottingham Forest": "Nottingham Forest",
    "Hull": "Hull City",
    "Wolves": "Wolverhampton Wanderers",
    "Spurs": "Tottenham",
    "Tottenham": "Tottenham",
    "Newcastle": "Newcastle",
    "Newcastle United": "Newcastle",
    "Brighton": "Brighton",
    "West Ham": "West Ham",
    "Leicester": "Leicester",
    "Sheffield United": "Sheffield Utd",
    "Sheffield Utd": "Sheffield Utd",
    "Atletico Madrid": "Atletico Madrid",
    "Ath Madrid": "Atletico Madrid",
    "Bayern Munich": "Bayern Munich",
    "Dortmund": "Dortmund",
    "Inter": "Inter",
    "PSG": "Paris Saint Germain",
    "Paris SG": "Paris Saint Germain",
}


def load_alias_file(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or config.ALIASES_JSON
    if not path.exists():
        return {"source": SOURCE, "aliases": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_alias_file(data: Dict[str, Any], path: Optional[Path] = None) -> None:
    path = path or config.ALIASES_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def dg_name_index(conn, snapshot_id: Optional[int] = None) -> Dict[str, Tuple[int, str]]:
    """Map lowercased DG team name -> (team_id, canonical name)."""
    if snapshot_id is None:
        row = conn.execute(
            "SELECT id FROM dg_snapshot ORDER BY generated_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return {}
        snapshot_id = int(row["id"])
    rows = conn.execute(
        "SELECT team_id, team FROM dg_team_rating WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchall()
    return {r["team"].lower(): (int(r["team_id"]), r["team"]) for r in rows}


def resolve_name(
    source_name: str,
    name_index: Dict[str, Tuple[int, str]],
    *,
    threshold: int = config.ALIAS_FUZZY_THRESHOLD,
) -> Optional[Tuple[int, str, float, str]]:
    """
    Resolve a football-data.co.uk name to (team_id, dg_name, confidence, method).
    """
    if not source_name or not name_index:
        return None

    # Seed map first
    seeded = SEED_ALIASES.get(source_name)
    if seeded and seeded.lower() in name_index:
        tid, canon = name_index[seeded.lower()]
        return tid, canon, 1.0, "seed"

    key = source_name.lower()
    if key in name_index:
        tid, canon = name_index[key]
        return tid, canon, 1.0, "exact"

    choices = list(name_index.keys())
    match = process.extractOne(key, choices, scorer=fuzz.token_sort_ratio)
    if match is None:
        return None
    # thefuzz returns (choice, score)
    matched_key, score = match[0], match[1]
    if score < threshold:
        return None
    tid, canon = name_index[matched_key]
    return tid, canon, score / 100.0, "fuzzy"


def upsert_alias(
    conn,
    source_name: str,
    team_id: int,
    *,
    league_code: Optional[str] = None,
    confidence: float = 1.0,
    method: str = "manual",
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO team_alias (source, source_name, league_code, team_id, confidence, method, verified_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, source_name, league_code) DO UPDATE SET
            team_id=excluded.team_id,
            confidence=excluded.confidence,
            method=excluded.method,
            verified_at=excluded.verified_at
        """,
        (SOURCE, source_name, league_code or "", team_id, confidence, method, now),
    )


def sync_aliases_to_json(conn) -> None:
    rows = conn.execute(
        "SELECT source_name, league_code, team_id, confidence, method FROM team_alias WHERE source = ?",
        (SOURCE,),
    ).fetchall()
    data = {
        "source": SOURCE,
        "aliases": [
            {
                "source_name": r["source_name"],
                "league_code": r["league_code"],
                "team_id": r["team_id"],
                "confidence": r["confidence"],
                "method": r["method"],
            }
            for r in rows
        ],
    }
    save_alias_file(data)


def lookup_cached(conn, source_name: str, league_code: Optional[str] = None) -> Optional[int]:
    row = conn.execute(
        """
        SELECT team_id FROM team_alias
        WHERE source = ? AND source_name = ? AND league_code = ?
        """,
        (SOURCE, source_name, league_code or ""),
    ).fetchone()
    if row:
        return int(row["team_id"])
    # try without league
    row = conn.execute(
        """
        SELECT team_id FROM team_alias
        WHERE source = ? AND source_name = ?
        ORDER BY confidence DESC LIMIT 1
        """,
        (SOURCE, source_name),
    ).fetchone()
    return int(row["team_id"]) if row else None


def resolve_or_learn(
    conn,
    source_name: str,
    name_index: Dict[str, Tuple[int, str]],
    *,
    league_code: Optional[str] = None,
) -> Optional[int]:
    cached = lookup_cached(conn, source_name, league_code)
    if cached is not None:
        return cached
    resolved = resolve_name(source_name, name_index)
    if resolved is None:
        return None
    tid, _canon, conf, method = resolved
    upsert_alias(
        conn,
        source_name,
        tid,
        league_code=league_code,
        confidence=conf,
        method=method,
    )
    return tid
