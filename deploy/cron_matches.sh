#!/usr/bin/env sh
# Matchday refresh: ingest/predict (Strongest), AI Picks vet, football-data results.
set -eu
python run_daily.py
python -m dg.cli vet-ai-picks || true
python -m dg.cli backfill-results --season "${FD_SEASON:-2627}" || true
