# Phase 1 Data Model: OCR as an Optional External Service

No database schema changes (Constitution Principle IV — N/A for this
feature; confirmed in `plan.md` Constitution Check). This document instead
describes the request/response shapes and configuration entities that cross
the new backend ⇄ OCR-service boundary, since that boundary is the entire
surface area of this feature.

## Entity: OCR Scan Request (backend → OCR service)

Sent by the backend's `_ocr_service_scan()` client function.

| Field | Type | Notes |
|---|---|---|
| `file` | binary (multipart part) | The meter photo, already validated (MIME, size ≤ 10 MB) by the backend before forwarding — the OCR service does not need to re-validate upload constraints, only handle decode failures gracefully. |
| `already_cropped` | bool (form field) | Same meaning as today's `already_cropped` param: skip auto display-strip detection when the user pre-cropped the image. |

## Entity: OCR Scan Result (OCR service → backend)

Matches the existing in-process return shape of `_run_ocr_on_file_sync`
today — **unchanged**, so no caller-side (frontend, `/api/ocr/*`) contract
changes are required.

| Field | Type | Notes |
|---|---|---|
| `raw_texts` | array of `{text: string, conf: number}` | All OCR candidate detections, for debugging/display. |
| `detected_value` | string \| null | Best-scoring numeric reading candidate. |
| `detected_serial` | string \| null | Always `null` from `/scan` — serial detection is a separate call (see below), matching today's behavior where `_run_ocr_on_file_sync` never sets it. |
| `detection_method` | `"ocr"` (literal) | Kept for response-shape parity with the LLM path's `"llm"` value, so the backend's existing merge logic in `app/routers/ocr.py` needs no branching changes. |

## Entity: OCR Serial Request / Result (backend → OCR service)

Sent by `_ocr_service_serial()`; mirrors today's `_extract_serial_sync`.

| Field | Type | Notes |
|---|---|---|
| `file` | binary (multipart part) | The user-cropped serial-label image. |
| → `detected_serial` | string \| null (response) | Best alphanumeric candidate, or `null`. |

## Entity: OCR Service Health (OCR service → backend)

Polled by backend's `/api/health` handler.

| Field | Type | Notes |
|---|---|---|
| `status` | `"ok"` | Simple readiness signal — the OCR service has no dependent subsystems of its own (no DB), so this is a liveness-equivalent check: process up, EasyOCR reader loadable. |

## Configuration Entity: Backend OCR-Service Settings

New fields on `backend/app/config.py`'s `Settings`, following the exact
shape of the existing `ollama_url` / `ollama_api_key` pair.

| Field | Type | Default | Notes |
|---|---|---|---|
| `ocr_url` | `str` | `""` | Base URL of the OCR service, e.g. `http://ocr:8100`. Empty disables OCR entirely (Principle II). |
| `ocr_api_key` | `Optional[str]` | `None` | Bearer token for the OCR service, only needed if the operator fronts it with an authenticating proxy. |

## Configuration Entity: Helm `ocr` values block

New top-level key in `chart/values.yaml`, analogous in shape to the existing
`ollama:` block but chart-deployed (see `research.md` Decision 4).

| Field | Type | Default | Notes |
|---|---|---|---|
| `ocr.enabled` | bool | `false` | Off by default — OCR stays fully optional (FR-008). |
| `ocr.image.repository` | string | `ghcr.io/dmkif/zaehl-o-mat-ocr` | Same registry convention as `backend.image.repository` / `frontend.image.repository`. |
| `ocr.image.tag` | string | `""` | Empty → chart `appVersion`, same convention as backend/frontend. |
| `ocr.resources` | object | requests/limits sized for EasyOCR's CPU/memory footprint | Determined during implementation from the relocated container's observed footprint. |

## State / Lifecycle Notes

- The OCR service is **stateless** across requests — no entity here has a
  lifecycle beyond a single request/response. Existing `Reading`,
  `Meter`, `Property` entities and their lifecycles are entirely
  unaffected by this feature (confirmed in spec.md Assumptions: "No data
  migration is required").
- `detection_method` values already in use by the frontend/response
  consumers (`"ocr"`, `"llm"`, `null`) are unchanged — this feature does not
  introduce a new value, so no frontend changes are required to interpret
  responses.
