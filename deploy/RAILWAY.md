# Deploy on Railway

This app is a **website** plus an **in-container daily refresh**. Railway only allows
**one volume per service**, so we do **not** use a second cron service sharing `/data`.

Instead:

1. **Web service** — dashboard (`uvicorn`) + background loop that runs `deploy/cron_daily.sh` daily
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
CRON_HOUR_UTC=8          # daily run hour (UTC); default 8
RUN_DAILY_ON_START=1     # run pipeline once on boot (default 1); set 0 to skip
FD_SEASON=2627           # football-data.co.uk season for daily results backfill
API_FOOTBALL_KEY=...     # optional; leftovers only — unset if the account is suspended
OPENAI_API_KEY=...       # optional; enables AI Picks (LLM screen of Strongest leans)
OPENAI_MODEL=gpt-5.6-luna  # optional; default gpt-5.6-luna
AI_VET_MIN_SCORE=70      # optional; approve floor for AI Picks
```

**Final scores:** the daily job scrapes **flashscore.mobi** (Playwright/Chromium in the image) via `sync-scores`, then falls back to football-data.co.uk. You do **not** need API-Football for scores. If `API_FOOTBALL_KEY` is suspended, remove it from Variables to avoid useless errors.

**AI Picks:** after predictions, `vet-ai-picks` screens Strongest leans via an OpenAI-compatible API. Without `OPENAI_API_KEY`, that step is skipped and `/ai-picks` explains setup.

### 3. Start command

`railway.toml` / Dockerfile already use:

```
sh /app/deploy/start.sh
```

That script:

1. Optionally runs the pipeline once on boot (`RUN_DAILY_ON_START=1`)
2. Sleeps until `CRON_HOUR_UTC`:00 UTC each day, then runs `deploy/cron_daily.sh`
3. Starts `uvicorn` in the foreground on `$PORT`

Health check path: `/healthz`

### 4. Public domain

**Settings → Networking → Generate domain.**

### 5. First data load

With `RUN_DAILY_ON_START=1` (default), the first deploy runs `python run_daily.py` shortly after boot. Wait a few minutes, then refresh the site.

To run manually from **web → Console**:

```
python run_daily.py
python -m dg.cli backfill-results --season 2627
python -m dg.cli sync-scores
```

Or the full daily wrapper (pipeline + FD results + Flashscore scores):

```
sh deploy/cron_daily.sh
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
