FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps for psycopg2 + EasyOCR (libgomp for OpenMP)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libgomp1 \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN adduser --disabled-password --gecos "" appuser && \
    mkdir -p /data/uploads && chown appuser /data/uploads
USER appuser

# Pre-download EasyOCR models at build time to avoid cold-start delay.
# Must run as appuser so the models land in a home directory the runtime
# user can actually read (~/.EasyOCR).
RUN python -c "import easyocr; easyocr.Reader(['de', 'en'], gpu=False)"

EXPOSE 8000
# --proxy-headers + --forwarded-allow-ips: trust X-Forwarded-For from the
# reverse proxy so rate limiting keys on the real client IP, not the proxy.
CMD alembic upgrade head && \
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'
