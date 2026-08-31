# DG Index Daily Pipeline

**Project Type:** BACKEND + web UI (batch pipeline + FastAPI dashboard)

## Overview

Autonomous Python pipeline that:

1. Fetches DataGaffer public JSON (`dg_meta.json`, `dg_ratings.json`, fixture feeds)
2. Snapshots to SQLite with gzip raw archives
3. Joins football-data.co.uk results for labelled outcomes
4. Runs explainable `rule_v1` matchup predictions
5. Serves a plain-language dashboard at `/` and guide at `/guide`
6. On Railway: web service + WAT match/score scheduler + volume at `/data`

## Success criteria

- Idempotent ingest keyed on `generated_at`
- Users open a URL — no scripts required day-to-day
- Guide explains lean / confidence / metrics for non-experts
- Offline pytest suite passes

## Tech stack

Python 3.9+ locally / 3.11 on Railway, FastAPI, Jinja2, requests, pandas, SQLite, pytest, ruff

## Key paths

- Package: `dg/` (web: `dg/web/`)
- Entry: `run_daily.py` / `uvicorn dg.web.app:app`
- Deploy: `Dockerfile`, `railway.toml`, `deploy/RAILWAY.md`
- Weights: `config/weights_rule_v1.yaml`

## Verification

```bash
.venv/bin/pytest tests/ -v
.venv/bin/ruff check dg tests run_daily.py
PATH="$(pwd)/.venv/bin:$PATH" python3 .agent/scripts/checklist.py .
```
