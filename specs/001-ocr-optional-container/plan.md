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
  dependency at implementation time) — target: backend image at least 30%
  smaller than today (SC-001, clarified 2026-09-11).
- Outbound backend → OCR-service HTTP calls MUST use a hard 30-second
  timeout (clarified 2026-09-11; tighter than the existing `_llm_fallback`
  pattern's `timeout=180.0` for Ollama, since OCR itself normally completes
  in 1-3s) so the request-handling path stays bounded (FR-011).
- When a caller explicitly requests `engine=ocr` and the OCR service is
  unreachable, the backend MUST return a clear error for that request — no
  silent fallback to the LLM path (clarified 2026-09-11; FR-005, US2/AC2).
  The `auto` engine's existing LLM-first/OCR-fallback behavior is
  unaffected.
- The existing `/api/ocr/*` HTTP contract (request/response shape presented
  to the frontend) MUST NOT change — only what's *behind* `engine="ocr"`
  changes, from an in-process call to a network call.

**Scale/Scope**: Unchanged from today — self-hosted, single-tenant-per-
deployment scale; existing rate limits on `/ocr/scan` etc. are untouched.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*
*Checked against Constitution v1.1.1 (10 principles).*

| Principle | Check | Result |
|---|---|---|
| I. Test-Backed Changes | Requires OCR-engine tests to move with the code (not be dropped) and the new backend↔OCR-service client to gain its own tests before merge. | **PASS** (planned in Phase 1 — see `data-model.md` / task breakdown in `/speckit-tasks`) |
| II. Graceful Degradation for Optional Subsystems | New `ocr_url` setting follows the exact `ollama_url` pattern: empty = disabled, health check reports `ocr` independently, absence never flips overall `status`. | **PASS** |
| III. Secure-by-Default Secrets & Access | No new secret is *required*; an optional bearer-token setting is added for parity with `OLLAMA_API_KEY`/`ollama.existingSecret`, for operators who front the OCR service with an authenticating proxy. No RBAC changes — OCR is called on the backend's behalf, same trust boundary as the Ollama call today. | **PASS** |
| IV. Schema Changes via Migrations | No database schema touched. | **PASS (N/A)** |
| V. Deployment Parity (Compose ⇄ Helm) | New service + its config var(s) MUST land in `docker-compose.yaml`, `chart/` (new optional Deployment/Service + `values.yaml` block), and the README env-var table in the same change. | **PASS** (explicitly scoped into Phase 1 design below) |
| VI. Brownfield Respect | Relocation of existing, already-tested logic into a new service — not a rewrite. `/api/ocr/*`'s contract to the frontend is unchanged (FR-010, data-model.md). Backend router/service split convention followed for the new service too. | **PASS** |
| VII. Security by OWASP Top 10 (NON-NEGOTIABLE) | See OWASP mapping table below — every relevant category has a concrete control. | **PASS** |
| VIII. Authenticated Communication by Default | No new *inbound* backend (FastAPI) endpoint is added — `/api/ocr/*` is unchanged, so the exhaustive inbound exception list is untouched. The backend→OCR-service call is an *outbound* call to a self-hosted, operator-controlled optional subsystem (Constitution v1.2.0's explicit outbound rule): a bearer-token setting (`ocr_api_key`) MUST be available and SHOULD be recommended to operators (research.md Decision 3, updated 2026-09-11 after `/speckit-analyze` flagged the original framing as resting on an unstated reinterpretation of the pre-v1.2.0 text). Off by default only for zero-config local use, matching the amended principle's explicit allowance. | **PASS** |
| IX. Input Validation Is Server-Side Authoritative | Backend already validates uploads (magic-bytes MIME, 10 MB cap) before forwarding to the OCR service (data-model.md "OCR Scan Request" — file already validated by caller). OCR service itself validates decodability and returns 400/422 on bad input (contracts/ocr-service-api.md). | **PASS** |
| X. Verifiable Security (CI Gates) | No new endpoint touching auth/access-control is added, so no new negative-test obligation beyond what Principle I already requires for the new HTTP client code. Existing CI gates (bandit, eslint-plugin-security, gitleaks, Trivy) apply automatically to the new `ocr-service/` code — no exemption needed or requested. | **PASS** |

### OWASP Top 10 Mapping (Principle VII)

| Category | Applicable? | Control |
|---|---|---|
| A01 Broken Access Control | Yes | The `ocr_url`/`OCR_URL` setting is operator-configured (env var/Helm value), never user-supplied per-request — no SSRF surface from end-user input. Backend-to-OCR-service call carries no cross-tenant data; OCR service is stateless and has no concept of properties/meters/RBAC, so there is nothing to authorize *within* it. |
| A02 Security Misconfiguration | Yes | `ocr_url` empty by default (secure default = disabled, Principle II). No default credentials introduced. |
| A03 Software Supply Chain | Yes | New `ocr-service/requirements.txt` pins exact versions (existing backend convention); Trivy scan (CI) covers it like every other dependency file. |
| A04 Cryptographic Failures | No new surface | No new crypto/secret material introduced beyond the existing, optional `OLLAMA_API_KEY`-style bearer token pattern (research.md Decision 3). |
| A05 Injection | Yes | OCR service receives raw image bytes only, never interpolates request data into a query, shell command, or template. |
| A06 Insecure Design | Yes | Documented explicitly: FR-011 bounds the call (30s), FR-005 defines the unreachable-service behavior, US2's edge cases cover misconfigured endpoint and rolling-upgrade ordering. |
| A07 Authentication Failures | Yes | New outbound-only bearer-token setting (`ocr_api_key`), not a new inbound auth mechanism — no custom crypto, mirrors existing `OLLAMA_API_KEY` (see Principle VIII check above). |
| A08 Software/Data Integrity | Yes | OCR-service JSON responses are parsed defensively by the backend client the same way `_llm_fallback` already parses Ollama responses (FR-010: contract preserved, not blindly trusted). |
| A09 Logging & Alerting | Yes | OCR-service call failures are logged (mirrors existing `logger.warning` pattern in `ocr_pipeline.py`); no image bytes or secrets logged. |
| A10 Exceptional Conditions | Yes | FR-005/FR-011: unreachable/timeout is a defined, handled state (health check + clear per-request error), not an unhandled exception path. |

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
