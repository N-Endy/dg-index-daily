# Deploy on Railway

This app is meant to be used as a **website**. Railway runs:

1. A **web** service — the dashboard users open
2. A **cron** service — refreshes DataGaffer data once per day into a shared volume

## One-time setup

### 1. Create the project

- New Project → Deploy from this GitHub repo (or Railway CLI).
- Railway should detect the `Dockerfile`.

### 2. Persistent volume (required)

Without a volume, the SQLite database is wiped on every deploy.

1. Open the **web** service → **Variables** / **Volumes**
2. Add a volume mounted at **`/data`**
3. Set environment variable:

```
DATA_DIR=/data
```

### 3. Web service

Start command (also in `railway.toml`):

```
uvicorn dg.web.app:app --host 0.0.0.0 --port $PORT
```

Generate a public domain under **Settings → Networking**.

Health check path: `/healthz`

### 4. Cron service (same image, daily refresh)

1. Add a **second service** from the same repo / same Dockerfile.
2. Mount the **same volume** at `/data`.
3. Set `DATA_DIR=/data`.
4. Set the start / cron command to:

```
python run_daily.py
```

   Or use the helper that also backfills results on Sundays:

```
sh deploy/cron_daily.sh
```

5. Schedule: **daily at 08:00 UTC** (DataGaffer ratings usually regenerate around 05:13 UTC).

Optional weekly results backfill is included in `deploy/cron_daily.sh` on Sundays. To run backfill alone:

```
python -m dg.cli backfill-results --season 2627
```

### 5. First data load

Until cron has run once, the homepage will say there is no data.

Run once from Railway shell / one-off:

```
python run_daily.py
```

Then open your public URL.

## What users do

Open the site → read the dashboard → use **How to read this** if anything is unclear.

They do **not** need to run Python locally.

## Local preview (optional)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run_daily.py
.venv/bin/uvicorn dg.web.app:app --reload --port 8787
# http://127.0.0.1:8787
```
