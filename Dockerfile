FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    PORT=8080

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY dg/ dg/
COPY config/ config/
COPY run_daily.py .
COPY deploy/cron_daily.sh deploy/cron_daily.sh
COPY pyproject.toml .

RUN chmod +x deploy/cron_daily.sh

# Ensure data mount point exists (Railway volume mounts over /data)
RUN mkdir -p /data

EXPOSE 8080

# Default: web service. Cron service overrides the command.
CMD ["sh", "-c", "uvicorn dg.web.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
