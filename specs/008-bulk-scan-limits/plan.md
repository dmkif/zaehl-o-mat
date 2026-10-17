# Implementation Plan: Bulk-Scan Resource Limits and Bounded OCR/LLM Output

**Branch**: `008-bulk-scan-limits` | **Spec**: [spec.md](spec.md)

## Summary
One work package, test-first. Files: `backend/app/routers/ocr.py`, `backend/app/services/{ocr_pipeline,llm_client}.py`, new `backend/app/body_limit.py` (+ registration in `main.py`), `backend/app/config.py` (settings appended at the end), `docker-compose.yaml`, `chart/` (values, Middleware, IngressRoute), tests `backend/tests/test_bulk_scan_limits.py`, `test_ocr_output_bounds.py`.

## Constitution Check
I tests first PASS; V Compose/Helm parity (MAX_BULK_*, body limit) PASS; VII OWASP A10/A08 PASS; IX server-side validation PASS; II optional OCR keeps degrading to a clean 502/503 PASS.

## Decisions
- F-012: chunked `_read_limited()` (64 kB chunks) with a running total; per-file overflow stops at limit+1 and the bytes are dropped (item error as before); budget overflow -> 413. `BodyLimitMiddleware` (pure ASGI, only `POST /api/ocr/bulk-scan`) checks `Content-Length` and counts streamed bytes against `max_bulk_total_bytes + 1 MiB` multipart slack (one limit, no new setting). Traefik `buffering` middleware, default 60 MB, on the `/api` route except `/api/ocr/bulk-scan` (buffering also buffers the response and would break the SSE stream; the backend middleware covers that path).
- F-013: all DB work (meters, dicts) up front, then `db.expunge_all(); db.close()`; the generator opens `Session(bind=db.get_bind())` per item for steps that need the DB and closes it before every await. No new pool setting.
- F-029: `sanitize_reading/serial/raw_texts/ocr_result` in `ocr_pipeline.py`; `_ocr_service_scan/_serial` read at most 64 kB, parse in try/except (ValueError, RecursionError) -> `OcrServiceError`, mapped to 502 in the router. LLM text capped at 8 kB in `llm_client.complete`. Equality guard extracted and applied in both paths.
- F-028: `_discard_upload()` removes file + `upload_files` row; `scan` wraps OCR in try/except BaseException-free `except Exception` + re-raise; bulk item failure discards.
- Not done: per-user quota / global concurrency semaphore (rest of B-24), OCR-service auth (B-25/F-030), nginx bulk limit.
