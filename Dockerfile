FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    PORT=8080 \
    TZ=Africa/Lagos \
    CRON_MATCH_HOURS_WAT=0,5 \
    CRON_SCORE_HOURS_WAT=6,16,18,20,22 \
    RUN_DAILY_ON_START=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY dg/ dg/
COPY config/ config/
COPY run_daily.py .
COPY deploy/cron_matches.sh deploy/cron_matches.sh
COPY deploy/cron_scores.sh deploy/cron_scores.sh
COPY deploy/cron_daily.sh deploy/cron_daily.sh
COPY deploy/start.sh deploy/start.sh
COPY pyproject.toml .

RUN chmod +x deploy/cron_matches.sh deploy/cron_scores.sh deploy/cron_daily.sh deploy/start.sh

# Ensure data mount point exists (Railway volume mounts over /data)
RUN mkdir -p /data

EXPOSE 8080

# Web + in-container WAT scheduler (same volume). See deploy/RAILWAY.md.
CMD ["sh", "/app/deploy/start.sh"]
