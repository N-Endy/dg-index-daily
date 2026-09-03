# Deploy on Railway

This app is a **website** plus an **in-container refresh schedule**. Railway only allows
**one volume per service**, so we do **not** use a second cron service sharing `/data`.

Instead:

1. **Web service** — dashboard (`uvicorn`) + background loop that runs match and Flashscore jobs on a Nigerian (WAT) schedule
2. **Volume at `/data`** — SQLite DB + reports (mounted only on that one service)

## One-time setup

### 1. Create the project

- New Project → Deploy from the GitHub repo (`N-Endy/dg-index-daily`).
- Railway should detect the `Dockerfile`.

### 2. Persistent volume (required)

Without a volume, the SQLite database is wiped on every deploy.

1. On the **project canvas**, right-click empty space → **New Volume**, *or* press **⌘K / Ctrl+K** → “New Volume”
2. Attach the volume to your **web** service
3. Click the volume card → set **Mount path** to **`/data`**
4. On the web service → **Variables**, set:

```
DATA_DIR=/data
```

Optional variables:

```
TZ=Africa/Lagos                    # Nigerian time (default)
CRON_MATCH_HOURS_WAT=0,5           # matches + Strongest + AI Picks (default midnight & 5am WAT)
CRON_SCORE_HOURS_WAT=6,16,18,20,22 # Flashscore sync (default 6am, 4pm, 6pm, 8pm, 10pm WAT)
RUN_DAILY_ON_START=1               # run matches pipeline once on boot (default 1); set 0 to skip
FD_SEASON=2627                     # football-data.co.uk season for results backfill
API_FOOTBALL_KEY=...               # optional; leftovers only — unset if the account is suspended
OPENAI_API_KEY=...                 # optional; enables AI Picks (LLM screen of Strongest leans)
OPENAI_MODEL=gpt-5.6-luna          # optional; default gpt-5.6-luna
AI_VET_MIN_SCORE=55                # optional; estimated-hit-chance floor for AI Picks (%)
SCORE_LINK_SECRET=...              # optional; unlock Flashscore “!” near-miss score confirms
```

Remove obsolete `CRON_HOUR_UTC` if it is still set on the service.

**Schedule (WAT):**

| Job | Times |
|-----|--------|
| Matches / Strongest / AI Picks (`cron_matches.sh`) | 00:00, 05:00 |
| Flashscore scores (`cron_scores.sh`) | 06:00, 16:00, 18:00, 20:00, 22:00 |

**Final scores:** Flashscore scrapes run on the score schedule above (Playwright/Chromium in the image) via `sync-scores`, then `sync-match-stats` fills corners/shots/cards for leagues football-data.co.uk does not cover. football-data.co.uk remains the secondary path for goals and for stats on its eleven main leagues. You do **not** need API-Football for scores. If `API_FOOTBALL_KEY` is suspended, remove it from Variables to avoid useless errors.


**Market selection (optional):**

| Variable | Default | Purpose |
|----------|---------|---------|
| `AI_VET_TOP_N` | `3` | Gate-passing candidates per fixture sent to LLM |
| `AI_VET_MIN_SCORE` | `55` | Minimum estimated hit chance (%) to publish |
| `MARKET_CALIBRATION_SHRINKAGE` | `50` | Empirical-Bayes weight toward parent rates for thin buckets |
| `MARKET_CALIBRATION_DEFAULT_RATE` | `0.50` | Fallback base rate when no calibration rows exist |
| `MARKET_PROB_CALIBRATION_ENABLED` | `1` | Calibrate dashboard market `%` from graded history |
| `MARKET_PROB_CALIBRATION_MIN_FIT` | `80` | Min samples before logit Platt is used (else shrink to base rate) |
| `MARKET_PROB_CALIBRATION_MIN_WEEKS` | `4` | Min distinct matchweeks before logit Platt is used |
| `STRONGEST_MIN_PROB` | `0.65` | Hard probability floor for a Strongest lean |
| `STRONGEST_MIN_AUC` | `0.55` | Exclude a market from Strongest only when its AUC upper bound is below this |
| `STRONGEST_AUC_MIN_LABELS` | `300` | Min graded samples before the AUC gate may exclude a market |
| `STRONGEST_AUC_MIN_WEEKS` | `8` | Min distinct matchweeks before the AUC gate may exclude a market |
| `STRONGEST_POISSON_PROB_EPSILON` | `0.03` | Prefer higher prob over Poisson tie-break when gap ≥ this |
| `STRONGEST_USE_MARKET_HIT_RATES` | `0` | Use backtest hit rates as ranking tie-break |
| `STRONGEST_MARKET_HIT_MIN_GRADED` | `100` | Min samples per market before hit rate applies |
| `FLASHSCORE_STATS_ENABLED` | `1` | Fetch Flashscore `?t=stats` after score sync |
| `FLASHSCORE_STATS_MAX_MATCHES` | `40` | Cap on match stats pages per `sync-match-stats` run |
| `FLASHSCORE_STATS_DELAY_SEC` | `1.0` | Sleep between stats page fetches |

