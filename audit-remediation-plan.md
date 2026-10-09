# Audit Remediation Plan — DataGaffer (DG Index Daily)

**Goal:** Remediate all critical architectural bugs, data gaps, performance bottlenecks, calibration skew, and lint/typing errors identified during the comprehensive system audit.

---

## 1. Context & Identified Defects

During the thorough system audit of DataGaffer, nine distinct issues were uncovered across data ingestion, database integrity, predictive modeling, API compatibility, and performance:

1. **Missing League Country Mappings & Unresolvable Data Waste:**
   - `config/league_countries.json` lacks IDs `253` (Major League Soccer / USA) and `89` (Eerste Divisie / Netherlands).
   - `FD_NEW_COUNTRY` contains `"ARG"` (Argentina), which is not rated by DataGaffer, dumping 5,425 unmatchable orphan rows into `match_result` (50% of the table).
2. **Database Migration Gap (`fixture_id` on `match_result`):**
   - Older rows have `NULL` for `fixture_id` despite the ID existing in `raw_json`.
3. **Model & Calibration Joins Ignore `fixture_id`:**
   - `evaluate_joined()`, `fit_calibration()`, `fit_market_prob_calibration()`, and `collect_training_rows()` do not select `fixture_id` from `match_result` and strictly join by `(home_id, away_id, date)`. Any score resolved via `fixture_id` is lost to evaluation and calibration.
4. **Duplicate Observation Skew in Platt Calibration:**
   - `fit_calibration()` in `dg/model/supervised.py` lacks `p.id IN (SELECT MAX(id) FROM prediction GROUP BY fixture_id)`, causing multi-run fixtures to be heavily overweighted.
5. **44,000-Query Retrospective Loop Freeze:**
   - In `evaluate_joined()`, when `n == 0` for a new model tag, it iterates over all ~11,000 matches calling uncached `build_team_features()`, executing ~44,500 synchronous SQLite queries.
6. **N+1 Query Bottleneck on Web Dashboard:**
   - `load_dashboard_context()` fires a separate `SELECT` on `fixture_projection` for every single fixture rendered.
7. **Missing Heuristic Probability Fallback for BTTS & Team Goals:**
   - `btts`, `team_goals_home_1_5`, and `team_goals_away_1_5` omit `prob` when the sim prior is missing, permanently barring them from Strongest/AI Picks.
8. **OpenAI Client Incompatibility with `gpt-4o` / `gpt-4o-mini`:**
   - `openai_client.py` sends `reasoning_effort: low` by default, triggering `400 Bad Request` on non-reasoning models.
9. **Code Quality Blockers:**
   - 8 ruff lint errors and 97 mypy errors block `.agent/scripts/checklist.py`.

---

## 2. Actionable Tasks Breakdown

### Phase 1: Ingestion & Country Config Fixes (P0)
- [ ] **Task 1.1: Fix League Country Mappings**
  - File: `config/league_countries.json`
  - Action: Add `"253": "USA"` and `"89": "Netherlands"`.
  - Verify: `python -c "from dg.leagues import country_for_league_id; assert country_for_league_id(253) == 'USA'; assert country_for_league_id(89) == 'Netherlands'"`
- [ ] **Task 1.2: Remove Unused Argentine Feed & Purge Orphan Results**
  - File: `dg/config.py` and `dg/storage/migrations.py`
  - Action: Remove `"ARG"` from `FD_NEW_COUNTRY`. Add a migration helper `clean_unmatchable_results(conn)` that deletes `match_result` rows with `home_team_id IS NULL` for leagues not tracked by DataGaffer.
  - Verify: Inspect `match_result` count; ensure no new Argentine rows are fetched.

### Phase 2: Database Schema & Migration for `fixture_id` (P0)
- [ ] **Task 2.1: Backfill `match_result.fixture_id` from `raw_json`**
  - File: `dg/storage/migrations.py`
  - Action: Implement `backfill_match_result_fixture_id(conn)` to parse `raw_json` for `source in ('flashscore', 'api-football', 'manual')` and populate `fixture_id`. Register in `_ensure_columns()` and `init_db()`.
  - Verify: Query `SELECT count(*) FROM match_result WHERE source = 'flashscore' AND fixture_id IS NOT NULL`; verify count matches total flashscore rows.

### Phase 3: Model & Calibration Join Key Alignment (P0)
- [ ] **Task 3.1: Enable `fixture_id` Joins in `evaluate_joined`**
  - File: `dg/model/evaluate.py`
  - Action: Add `fixture_id` to the `SELECT` from `match_result`. In the row evaluation loop, check `result_index.lookup_result(index, ..., fixture_id=r["fixture_id"])` before falling back to `(hid, aid, day)`.
  - Verify: Run `.venv/bin/pytest tests/test_evaluate.py`.
