#!/usr/bin/env sh
# Railway daily pipeline: ingest/predict, FD results, then API-Football scores.
set -eu
python run_daily.py
python -m dg.cli backfill-results --season "${FD_SEASON:-2627}" || true
python -m dg.cli sync-scores || true
