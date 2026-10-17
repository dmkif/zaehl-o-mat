# Tasks: Bulk-Scan Resource Limits and Bounded OCR/LLM Output

Tests first for every task (red -> green).

## US1 - memory bounds (F-012)
- [x] T001 [P] Tests (red): `backend/tests/test_bulk_scan_limits.py` - total budget 413 (nothing stored, no OCR), file count setting 400, per-file oversize stays an item error, Content-Length / streamed body over route limit 413 before parsing
- [x] T002 Settings `max_bulk_files`, `max_bulk_total_bytes` (end of `Settings`), Compose + chart env; `_read_limited` and budget in `bulk_scan_meters`; `app/body_limit.py` registered in `main.py`
- [x] T003 Chart: Middleware `buffering.maxRequestBodyBytes` + route split (bulk-scan SSE excluded), value `ingress.maxRequestBodyBytes`; `helm lint` / `helm template` + kubeconform

## US2 - DB session (F-013)
- [x] T004 [P] Tests (red): request session not in a transaction and no short session open during `_llm_fallback` / `_run_ocr_on_file` in the generator
- [x] T005 Restructure the generator: upfront DB work, `expunge_all` + `close`, short sessions per item

## US3 - bounded output (F-029)
- [x] T006 [P] Tests (red): `backend/tests/test_ocr_output_bounds.py` - reading digits, serial charset/length, raw_texts caps, OCR-path reading==serial, non-JSON / nested / oversized / wrong-type OCR response -> 502 (scan and rescan paths), LLM text cap, EXIF date pattern
- [x] T007 Sanitizers + `OcrServiceError` in `ocr_pipeline.py`, text cap in `llm_client.py`, 502 mapping in `routers/ocr.py`

## US4 - orphans (F-028)
- [x] T008 [P] Tests (red): scan with LLM/OCR failure leaves no file or registry row (also serial crop); bulk item failure likewise, `temp_image_path` null
- [x] T009 `_discard_upload` in `routers/ocr.py` for scan and bulk

## Integration
- [ ] T010 Full backend suite, `ocr-service` tests untouched, pre-commit; update `security/REPORT.md` statuses after the rig run (owner)