- [ ] **Task 3.2: Enable `fixture_id` Joins & Eliminate Duplicates in `supervised.py`**
  - File: `dg/model/supervised.py`
  - Action:
    1. In `fit_calibration()`: Add `WHERE p.id IN (SELECT MAX(id) FROM prediction GROUP BY fixture_id)`.
    2. Add `fixture_id` to `match_result` SELECT queries in both `fit_calibration()` and `fit_market_prob_calibration()`.
    3. Update `result_index` lookup to match by `fixture_id` first.
  - Verify: Run `.venv/bin/pytest tests/test_supervised.py`.
- [ ] **Task 3.3: Enable `fixture_id` Joins in `residual.py`**
  - File: `dg/model/residual.py`
  - Action: Select `fixture_id` in `collect_training_rows` from `match_result` and join via `lookup_result` with `fixture_id`.
  - Verify: Run `.venv/bin/pytest tests/test_residual_and_brief.py`.

### Phase 4: Performance Optimizations (P1)
- [ ] **Task 4.1: Eliminate Retrospective 44k-Query Loop in `evaluate_joined`**
  - File: `dg/model/evaluate.py`
  - Action: In the `if n == 0:` fallback block, memoize `build_team_features` using a snapshot-scoped cache dictionary: `team_features_cache: Dict[int, Dict[str, Any]] = {}`. Cap `hist` to the most recent 200 matches.
  - Verify: Run `.venv/bin/python -m dg.cli backtest` and confirm execution completes in < 2 seconds.
- [ ] **Task 4.2: Eliminate Dashboard N+1 Query on `fixture_projection`**
  - File: `dg/report/loaders.py`
  - Action: Pre-fetch latest projections in a single batch query:
    `SELECT fixture_id, home_win_pct, draw_pct, away_win_pct, book_odds_json, sim_xg_home, sim_xg_away FROM fixture_projection WHERE id IN (SELECT MAX(id) FROM fixture_projection GROUP BY fixture_id)`
    and map by `fixture_id` in memory before the loop.
  - Verify: Run `.venv/bin/pytest tests/test_web.py`.

### Phase 5: Market Logic & OpenAI Client Hardening (P1)
- [ ] **Task 5.1: Add Heuristic Fallback Probabilities for BTTS & Team Goals**
  - File: `dg/model/markets.py`
  - Action: For `btts`, `team_goals_home_1_5`, and `team_goals_away_1_5`, when Poisson probability is `None`, fall back to `_heuristic_p_pos(score)` instead of leaving `prob` as `None`.
  - Verify: Run `.venv/bin/pytest tests/test_markets.py`.
- [ ] **Task 5.2: Guard `reasoning_effort` in `openai_client.py`**
  - File: `dg/ai/openai_client.py`
  - Action: Check `model` string; only attach `payload["reasoning_effort"]` if `any(m in (model or "").lower() for m in ("o1", "o3", "luna"))`.
  - Verify: Run `.venv/bin/pytest tests/test_ai_picks.py`.

### Phase 6: Code Quality, Lint & Master Validation (P2)
- [ ] **Task 6.1: Fix Ruff Linter Errors**
  - Files: `dg/leagues.py`, `dg/report/scoreboard.py`, and test files.
  - Action: Remove unused imports and re-sort test imports.
  - Verify: `.venv/bin/ruff check dg tests run_daily.py` returns 0 errors.
- [ ] **Task 6.2: Resolve Critical Mypy Errors**
  - Files: `dg/http.py`, `dg/model/sim_prior.py`, `dg/report/scoring_env.py`, `dg/web/app.py`.
  - Action: Add proper type guards and `# type: ignore` annotations where libraries use non-standard conventions (e.g. `requests` proxies).
  - Verify: `.venv/bin/mypy dg` passes with 0 errors.
- [ ] **Task 6.3: Run Master Checklist**
  - Action: Execute `python3 .agent/scripts/checklist.py .`
  - Verify: P0 Security, P1 Lint, P2 Schema, and P3 Tests all pass.

---

## 3. Done When
- [ ] `config/league_countries.json` resolves MLS (`253`) and Eerste Divisie (`89`).
- [ ] `FD_NEW_COUNTRY` no longer pulls unmatchable Argentine matches.
- [ ] All Flashscore results in `match_result` have their `fixture_id` backfilled.
- [ ] `evaluate_joined()`, `fit_calibration()`, `fit_market_prob_calibration()`, and `collect_training_rows()` correctly join matches using `fixture_id`.
- [ ] Platt calibration operates strictly on deduplicated predictions.
- [ ] `evaluate_joined()` executes in < 2 seconds without hanging on retrospective loops.
- [ ] Web dashboard runs with 0 N+1 queries.
- [ ] `openai_client.py` supports standard models (`gpt-4o`, `gpt-4o-mini`) without 400 errors.
- [ ] `ruff check` and `.agent/scripts/checklist.py .` pass cleanly.
