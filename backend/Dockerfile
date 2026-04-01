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

# Pre-download EasyOCR models at build time to avoid cold-start delay
RUN python -c "import easyocr; easyocr.Reader(['de', 'en'], gpu=False)" || true

RUN adduser --disabled-password --gecos "" appuser && \
    mkdir -p /data/uploads && chown appuser /data/uploads
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
