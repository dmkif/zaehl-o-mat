# Feature Specification: Bulk-Scan Resource Limits and Bounded OCR/LLM Output

**Feature Branch**: `008-bulk-scan-limits`

**Created**: 2026-10-17

**Status**: Draft

**Input**: Fix the resource-exhaustion findings of the security review (feature 004) that concern the OCR endpoints: F-012, F-013, F-028 (orphan files), F-029. Source: `security/REPORT.md`, `security/review/backend.md` (B-08, B-09, B-24, B-25, B-28).

## Background

Feature 006 added upload ownership and pixel limits. What remains on the OCR path: `bulk-scan` reads every uploaded file completely before any size check (F-012), keeps one pooled DB connection for the whole streamed LLM run (F-013), failed scans leave their stored image behind (F-028), and values from the gateway / OCR service are passed on without type or size bounds, a malformed OCR response gives 500 (F-029). Per-user quotas, a global concurrency cap and the OCR-service authentication (rest of B-24/B-25, F-030) stay out of scope.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A bulk upload cannot exhaust memory (F-012, P1)

**Independent Test**: With small limits configured, a bulk request over the budget is answered 413 without OCR and without stored files; a request with too many files is 400; a body whose declared or streamed size exceeds the route limit is refused 413 before the multipart parser runs; Traefik refuses bodies over the configured size.

**Acceptance Scenarios**:
1. **Given** `MAX_BULK_TOTAL_BYTES` = N, **When** the files together exceed N, **Then** 413, reading stops at the budget, nothing is stored, no OCR call.
2. **Given** a file over the per-file limit (10 MB) inside an otherwise valid batch, **Then** that item is an error item and the other items are processed (unchanged); its bytes are not kept in memory.
3. **Given** more than `MAX_BULK_FILES` files, **Then** 400.
4. **Given** a `Content-Length` above the bulk route limit (or a chunked body exceeding it), **Then** 413 before the form is parsed.
5. **Given** the Kubernetes path, **Then** the Traefik `/api` route carries a buffering middleware with `maxRequestBodyBytes` (default 60 MB); the bulk-scan SSE route is excluded (buffering would break the stream) and bounded by the backend limit of scenario 4.

### User Story 2 - A slow bulk run does not hold a DB connection (F-013, P1)

During every LLM/OCR await of the SSE generator no DB session or transaction of the request is open; each item opens a short session for the duplicate check, upload registration and last-reading lookup.

**Acceptance Scenarios**:
1. While `_llm_fallback` / `_run_ocr_on_file` runs, the request session is closed (not in a transaction) and no short session is open.
2. Results (items, matching, final meter list) are identical to before.

### User Story 3 - Gateway and OCR-service output is bounded (F-029, P2)

**Acceptance Scenarios**:
1. A reading has at most 12 digits, otherwise it is dropped; a serial matches `[A-Za-z0-9./]{1,64}`, otherwise it is dropped; applied to LLM and OCR-service results alike.
2. `reading == serial` (>= 6 digits) drops the reading on the OCR-service path too.
3. `raw_texts` has at most 50 entries, each `{text <= 200 chars, conf 0..1}`; other shapes are dropped; `detection_method` is `ocr` or `llm`.
4. A non-JSON, oversized, wrongly typed or deeply nested OCR-service response gives 502 (`engine=ocr` / auto fallback), never 500; in bulk-scan it is an error item.
5. LLM answer text is capped (8 kB) before parsing; EXIF date is `YYYY-MM-DD[THH:MM]` or null.

### User Story 4 - A failed scan leaves no file behind (F-028, P2)

**Acceptance Scenarios**:
1. `scan` failing with LLM/OCR error (503/502 or unexpected exception) deletes the stored image, the serial crop and their registry rows.
2. A failing `bulk-scan` item (LLM error or exception) deletes its file and registry row and returns `temp_image_path: null`.

### Edge Cases
- Existing behavior stays: gateway outage stops the remaining items; duplicate detection; per-item size / MIME / pixel errors as error items.
- Nginx (Compose) keeps its 15 MB body cap; the app limits apply behind any proxy.

## Requirements *(mandatory)*

- **FR-001** (F-012): bulk-scan reads uploads in chunks against a cumulative budget (`MAX_BULK_TOTAL_BYTES`, default 50 MiB) and per-file limit, count limit `MAX_BULK_FILES` (default 50); a route-level ASGI limit refuses oversized bodies before parsing.
- **FR-002** (F-012): Helm chart adds a Traefik `buffering` Middleware (`maxRequestBodyBytes`, value `ingress.maxRequestBodyBytes`, default 62914560) on the `/api` route except bulk-scan (SSE).
- **FR-003** (F-013): the SSE generator never holds the request session across awaits; short sessions per item.
- **FR-004** (F-029): shared sanitizers for reading / serial / raw_texts / method; OCR-service responses validated; malformed -> 502.
- **FR-005** (F-028): files and registry rows of failed scans are removed.
- **FR-006**: every fix has a regression test that fails before and passes after (principle I); Compose and chart carry the new variables (principle V).

## Success Criteria
- **SC-001**: full backend suite green; helm template + kubeconform clean.
- **SC-002**: security rig experiments E-UP-001 (bulk), E-A08-001, E-A08-001 orphan / hostile tests pass (run by the owner later).

## Assumptions
- Settings are appended at the end of `Settings`. Reading digits are capped at 12 and serial at 64 characters per the review recommendation.
