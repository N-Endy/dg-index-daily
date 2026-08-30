#!/usr/bin/env sh
# Railway daily pipeline: ingest/predict, then pull football-data results for scores.
set -eu
python run_daily.py
python -m dg.cli backfill-results --season "${FD_SEASON:-2627}" || true
