#!/usr/bin/env sh
# Flashscore timely scores (settles Final / Awaiting on the board).
set -eu
python -m dg.cli sync-scores || true