Run `python -m dg.cli selection-audit` to measure selection regret vs oracle hit rate. Run `python -m dg.cli market-audit` to compare stated market percentages with actual hit rates (AUC and cross-validated log-loss vs a constant).

**AI Picks:** after predictions on the match schedule, `vet-ai-picks` screens top-N gate-passing candidates per fixture via an OpenAI-compatible API. The LLM returns coherence/concerns; the published **Est.%** is a measured hit rate keyed by market × source agreement × probability band (with shrinkage), adjusted by that screen — **not** the model lean percentage. Run `dg calibration-audit` to check ranking vs actual hits. Without `OPENAI_API_KEY`, that step is skipped and `/ai-picks` explains setup. Fixture ingest warnings make `run_daily.py` exit `1` (partial); `cron_matches.sh` still runs AI vet and backfill unless the daily run exits `2` (critical).

**Score near-miss (!):** set `SCORE_LINK_SECRET`, then visit `/score-link/unlock?token=YOUR_SECRET` once (HttpOnly cookie, 12h). Awaiting fixtures with soft Flashscore name matches show **!** — confirm a candidate to write **Final**.

### 3. Start command

`railway.toml` / Dockerfile already use:

```
sh /app/deploy/start.sh
```

That script:

1. Optionally runs the matches pipeline once on boot (`RUN_DAILY_ON_START=1`)
2. Sleeps until the next WAT hour in `CRON_MATCH_HOURS_WAT` / `CRON_SCORE_HOURS_WAT`, then runs `cron_matches.sh` or `cron_scores.sh`
3. Starts `uvicorn` in the foreground on `$PORT`

Health check path: `/healthz`

### 4. Public domain

**Settings → Networking → Generate domain.**

### 5. First data load

With `RUN_DAILY_ON_START=1` (default), the first deploy runs the matches pipeline shortly after boot. Wait a few minutes, then refresh the site.

To run manually from **web → Console**:

```
python run_daily.py
python -m dg.cli vet-ai-picks
python -m dg.cli backfill-results --season 2627
python -m dg.cli sync-scores
python -m dg.cli sync-match-stats
```

After a deploy that touches Flashscore stats parsing, confirm heuristic markets grade:

```
python -m dg.cli sync-match-stats   # expect fetched/written > 0 (not all "stats empty")
python -m dg.cli backtest
python -m dg.cli market-audit       # expect corners/shots/SOT/cards; n_markets toward 10
```

Manual `sync-scores` is safe while the site is live (SQLite WAL). If you see `database is locked`, retry once — you do not need to stop uvicorn.

Or the wrappers:

```
sh deploy/cron_matches.sh   # predictions + AI + FD backfill
sh deploy/cron_scores.sh    # Flashscore only
sh deploy/cron_daily.sh     # both (full manual refresh)
```

## What users do

Open the site → read the dashboard → use **How to read this** if anything is unclear.

They do **not** need to run Python locally.

## Do not create a second cron service for the DB

Railway **cannot** mount the same volume on two services. A separate cron service would get an empty `/data` and never update the site’s database. Keep everything on the single web service + volume.

## Local preview (optional)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run_daily.py
.venv/bin/uvicorn dg.web.app:app --reload --port 8787
# http://127.0.0.1:8787
```
