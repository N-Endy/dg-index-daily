#!/usr/bin/env sh
# Matchday refresh: ingest/predict (Strongest), AI Picks vet, football-data results.
set -eu

# run_daily.py returns 1 (partial) for fixture ingest warnings; with set -e that
# would skip vet-ai-picks and backfill-results. Only abort on critical (exit 2).
daily_rc=0
python run_daily.py || daily_rc=$?
if [ "$daily_rc" -eq 2 ]; then
  echo "[cron_matches] run_daily.py failed critically (exit $daily_rc); skipping AI vet and backfill" >&2
  exit 2
fi
if [ "$daily_rc" -eq 1 ]; then
  echo "[cron_matches] run_daily.py completed with warnings (exit $daily_rc); continuing AI vet and backfill" >&2
fi

python -m dg.cli vet-ai-picks || true
python -m dg.cli backfill-results --season "${FD_SEASON:-2627}" || true
