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
# --no-proxy-headers: uvicorn must not rewrite the client address from
# X-Forwarded-For (with '*' that would take the client-controlled leftmost
# entry). The rate limiter (app/limiter.py) evaluates the header itself, only
# for peers in TRUSTED_PROXIES. `exec` makes uvicorn PID 1 so SIGTERM reaches it.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers"]
