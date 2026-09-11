# Implementation Plan: OCR as an Optional External Service

**Branch**: `001-ocr-optional-container` | **Date**: 2026-09-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-ocr-optional-container/spec.md`

## Summary

Move EasyOCR (and its heavy transitive dependencies — opencv, numpy, torch)
out of the backend image entirely into a new standalone `ocr-service`
container that the backend calls over HTTP, the same way it already calls
the optional Ollama vision LLM. OCR becomes a fully separable, optional
component in both Docker Compose and the Helm chart, off by default; the
LLM path remains the primary, recommended extraction method. No database
schema changes are involved — this is a service-boundary relocation of
existing, already-tested logic.

## Technical Context

**Language/Version**: Python 3.12 (matches the existing backend image)

**Primary Dependencies**:
- `ocr-service` (new): FastAPI, uvicorn, easyocr==1.7.2 (+ its opencv/numpy/
  torch transitive deps), Pillow, `filetype`, python-multipart — i.e. exactly
  what the backend carries for OCR today, relocated verbatim.
- `backend` (modified): drops `easyocr` from `requirements.txt`; gains no
  new runtime dependency — it already uses `httpx` for the equivalent Ollama
  HTTP calls, and reuses that same client for the new OCR-service calls.

**Storage**: N/A — the OCR service is stateless (processes an uploaded image
in memory/temp file, returns a result, keeps no state). No database of its
own; no change to the existing PostgreSQL schema.

**Testing**: pytest, mirroring the existing backend suite's structure.
OCR-engine-specific tests (`backend/tests/test_ocr_scoring.py` and the
EasyOCR-path parts of `test_ocr_endpoint.py`) move to `ocr-service/tests/`
alongside the code they test; the backend keeps/gains tests for the new
HTTP client functions (mocked OCR-service responses) and for `engine`
selection logic in `app/routers/ocr.py`.

**Target Platform**: Linux containers — Docker Compose for self-hosted
quickstart, Kubernetes via the existing Helm chart.

**Project Type**: Web service, multi-container (backend + frontend + proxy
today; this feature adds a fourth, optional container).

**Performance Goals**: No new performance targets. The relocated OCR call
must stay bounded — see Constraints — so a slow/stuck OCR container cannot
stall backend request handling (FR-011).

**Constraints**:
- Backend image build MUST drop the EasyOCR model pre-download step and its
  `apt` deps (`libgl1`, `libglib2.0-0` are OCR-only; `libpq5`/`libgomp1`
  stay as they're needed for psycopg2/other native deps — verify per
  dependency at implementation time) — net effect: smaller, faster backend
  builds (SC-001).
- Outbound backend → OCR-service HTTP calls MUST use an explicit timeout
  (mirrors the existing `_llm_fallback` pattern, which already sets
  `timeout=180.0` for Ollama calls) so the request-handling path stays
  bounded (FR-011).
- The existing `/api/ocr/*` HTTP contract (request/response shape presented
  to the frontend) MUST NOT change — only what's *behind* `engine="ocr"`
  changes, from an in-process call to a network call.

**Scale/Scope**: Unchanged from today — self-hosted, single-tenant-per-
deployment scale; existing rate limits on `/ocr/scan` etc. are untouched.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Result |
|---|---|---|
| I. Test-Backed Changes | Requires OCR-engine tests to move with the code (not be dropped) and the new backend↔OCR-service client to gain its own tests before merge. | **PASS** (planned in Phase 1 — see `data-model.md` / task breakdown in `/speckit-tasks`) |
| II. Graceful Degradation for Optional Subsystems | New `ocr_url` setting follows the exact `ollama_url` pattern: empty = disabled, health check reports `ocr` independently, absence never flips overall `status`. | **PASS** |
| III. Secure-by-Default Secrets & Access | No new secret is *required*; an optional bearer-token setting is added for parity with `OLLAMA_API_KEY`/`ollama.existingSecret`, for operators who front the OCR service with an authenticating proxy. No RBAC changes — OCR is called on the backend's behalf, same trust boundary as the Ollama call today. | **PASS** |
| IV. Schema Changes via Migrations | No database schema touched. | **PASS (N/A)** |
| V. Deployment Parity (Compose ⇄ Helm) | New service + its config var(s) MUST land in `docker-compose.yaml`, `chart/` (new optional Deployment/Service + `values.yaml` block), and the README env-var table in the same change. | **PASS** (explicitly scoped into Phase 1 design below) |

No violations — **Complexity Tracking is not needed.**

## Project Structure

### Documentation (this feature)

```text
specs/001-ocr-optional-container/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
│   └── ocr-service-api.md
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
ocr-service/                       # NEW — standalone optional container
├── app/
│   ├── main.py                    # FastAPI app: POST /scan, POST /serial, GET /health
│   ├── pipeline.py                # Moved verbatim from backend/app/services/ocr_pipeline.py:
│   │                               #   _preprocess_image, _fix_seven_segment, _extract_numeric,
│   │                               #   _extract_serial_sync, _run_ocr_on_file_sync, _get_reader,
│   │                               #   _reader_lock — the EasyOCR-only functions.
│   └── config.py                  # Minimal settings (bind host/port, log level)
├── tests/
│   ├── test_scoring.py            # Moved from backend/tests/test_ocr_scoring.py
│   └── test_endpoints.py          # New — HTTP-layer tests for /scan, /serial, /health
├── requirements.txt                # easyocr, opencv deps (transitive), pillow, fastapi,
│                                   #   uvicorn, python-multipart, filetype
└── Dockerfile                      # Moved/adapted from backend/Dockerfile: apt libgl1/
                                    #   libglib2.0-0, pip install, EasyOCR model pre-download

backend/
├── app/
│   ├── config.py                  # + ocr_url: str = "", ocr_api_key: Optional[str] = None
│   ├── main.py                    # health(): ocr_ok now pings {ocr_url}/health (like llm_ok
│   │                               #   already pings {ollama_url}/api/tags)
│   ├── routers/ocr.py              # engine="ocr"/"auto" fallback now calls the new OCR-service
│   │                               #   client instead of _run_ocr_on_file_sync directly
│   └── services/ocr_pipeline.py    # LLM-only functions stay; EasyOCR-only functions removed;
│                                   #   + new _ocr_service_scan()/_ocr_service_serial() HTTP
│                                   #   client functions (httpx, mirrors _llm_fallback's shape)
├── tests/
│   ├── test_ocr_endpoint.py        # EasyOCR-path assertions replaced with mocked
│   │                               #   OCR-service-client assertions
│   └── (test_ocr_scoring.py removed — moved to ocr-service/tests/test_scoring.py)
├── requirements.txt                 # - easyocr
└── Dockerfile                       # - libgl1/libglib2.0-0 apt packages, - EasyOCR
                                     #   model pre-download RUN step

docker-compose.yaml                  # + `ocr` service (build: ./ocr-service, optional/
                                     #   commented profile), backend env `OCR_URL`
README.md                            # env-var table: + OCR_URL, OCR_API_KEY; note on the
                                     #   new optional container

chart/
├── templates/
│   ├── deployment-ocr.yaml          # NEW, guarded by {{ if .Values.ocr.enabled }}
│   ├── services.yaml                # + ocr Service entry (guarded the same way)
│   └── networkpolicy.yaml           # + ocr NetworkPolicy: ingress only from backend pods
└── values.yaml                      # + `ocr:` block (enabled/image/resources), analogous
                                     #   to the existing `ollama:` block but chart-deployed
                                     #   rather than bring-your-own (see research.md)
```

**Structure Decision**: Web-service project, now four containers instead of
three (backend, frontend, proxy, **+ocr**). The OCR service is a new,
independently built/deployed/versioned unit — not a library imported by the
backend — matching FR-002 and Constitution Principle II's established
optional-subsystem contract. Docker Compose and the Helm chart are updated
in the same change per Principle V; see `research.md` for why the chart
*deploys* this service (unlike the bring-your-own `ollama:` block).

## Complexity Tracking

*No Constitution Check violations — this section is intentionally empty.*
