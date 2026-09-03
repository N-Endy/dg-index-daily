#!/usr/bin/env sh
# Flashscore timely scores (settles Final / Awaiting on the board), then match stats.
set -eu
python -m dg.cli sync-scores || true
# Isolated from score sync: a stats block must never disable goal sync.
python -m dg.cli sync-match-stats || true
