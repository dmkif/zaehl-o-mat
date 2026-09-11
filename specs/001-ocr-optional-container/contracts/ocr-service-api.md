# Contract: OCR Service HTTP API

Internal service-to-service API. Consumed only by the backend
(`backend/app/services/ocr_pipeline.py`); not exposed to end users or the
frontend directly. No authentication required by default (see
`research.md` Decision 3); an optional `Authorization: Bearer <token>`
header is accepted when the operator configures `OCR_API_KEY`.

Base URL: operator-configured, e.g. `http://ocr:8100` (Compose) or
`http://<release>-ocr:8100` (Helm, in-cluster Service DNS).

---

## `POST /scan`

Extract a meter reading from a (typically pre-cropped) display photo.

**Request**: `multipart/form-data`

| Part | Type | Required | Description |
|---|---|---|---|
| `file` | file | yes | The meter display image. |
| `already_cropped` | form field, `"true"`/`"false"` | no (default `false`) | Skip auto display-strip detection. |

**Response**: `200 OK`, `application/json`

```json
{
  "raw_texts": [{"text": "0309735", "conf": 0.93}],
  "detected_value": "0309735",
  "detected_serial": null,
  "detection_method": "ocr"
}
```

**Error responses**:

| Status | When |
|---|---|
| `400` | Unreadable/corrupt image. |
| `422` | Missing `file` part. |
| `500` | Unexpected OCR pipeline failure (logged server-side; message MUST NOT leak internal paths). |

Timeout expectation for callers: the backend MUST apply a 30-second
client-side timeout (FR-011, clarified 2026-09-11) — this endpoint does not
artificially delay responses, but callers must not wait indefinitely on a
slow/stuck container.

---

## `POST /serial`

Extract a serial number from a user-cropped label image.

**Request**: `multipart/form-data`

| Part | Type | Required | Description |
|---|---|---|---|
| `file` | file | yes | The cropped serial-label image. |

**Response**: `200 OK`, `application/json`

```json
{"detected_serial": "0012345678"}
```

Error responses: same as `/scan`.

---

## `GET /health`

Liveness/readiness for the backend's health-check ping (Decision 5).

**Response**: `200 OK`

```json
{"status": "ok"}
```

No degraded/unhealthy states are modeled — the service has no dependent
subsystems (no DB); a `200` means the process is up and the OCR reader is
usable, anything else (connection refused, timeout, non-200) means "OCR
unavailable" from the backend's point of view, exactly as an unreachable
Ollama endpoint is treated today.

---

## Versioning / Compatibility

This contract intentionally reproduces the existing in-process return
shapes of `_run_ocr_on_file_sync` / `_extract_serial_sync` (see
`data-model.md`) so that `backend/app/routers/ocr.py` and everything
downstream of it (frontend, tests) require no changes beyond swapping the
call site from an in-process function call to an HTTP client call.
