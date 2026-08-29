#!/usr/bin/env sh
# Optional wrapper for Railway cron: daily ingest + weekly backfill on Sundays.
set -eu
python run_daily.py
# Sunday = 0 on some systems; use date +%u (1=Mon … 7=Sun)
if [ "$(date -u +%u)" = "7" ]; then
  python -m dg.cli backfill-results --season "${FD_SEASON:-2627}" || true
fi
