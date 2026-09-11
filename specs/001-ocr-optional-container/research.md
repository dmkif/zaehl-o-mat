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

## Decision 3: Auth model — bearer token available and recommended, off by default for zero-config use

**Decision**: Backend → OCR-service calls support an optional bearer-token
setting (`ocr_api_key` / Helm `ocr.existingSecret`), mirroring
`OLLAMA_API_KEY`/`ollama.existingSecret`. It stays unset (unauthenticated)
by default for zero-config Compose/local use where both containers share a
private Docker network, but operators are explicitly encouraged in
documentation to set it, particularly in Kubernetes deployments where the
OCR service and backend may not share full network isolation. Like
`OLLAMA_API_KEY` today, the token is meaningful only when the operator
fronts the OCR service with an authenticating reverse proxy — the OCR
service itself implements no authentication (see `/speckit-analyze`
finding N1, 2026-09-11: an earlier draft of `contracts/ocr-service-api.md`
had the service checking the header itself, with no task ever wiring the
secret into its container; corrected to match this decision).

**Rationale**: Constitution Principle VIII (as amended 2026-09-11, MINOR
v1.2.0) explicitly requires that outbound calls to self-hosted/operator-
controlled optional subsystems — this is one, same as Ollama — SHOULD
offer and recommend an auth mechanism, even though the default may stay
unauthenticated for zero-config local use. This decision was updated from
its original "unauthenticated by default, mentioned only as an option"
framing after `/speckit-analyze` flagged that framing as resting on an
unstated reinterpretation of Principle VIII rather than an explicit rule.
The mechanism itself (`ocr_api_key`) was already designed this way; what
changed is the documentation obligation (README, Assumptions) to actually
recommend using it.

**Alternatives considered**: mTLS between backend and OCR service — rejected
as disproportionate for a same-trust-boundary internal call; no other
service in the stack does this today (not even the DB connection, which
relies on network isolation). Making the bearer token mandatory (no
unauthenticated default) — rejected: would break the zero-config Compose
quickstart's "clone and `docker compose up`" promise for a same-host,
private-network call; Principle VIII's amended text explicitly allows
"MAY remain unauthenticated... for zero-config local/trusted-network use."

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
