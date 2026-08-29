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
| Volume | Mount at `/data` |
| Env | `DATA_DIR=/data` |
| Web start | `uvicorn dg.web.app:app --host 0.0.0.0 --port $PORT` |
| Cron (daily 08:00 UTC) | `python run_daily.py` (same image + same volume) |

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
