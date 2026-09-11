# Phase 0 Research: OCR as an Optional External Service

No `[NEEDS CLARIFICATION]` markers were left in the Technical Context — the
project already has an established pattern for exactly this kind of optional,
network-called subsystem (the Ollama LLM integration), so the decisions below
apply that pattern rather than inventing a new one. Recorded here for
traceability.

## Decision 1: Transport — HTTP + multipart, not base64/JSON

**Decision**: The OCR service exposes plain HTTP endpoints accepting
`multipart/form-data` file uploads (like the backend's own `/api/ocr/scan`
does today for the frontend), not base64-encoded JSON payloads.

**Rationale**: The backend already accepts raw multipart uploads from the
frontend and already has the image as bytes on disk by the time OCR is
invoked — passing it through as a file avoids the ~33% size inflation of
base64 for what can be multi-megabyte photos, and keeps the two services'
contract simple and inspectable (`curl -F file=@photo.jpg` works directly).

**Alternatives considered**:
- *Base64 inside JSON, like the Ollama `/api/generate` call*: rejected —
  that shape is dictated by Ollama's own API, not something we should copy
  for our own service-to-service contract where we control both ends.
  Extra encoding overhead has no offsetting benefit here.
- *gRPC*: rejected — introduces a new dependency/tooling class (protobuf,
  codegen) for a single low-QPS internal call; the project has no other gRPC
  usage, so this would add complexity Principle overhead without a matching
  need (rate-limited to 20/min at the HTTP layer today).
- *Shared filesystem handoff (backend writes file, OCR service polls a
  directory)*: rejected — turns a request/response operation into an
  asynchronous, stateful, harder-to-time-out workflow; conflicts with
  FR-011 (bounded call).

## Decision 2: Framework — FastAPI for the new service

**Decision**: `ocr-service` is a small FastAPI app, same as the backend.

**Rationale**: Zero new framework to learn/maintain; the existing
`_run_ocr_on_file_sync` / `_extract_serial_sync` functions in
`ocr_pipeline.py` already return plain dicts that map directly onto FastAPI
response models. Team familiarity and consistent tooling (pytest +
`TestClient`) across both services.

**Alternatives considered**: Flask, plain WSGI/ASGI script — rejected, no
advantage over FastAPI here and would fragment the codebase's conventions
for no gain.

## Decision 3: Auth model — unauthenticated by default, optional bearer token

**Decision**: Backend → OCR-service calls are unauthenticated by default
(trusted internal network, same as the current unauthenticated Ollama call),
with an optional bearer-token setting (`ocr_api_key` / Helm
`ocr.existingSecret`) for operators who expose the OCR service through
something that needs it.

**Rationale**: Exact mirror of the existing `OLLAMA_API_KEY` /
`ollama.existingSecret` pattern already in `backend/app/config.py` and
`chart/values.yaml` — Constitution Principle III requires secrets to flow
through env vars/`/run/secrets`, not that every internal call be
authenticated; the established precedent for this exact kind of call
(backend → optional local AI/ML service) is unauthenticated-by-default.

**Alternatives considered**: mTLS between backend and OCR service — rejected
as disproportionate for a same-trust-boundary internal call; no other
service in the stack does this today (not even the DB connection, which
relies on network isolation).

## Decision 4: Chart deployment model — chart deploys the OCR service itself

**Decision**: Unlike the `ollama:` block in `chart/values.yaml` (which is
*bring-your-own*: the chart only takes a URL, it never deploys Ollama
itself), the new `ocr:` block causes the chart to deploy its own
Deployment + Service for the OCR container when `ocr.enabled=true`.

**Rationale**: The feature request is explicitly to "carry it along as an
optional container" (`als optionalen Container mitführen`) — this project
owns and publishes the OCR service image (`ghcr.io/dmkif/zaehl-o-mat-ocr` by
the same convention as the backend/frontend images), the same way it owns
and deploys `frontend`/`backend`/bundled `postgresql`. Ollama, by contrast,
is a large, generic, third-party model server operators are expected to
already run or size independently — a genuinely different case.

**Alternatives considered**: Bring-your-own OCR endpoint (chart only takes a
URL, like `ollama:`) — rejected as not matching the explicit ask, and less
useful: unlike Ollama (which needs GPU sizing decisions best left to the
operator), the OCR service is lightweight enough that "the chart just runs
it for you" is the more useful default.

## Decision 5: Health check integration

**Decision**: `backend`'s `/api/health` reports `ocr` by pinging
`{ocr_url}/health` with a short timeout (mirrors the existing `llm_ok` check
against `{ollama_url}/api/tags`, `timeout=3.0`), rather than reusing the old
`_is_easyocr_available()` import check (which no longer applies — EasyOCR is
not importable in the backend process anymore).

**Rationale**: Direct continuation of Constitution Principle II's existing,
working pattern — no new design needed, just applying the same shape to a
second optional subsystem.

**Alternatives considered**: None seriously — this is a direct port of
already-established, already-correct backend behavior.

## Decision 6: Explicit-engine failure mode — hard error, no silent fallback

**Decision**: When a caller explicitly requests `engine=ocr` and the OCR
service is unreachable, the backend returns a clear per-request error
rather than silently substituting the LLM path. The `auto` engine's
existing LLM-first/OCR-fallback behavior is unchanged — this decision only
affects an *explicit* `engine=ocr` request. Resolved via `/speckit-clarify`
on 2026-09-11 (see spec.md Clarifications).

**Rationale**: `engine` is a per-request choice made by the client/UI, not
a fixed deployment setting — a caller who explicitly asks for OCR has a
reason to (e.g. comparing engines, or the LLM already failed for this
image). Silently substituting a different engine would hide that failure
and could return a materially different reading without the caller
knowing which engine produced it.

**Alternatives considered**: Auto-fallback to LLM on OCR failure even for
explicit `engine=ocr` requests — rejected: would make `engine=ocr` a
non-deterministic request (sometimes OCR, sometimes silently LLM), and
masks OCR-service outages behind an apparently-successful response instead
of surfacing them via the error path (and `/api/health`).
