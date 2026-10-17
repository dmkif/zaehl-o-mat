# Implementation Plan: Low findings - data, upload and OCR area

**Branch**: `011-low-data-ocr` | **Spec**: [spec.md](spec.md)

Ownership: `backend/app/{routers/{readings,meters,ocr,admin}.py,schemas/**,models.py,jobs/retention.py,services/ocr_pipeline.py}`, new `app/error_handlers.py`, `app/services/image_hygiene.py`, `app/schemas/common.py`, migration `0009`, `ocr-service/**`, chart OCR template/values, Compose. `main.py` gets two lines (import + `install_error_handlers(app)`).

## Decisions
- Unique index `uq_readings_meter_image_hash (meter_id, image_hash) WHERE image_hash IS NOT NULL` (partial on PostgreSQL and SQLite). Migration 0009 first NULLs `image_hash` of all but the oldest row per `(meter_id, image_hash)` (no row or image deleted); downgrade drops the index only (NULLed hashes are not restored).
- `source` is server-derived (`manual`); the schema keeps the field for client compatibility.
- Image hygiene in `services/image_hygiene.py`: `sanitize_image(bytes) -> bytes` (decode check + metadata allow-list); JPEG APP1/APP13 and XMP segments, PNG ancillary text/exif chunks and WEBP EXIF/XMP chunks are rewritten without re-encoding pixels.
- Error handlers: `app/error_handlers.install(app)`, registered for `DBAPIError`, `TimeoutError`/`DisconnectionError` (SQLAlchemy) and `Exception`.
- OCR key: `hmac.compare_digest` on UTF-8 bytes; `/health` open.

## Constitution check
Tests first PASS; reversible migration PASS; Compose/chart parity (OCR key) PASS; server-side validation PASS.
