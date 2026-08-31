#!/usr/bin/env sh
# Single-service Railway entrypoint:
# - background loop runs match + Flashscore jobs on WAT (Africa/Lagos) hour lists
# - uvicorn serves the dashboard in the foreground (same volume / DATA_DIR)
set -eu

PORT="${PORT:-8080}"
export TZ="${TZ:-Africa/Lagos}"
CRON_MATCH_HOURS_WAT="${CRON_MATCH_HOURS_WAT:-0,5}"
CRON_SCORE_HOURS_WAT="${CRON_SCORE_HOURS_WAT:-6,16,18,20,22}"
RUN_DAILY_ON_START="${RUN_DAILY_ON_START:-1}"

# Seconds from now until next HH:00:00 in $TZ (force decimal; avoid octal).
seconds_until_hour() {
  hour="$1"
  h=$(date +%H | sed 's/^0//')
  m=$(date +%M | sed 's/^0//')
  s=$(date +%S | sed 's/^0//')
  h=${h:-0}
  m=${m:-0}
  s=${s:-0}
  now_sec=$((h * 3600 + m * 60 + s))
  target=$((hour * 3600))
  if [ "$now_sec" -ge "$target" ]; then
    echo $((86400 - now_sec + target))
  else
    echo $((target - now_sec))
  fi
}

# Echo: "<wait_sec> <kind> <hour>" for the soonest scheduled job.
# kind is "matches" or "scores".
next_scheduled_job() {
  min_wait=""
  next_kind=""
  next_hour=""

  hours_csv="$CRON_MATCH_HOURS_WAT"
  kind="matches"
  old_ifs=$IFS
  IFS=,
  # shellcheck disable=SC2086
  set -- $hours_csv
  IFS=$old_ifs
  for hour in "$@"; do
    hour=$(echo "$hour" | tr -d ' ')
    [ -n "$hour" ] || continue
    wait_sec=$(seconds_until_hour "$hour")
    if [ -z "$min_wait" ] || [ "$wait_sec" -lt "$min_wait" ]; then
      min_wait=$wait_sec
      next_kind=$kind
      next_hour=$hour
    fi
  done

  hours_csv="$CRON_SCORE_HOURS_WAT"
  kind="scores"
  IFS=,
  # shellcheck disable=SC2086
  set -- $hours_csv
  IFS=$old_ifs
  for hour in "$@"; do
    hour=$(echo "$hour" | tr -d ' ')
    [ -n "$hour" ] || continue
    wait_sec=$(seconds_until_hour "$hour")
    if [ -z "$min_wait" ] || [ "$wait_sec" -lt "$min_wait" ]; then
      min_wait=$wait_sec
      next_kind=$kind
      next_hour=$hour
    fi
  done

  if [ -z "$min_wait" ]; then
    echo "3600 matches 0"
    return 0
  fi
  echo "$min_wait $next_kind $next_hour"
}

run_job() {
  kind="$1"
  case "$kind" in
    matches)
      sh /app/deploy/cron_matches.sh
      ;;
    scores)
      sh /app/deploy/cron_scores.sh
      ;;
    *)
      echo "[start] unknown job kind: $kind" >&2
      return 1
      ;;
  esac
}

schedule_loop() {
  if [ "$RUN_DAILY_ON_START" = "1" ]; then
    echo "[start] RUN_DAILY_ON_START=1 — running matches pipeline once on boot"
    sh /app/deploy/cron_matches.sh || echo "[start] boot matches pipeline failed (will retry on schedule)"
  fi

  while true; do
    set -- $(next_scheduled_job)
    wait_sec=$1
    kind=$2
    hour=$3
    echo "[start] next ${kind} run in ${wait_sec}s (target ${hour}:00 ${TZ})"
    sleep "$wait_sec"
    echo "[start] running scheduled ${kind} job (${hour}:00 ${TZ})"
    run_job "$kind" || echo "[start] scheduled ${kind} job failed"
    # Avoid a tight loop if the job finishes in under a second at exactly HH:00
    sleep 60
  done
}

schedule_loop &
exec uvicorn dg.web.app:app --host 0.0.0.0 --port "$PORT"
