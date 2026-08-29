"""Central paths, URLs, and constants for the DG daily pipeline."""
from __future__ import annotations

import os
from pathlib import Path

# Project roots
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# DATA_DIR: Railway mounts a volume at /data and sets DATA_DIR=/data.
# Locally default to ./data under the project root.
_data_env = os.environ.get("DATA_DIR", "").strip()
if _data_env:
    DATA_DIR = Path(_data_env)
elif Path("/data").is_dir() and os.environ.get("RAILWAY_ENVIRONMENT"):
    DATA_DIR = Path("/data")
else:
    DATA_DIR = PROJECT_ROOT / "data"

RAW_DIR = DATA_DIR / "raw"
REPORTS_DIR = DATA_DIR / "reports"
LOGS_DIR = DATA_DIR / "logs"
ALIASES_DIR = DATA_DIR / "aliases"
CONFIG_DIR = PROJECT_ROOT / "config"
DB_PATH = DATA_DIR / "dg.db"
WEIGHTS_PATH = CONFIG_DIR / "weights_rule_v1.yaml"
MARKETS_WEIGHTS_PATH = CONFIG_DIR / "weights_markets_v1.yaml"
GOALS_WEIGHTS_PATH = CONFIG_DIR / "weights_goals_v1.yaml"
ALIASES_JSON = ALIASES_DIR / "football_data_couk.json"

# Web
PORT = int(os.environ.get("PORT", "8787"))
STALE_HOURS_THRESHOLD = float(os.environ.get("STALE_HOURS_THRESHOLD", "36"))

# HTTP
USER_AGENT = "DataGafferDailyPipeline/0.1 (+local research; polite once-daily)"
REQUEST_DELAY_SEC = 1.0
MAX_RETRIES = 3
REQUEST_TIMEOUT_SEC = 60

# DataGaffer endpoints
DG_BASE = "https://www.datagaffer.com"
DG_META_URL = f"{DG_BASE}/dg_meta.json"
DG_RATINGS_URL = f"{DG_BASE}/dg_ratings.json"
DG_TEAMS_URL = f"{DG_BASE}/teams.json"
DG_FIXTURE_FEEDS = (
    "fixtures_yesterday.json",
    "fixtures.json",
    "fixtures_tomorrow.json",
    "fixtures_day_after_tomorrow.json",
)

# football-data.co.uk
FD_BASE = "https://www.football-data.co.uk"
# Main league codes used by mmz4281/{season}/{code}.csv
FD_MAIN_CODES = (
    "E0",  # Premier League
    "E1",  # Championship
    "SP1",  # La Liga
    "D1",  # Bundesliga
    "D2",  # 2. Bundesliga
    "I1",  # Serie A
    "F1",  # Ligue 1
    "N1",  # Eredivisie
    "P1",  # Primeira Liga
    "SC0",  # Scottish Premiership
    "B1",  # Belgian Pro League
    "T1",  # Super Lig
)
# Extra /new/{COUNTRY}.csv feeds
FD_NEW_COUNTRY = (
    "BRA",  # Brazil Serie A
    "MEX",  # Liga MX
    "SWE",  # Allsvenskan
    "NOR",  # Eliteserien
    "DEN",  # Superliga
    "AUT",  # Austrian Bundesliga
    "SWZ",  # Swiss Super League
    "ARG",
)

# Contract / quality
MIN_TEAMS = 300
INDEX_KEYS = (
    "ppda_index",
    "pace_index",
    "agix_index",
    "nec_index",
    "control_index",
)
VENUE_INDEX_KEYS = (
    "home_pace_index",
    "away_pace_index",
    "home_agix_index",
    "away_agix_index",
    "home_nec_index",
    "away_nec_index",
    "home_control_index",
    "away_control_index",
    "home_off_eff_index",
    "away_off_eff_index",
    "home_def_eff_index",
    "away_def_eff_index",
    "home_rating_index",
    "away_rating_index",
    "home_rating_raw_index",
    "away_rating_raw_index",
)
REQUIRED_RATING_KEYS = (
    "team_id",
    "team",
    "league",
    "league_id",
    "ppda",
    "match_pace_shots",
    "chaos_index",
    "nec_chaos",
    "control_raw",
) + INDEX_KEYS + VENUE_INDEX_KEYS

# Warn-only: DG Rating strength fields (payload may rename; degrade gracefully)
RATING_STRENGTH_KEYS = (
    "DGRtg",
    "ORtg",
    "DRtg",
    "home_rating",
    "away_rating",
    "consistency",
    "coef_adj",
)

ANOMALY_Z_THRESHOLD = 2.0
ALIAS_FUZZY_THRESHOLD = 80
SUPERVISED_MIN_LABELS = 300
DEFAULT_FD_SEASON = "2627"

# Exit codes
EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_CRITICAL = 2


def ensure_dirs() -> None:
    """Create data directories if missing."""
    for d in (DATA_DIR, RAW_DIR, REPORTS_DIR, LOGS_DIR, ALIASES_DIR, CONFIG_DIR):
        d.mkdir(parents=True, exist_ok=True)
