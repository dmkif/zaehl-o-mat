FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN adduser --disabled-password --gecos "" appuser && \
    mkdir -p /data/uploads && chown appuser /data/uploads
USER appuser

EXPOSE 8000
# --proxy-headers + --forwarded-allow-ips: trust X-Forwarded-For from the
# reverse proxy so rate limiting keys on the real client IP, not the proxy.
CMD alembic upgrade head && \
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'
