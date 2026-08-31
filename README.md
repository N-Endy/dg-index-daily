# DG Index Daily

Open the website → read today’s fixture leans in plain English.  
Data refreshes automatically each day on Railway. You do not need to run scripts.

## Use the site (what you care about)

1. Open your Railway URL (or `http://127.0.0.1:8787` locally).
2. Skim the dashboard; filter by date or league if you want.
3. Read **How to read this** (`/guide`) for what lean, confidence, and match style mean.

Predictions are **directional / exploratory** — rule-based, not a self-learning tip service, and **not betting advice**.

## Deploy on Railway

Step-by-step: **[deploy/RAILWAY.md](deploy/RAILWAY.md)**

Short version:

| Piece | Setting |
|--------|---------|
| Build | `Dockerfile` |
| Volume | Mount at `/data` on the **web** service only |
| Env | `DATA_DIR=/data`; optional `OPENAI_API_KEY` (AI Picks); optional `API_FOOTBALL_KEY` (leftover scores) |
| Start | `sh /app/deploy/start.sh` (uvicorn + WAT scheduler in one container) |
| Schedule | **Matches/AI** 00:00 & 05:00 WAT; **Flashscore** 06:00, 16:00, 18:00, 20:00, 22:00 WAT — no second cron service |

Railway cannot share one volume across two services, so the refresh schedule runs **inside** the web container.

Finished-match scores come from a **flashscore.mobi** scrape (`sync-scores`) plus football-data.co.uk. API-Football is optional; if that account is suspended, unset `API_FOOTBALL_KEY`.

## Local development (optional)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run_daily.py
.venv/bin/uvicorn dg.web.app:app --reload --port 8787
```

Open http://127.0.0.1:8787

### Ops CLI (still available)

```bash
.venv/bin/python -m dg.cli doctor
.venv/bin/python run_daily.py
.venv/bin/python -m dg.cli backfill-results --season 2627
.venv/bin/python -m dg.cli sync-scores   # Flashscore.mobi (needs playwright chromium locally)
.venv/bin/python -m dg.cli vet-ai-picks  # LLM screen of Strongest → AI Picks (needs OPENAI_API_KEY)
.venv/bin/python -m dg.cli backtest
```

Exit codes: `0` success, `1` partial, `2` critical.

### macOS launchd (local only)

Production refresh is **Railway Cron**. For a Mac-only schedule, see [`deploy/com.datagaffer.daily.plist`](deploy/com.datagaffer.daily.plist).

## Verification

```bash
.venv/bin/pytest tests/ -v
.venv/bin/ruff check dg tests run_daily.py
PATH="$(pwd)/.venv/bin:$PATH" python3 .agent/scripts/checklist.py .
```

## Layout

| Path | Purpose |
|------|---------|
| `dg/web/` | FastAPI dashboard + guide |
| `data/` or `$DATA_DIR` | SQLite, reports, raw archives |
| `config/weights_rule_v1.yaml` | Rule model weights |
| `deploy/RAILWAY.md` | Hosting instructions |
