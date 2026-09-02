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
AI_VET_MIN_SCORE=70                # optional; approve floor for AI Picks
SCORE_LINK_SECRET=...              # optional; unlock Flashscore “!” near-miss score confirms
```

Remove obsolete `CRON_HOUR_UTC` if it is still set on the service.

**Schedule (WAT):**

| Job | Times |
|-----|--------|
| Matches / Strongest / AI Picks (`cron_matches.sh`) | 00:00, 05:00 |
| Flashscore scores (`cron_scores.sh`) | 06:00, 16:00, 18:00, 20:00, 22:00 |

**Final scores:** Flashscore scrapes run on the score schedule above (Playwright/Chromium in the image) via `sync-scores`, with football-data.co.uk as a secondary path. You do **not** need API-Football for scores. If `API_FOOTBALL_KEY` is suspended, remove it from Variables to avoid useless errors.


**Market selection (optional):**

| Variable | Default | Purpose |
|----------|---------|---------|
| `AI_VET_TOP_N` | `3` | Gate-passing candidates per fixture sent to LLM |
| `STRONGEST_POISSON_PROB_EPSILON` | `0.03` | Prefer higher prob over Poisson tie-break when gap ≥ this |
| `STRONGEST_USE_MARKET_HIT_RATES` | `0` | Use backtest hit rates as ranking tie-break |
| `STRONGEST_MARKET_HIT_MIN_GRADED` | `100` | Min samples per market before hit rate applies |

Run `python -m dg.cli selection-audit` to measure selection regret vs oracle hit rate.

**AI Picks:** after predictions on the match schedule, `vet-ai-picks` screens Strongest leans via an OpenAI-compatible API. Without `OPENAI_API_KEY`, that step is skipped and `/ai-picks` explains setup. Fixture ingest warnings make `run_daily.py` exit `1` (partial); `cron_matches.sh` still runs AI vet and backfill unless the daily run exits `2` (critical).

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
