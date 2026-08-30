FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    PORT=8080 \
    CRON_HOUR_UTC=8 \
    RUN_DAILY_ON_START=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY dg/ dg/
COPY config/ config/
COPY run_daily.py .
COPY deploy/cron_daily.sh deploy/cron_daily.sh
COPY deploy/start.sh deploy/start.sh
COPY pyproject.toml .

RUN chmod +x deploy/cron_daily.sh deploy/start.sh

# Ensure data mount point exists (Railway volume mounts over /data)
RUN mkdir -p /data

EXPOSE 8080

# Web + in-container daily scheduler (same volume). See deploy/RAILWAY.md.
CMD ["sh", "/app/deploy/start.sh"]
