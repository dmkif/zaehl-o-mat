# Feature Specification: Low findings - data, upload and OCR area

**Feature Branch**: `011-low-data-ocr`
**Status**: Draft
**Input**: fix the open low findings of `security/REPORT.md` in the data/upload/OCR area: F-021, F-022, F-023, F-024, F-030, F-032, F-033, F-035, F-037 (F-036 documented, not changed).

## User Stories

### US1 - Readings stay intact (F-021, F-022, F-037, P1)
- `source` of an API-created reading is **always `manual`**. A client-supplied `auto`/`archived` is accepted for compatibility (the shipped frontend still sends `auto` for scanned readings) but ignored; an unknown value is still 422. The retention job additionally never touches a row that carries evidence (`image_path`, `serial_image_path`, `note`), which protects readings already stored with `source=auto`.
- Two concurrent POSTs with the same image on the same meter produce one 201 and one 409 `duplicate_image` (partial unique index `(meter_id, image_hash) WHERE image_hash IS NOT NULL`, `IntegrityError` -> 409). The cross-meter check stays the existing, access-scoped pre-check. The index is per meter, not global, so a foreign tenant's reading can never influence (or be revealed by) a request.
- `replaced_by_id` must reference a different meter of the **same property** (422 otherwise). `integration_config` stays plaintext in the column and is never returned (documented, accepted).
- **Identical readings (design decision)**: no uniqueness rule for readings without image. Two readings with equal value and time are legitimate (re-read, correction, two household members) and the dashboard handles them; the image hash is the only de-duplication key.

### US2 - Bad input is a 4xx, never a 500 (F-023, F-024, P1)
- Strings: max length equal to the column (`name` 255, `serial_number` 100, ...), free text capped; NUL bytes and lone surrogates rejected (422).
- Numbers: `Reading.value` finite, quantised to 3 decimals, at most 12 digits (`|v| < 10^9`); NaN/Infinity rejected.
- Explicit `null` for NOT NULL fields (`name`, `integration_type`) rejected. `integration_config` is an object or null (stored as JSON text, max 8 KiB), never an arbitrary value.
- `offset` <= 2^31-1; NUL in `hint-rescan` form fields -> 422.
- F-024: deleting a user that has property links works (`passive_deletes`, the DB cascade removes the rows). The finding was real: the ORM tried to null `property_users.user_id`.

### US3 - Stored images are sound and private (F-032, F-033, P2)
- Scan, serial crop and bulk scan decode the image completely (`PIL load`) before anything is stored; undecodable/truncated -> 400 (bulk: item error).
- Stored images carry only an allow-list of metadata: orientation and date tags (`DateTime`, `DateTimeOriginal`, `DateTimeDigitized`). GPS, maker notes, XMP and text chunks are removed (JPEG/PNG/WEBP by lossless segment/chunk rewrite, TIFF re-encoded; GIF/BMP carry no location data). The image hash is computed over the **stored** bytes (scan, bulk scan and `create_reading` agree). Side effect: the hash of a re-uploaded photo no longer matches readings stored by older versions.

### US4 - Failure modes are clean (F-035, P2)
- Database connection errors map to `503 {"detail": "Service temporarily unavailable"}` + `Retry-After`; `DataError` -> 422, unexpected `IntegrityError` -> 409, any other unhandled exception -> generic 500 JSON. No internals in the body.
- Write endpoints build their response **before** `commit` and do not `refresh` afterwards: a successful commit is always acknowledged.

### US5 - OCR service is authenticated (F-030, P2)
- `ocr-service` requires `Authorization: Bearer <OCR_API_KEY>` on `/scan` and `/serial` when `OCR_API_KEY` is set (constant-time, 401 otherwise); `/health` stays open for probes. Without a key it runs unauthenticated and logs a startup warning.
- Compose requires `OCR_API_KEY` (shared by `ocr` and `backend`); the chart requires `ocr.existingSecret` (key `OCR_API_KEY`) unless `ocr.allowUnauthenticated=true`, and passes it to both pods.

### Out of scope
F-036 (unattached scan upload readable by every authenticated user): accepted, the upload registry (F-004) limits it to the uploader/property; no change. F-034, F-031, F-025/26 etc. belong to other features.

## Success criteria
New tests for every item fail before and pass after; full backend suite and `ocr-service` suite green; migration 0009 up/down verified on PostgreSQL 18.
