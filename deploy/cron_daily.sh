#!/usr/bin/env sh
# Railway daily pipeline: ingest/predict, AI Picks vet, FD results, Flashscore sync-scores.
set -eu
python run_daily.py
python -m dg.cli vet-ai-picks || true
python -m dg.cli backfill-results --season "${FD_SEASON:-2627}" || true
python -m dg.cli sync-scores || true
