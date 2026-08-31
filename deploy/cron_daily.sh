#!/usr/bin/env sh
# Full manual refresh: matches pipeline then Flashscore sync.
set -eu
sh "$(dirname "$0")/cron_matches.sh"
sh "$(dirname "$0")/cron_scores.sh"
