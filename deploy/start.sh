#!/usr/bin/env sh
# Single-service Railway entrypoint:
# - background loop runs the daily pipeline at CRON_HOUR_UTC (default 08:00 UTC)
# - uvicorn serves the dashboard in the foreground (same volume / DATA_DIR)
set -eu

PORT="${PORT:-8080}"
CRON_HOUR_UTC="${CRON_HOUR_UTC:-8}"
RUN_DAILY_ON_START="${RUN_DAILY_ON_START:-1}"

# Seconds from now until next HH:00:00 UTC (HH = CRON_HOUR_UTC).
seconds_until_cron() {
  hour="$1"
  # Force decimal (avoid octal from leading zeros)
  h=$(date -u +%H | sed 's/^0//')
  m=$(date -u +%M | sed 's/^0//')
  s=$(date -u +%S | sed 's/^0//')
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

daily_loop() {
  if [ "$RUN_DAILY_ON_START" = "1" ]; then
    echo "[start] RUN_DAILY_ON_START=1 — running pipeline once on boot"
    sh /app/deploy/cron_daily.sh || echo "[start] boot pipeline failed (will retry on schedule)"
  fi

  while true; do
    wait_sec=$(seconds_until_cron "$CRON_HOUR_UTC")
    echo "[start] next daily run in ${wait_sec}s (target ${CRON_HOUR_UTC}:00 UTC)"
    sleep "$wait_sec"
    echo "[start] running scheduled daily pipeline"
    sh /app/deploy/cron_daily.sh || echo "[start] scheduled pipeline failed"
    # Avoid a tight loop if the job finishes in under a second at exactly HH:00
    sleep 60
  done
}

daily_loop &
exec uvicorn dg.web.app:app --host 0.0.0.0 --port "$PORT"
