-- DG Index Daily Pipeline schema
-- Idempotent on dg_snapshot.generated_at

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS dg_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL UNIQUE,
    scraped_at TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    n_teams INTEGER NOT NULL,
    meta_json TEXT
);

CREATE TABLE IF NOT EXISTS dg_team_rating (
    snapshot_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    team TEXT NOT NULL,
    league TEXT,
    league_id INTEGER,
    ppda REAL,
    match_pace_shots REAL,
    chaos_index REAL,
    nec_chaos REAL,
    control_raw REAL,
    ppda_index REAL,
    pace_index REAL,
    agix_index REAL,
    nec_index REAL,
    control_index REAL,
    dgr_index REAL,
    home_pace_index REAL,
    away_pace_index REAL,
    home_agix_index REAL,
    away_agix_index REAL,
    home_nec_index REAL,
    away_nec_index REAL,
    home_control_index REAL,
    away_control_index REAL,
    home_off_eff_index REAL,
    away_off_eff_index REAL,
    home_def_eff_index REAL,
    away_def_eff_index REAL,
    home_rating_index REAL,
    away_rating_index REAL,
    home_rating_raw_index REAL,
    away_rating_raw_index REAL,
    -- DG Rating strength fields (from payload; mixed-case keys mapped at ingest)
    dgrtg REAL,
    ortg REAL,
    drtg REAL,
    ortg_raw REAL,
    drtg_raw REAL,
    home_rating REAL,
    away_rating REAL,
    home_rating_raw REAL,
    away_rating_raw REAL,
    consistency REAL,
    consistency_index REAL,
    luck_per REAL,
    off_luck_per REAL,
    def_luck_per REAL,
    gf_per REAL,
    ga_per REAL,
    xgf_per REAL,
    xga_per REAL,
    coef_adj REAL,
    off_eff_index REAL,
    def_eff_index REAL,
    rank INTEGER,
    points REAL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, team_id),
    FOREIGN KEY (snapshot_id) REFERENCES dg_snapshot(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_team_rating_team ON dg_team_rating(team_id);
CREATE INDEX IF NOT EXISTS idx_team_rating_league ON dg_team_rating(league_id);

CREATE TABLE IF NOT EXISTS fixture (
    fixture_id INTEGER PRIMARY KEY,
    date_utc TEXT NOT NULL,
    league TEXT,
    league_id INTEGER,
    home_id INTEGER NOT NULL,
    away_id INTEGER NOT NULL,
    home_name TEXT,
    away_name TEXT,
    round TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    raw_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_fixture_date ON fixture(date_utc);

CREATE TABLE IF NOT EXISTS fixture_projection (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    snapshot_id INTEGER,
    sim_xg_home REAL,
    sim_xg_away REAL,
    home_win_pct REAL,
    draw_pct REAL,
    away_win_pct REAL,
    over_2_5_pct REAL,
    btts_pct REAL,
    matchup_pace_score REAL,
    book_odds_json TEXT,
    sim_stats_json TEXT,
    UNIQUE (fixture_id, observed_at),
    FOREIGN KEY (fixture_id) REFERENCES fixture(fixture_id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES dg_snapshot(id)
);

CREATE TABLE IF NOT EXISTS match_result (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    season TEXT,
    league_code TEXT,
    date TEXT NOT NULL,
    home_name TEXT NOT NULL,
    away_name TEXT NOT NULL,
    home_team_id INTEGER,
    away_team_id INTEGER,
    fthg INTEGER,
    ftag INTEGER,
    ftr TEXT,
    hthg INTEGER,
    htag INTEGER,
    hs INTEGER,
    as_shots INTEGER,
    hst INTEGER,
    ast INTEGER,
    hc INTEGER,
    ac INTEGER,
    hy INTEGER,
    ay INTEGER,
    hr INTEGER,
    ar INTEGER,
    closing_home REAL,
    closing_draw REAL,
    closing_away REAL,
    raw_json TEXT,
    UNIQUE (source, season, league_code, date, home_name, away_name)
);

CREATE INDEX IF NOT EXISTS idx_match_result_date ON match_result(date);
CREATE INDEX IF NOT EXISTS idx_match_result_teams ON match_result(home_team_id, away_team_id);

CREATE TABLE IF NOT EXISTS team_alias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_name TEXT NOT NULL,
    league_code TEXT,
    team_id INTEGER NOT NULL,
    confidence REAL,
    method TEXT,
    verified_at TEXT,
    UNIQUE (source, source_name, league_code)
);

CREATE TABLE IF NOT EXISTS prediction (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id INTEGER NOT NULL,
    snapshot_id INTEGER,
    predicted_at TEXT NOT NULL,
    model_version TEXT NOT NULL,
    lean TEXT NOT NULL,
    confidence TEXT NOT NULL,
    match_character TEXT,
    score REAL,
    scores_json TEXT,
    drivers_json TEXT,
    markets_json TEXT,
    probs_json TEXT,
    UNIQUE (fixture_id, model_version, predicted_at),
    FOREIGN KEY (fixture_id) REFERENCES fixture(fixture_id),
    FOREIGN KEY (snapshot_id) REFERENCES dg_snapshot(id)
);

CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT,
    exit_code INTEGER,
    stages_json TEXT,
    message TEXT
);

CREATE TABLE IF NOT EXISTS ingest_anomaly (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    team_id INTEGER,
    team TEXT,
    metric TEXT,
    detail TEXT,
    FOREIGN KEY (snapshot_id) REFERENCES dg_snapshot(id)
);
