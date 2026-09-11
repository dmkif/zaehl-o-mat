# Feature Specification: OCR as an Optional External Service

**Feature Branch**: `001-ocr-optional-container`

**Created**: 2026-09-11

**Status**: Draft

**Input**: User description: "ich möchte das ocr-build aus dem Backend entfernen und als optionalen Container mitführen. Außerdem soll ocr nur als optional gelten, mit llm habe ich bessere ergebnisse erziehlt. Dann soll auch noch die Codebase reviewt werden und ein helm-chart erstellt werden"

## Clarifications

### Session 2026-09-11

- Q: When a caller explicitly requests OCR-only extraction (engine=ocr) but the OCR service is unreachable, what should the backend do? → A: Hard error — no silent substitution of a different engine. (The `engine` choice is made per-request by the client/UI, not fixed via environment variable — unchanged existing behavior.)
- Q: What's the maximum time the backend should wait for a single OCR-service call before giving up? → A: 30 seconds.
- Q: What minimum backend container image size reduction should count as this feature succeeding (SC-001)? → A: At least 30% smaller.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Deploy backend without OCR bundled (Priority: P1)

An operator self-hosting Zähl-O-Mat builds and deploys the backend to serve meter
readings, using the Ollama vision model as the reading-extraction method. The
operator does not need or want the classical OCR engine (EasyOCR) present in the
backend at all — it should add no build time, image size, or dependency weight
to the backend when unused.

**Why this priority**: The backend must work standalone with LLM-based extraction
only. This is the baseline everything else builds on, and it is the direct ask:
stop bundling OCR into the backend build.

**Independent Test**: Build and run the backend with no OCR service configured.
Confirm the backend starts, all core functionality (properties, meters, readings,
RBAC, dashboard, LLM-based extraction) works, and no OCR engine dependency is
present in the backend's build artifacts.

**Acceptance Scenarios**:

1. **Given** a freshly built backend image with no OCR service configured, **When**
   the operator starts the stack, **Then** the backend starts successfully and
   `/api/health` reports OCR as unavailable without affecting overall status.
2. **Given** the backend is running with only the LLM path configured, **When** a
   user uploads a meter photo, **Then** the reading is extracted via the LLM and
   returned normally.

---

### User Story 2 - Enable OCR as a separate optional service (Priority: P2)

An operator who wants the classical OCR fallback (in addition to, or instead of,
the LLM) deploys a separate, optional OCR service alongside the backend and
points the backend at it. The backend calls out to this service for OCR-based
extraction instead of running OCR in-process.

**Why this priority**: This is the mechanism that replaces the removed in-process
OCR path. It's needed for operators who still want OCR available, but it's
secondary to the P1 baseline (LLM-only operation) working first.

**Independent Test**: Deploy the optional OCR service alongside a backend that has
no in-process OCR engine, configure the backend to use it, upload a meter photo
requesting OCR extraction, and confirm a reading is returned with the same
accuracy/format as before the change.

**Acceptance Scenarios**:

1. **Given** the OCR service is deployed and configured on the backend, **When**
   a user requests OCR-based extraction (or the LLM path falls through to OCR),
   **Then** the backend calls the OCR service over the network and returns the
   extracted reading and serial number.
2. **Given** the OCR service is deployed but temporarily unreachable, **When** the
   backend attempts an OCR call, **Then** the request fails with a clear error
   after at most 30 seconds (no silent substitution of a different engine), the
   backend does not crash or hang, and `/api/health` reflects OCR as unavailable.
3. **Given** the OCR service is not deployed at all, **When** the backend starts,
   **Then** the backend operates normally with OCR simply reported as
   unavailable — deploying the OCR service is entirely optional.

---

### User Story 3 - Deploy the optional OCR service via Helm (Priority: P3)

An operator running Zähl-O-Mat on Kubernetes uses the Helm chart to deploy the
stack and wants the same LLM-primary / OCR-optional topology available there:
OCR off by default, toggleable on via chart values, consistent with how the
chart already handles other optional components.

**Why this priority**: Kubernetes operators need deployment parity with the
Docker Compose path (existing project convention). It depends on the OCR service
existing as a standalone deployable unit (US2) first.

**Independent Test**: Run a Helm install with the OCR component disabled (default)
and confirm the stack deploys and runs without it; run a Helm install with the
OCR component enabled and confirm the OCR service is provisioned and reachable
by the backend.

**Acceptance Scenarios**:

1. **Given** the Helm chart's default values, **When** an operator installs the
   chart, **Then** the stack deploys without the OCR service and the backend
   operates on the LLM path only.
2. **Given** an operator sets the OCR-enable value, **When** they install or
   upgrade the release, **Then** the OCR service is deployed and the backend is
   configured to reach it.

---

### Edge Cases

- What happens when the OCR service is enabled but its endpoint is misconfigured
  (wrong URL) — backend must fail the OCR call cleanly (within the 30-second
  timeout) and continue serving all other functionality, same as "OCR
  unreachable."
- What happens when a caller explicitly picks OCR (`engine=ocr`) and the OCR
  service is unreachable — the backend returns a clear error for that request;
  it does not silently substitute the LLM path. The user must explicitly
  choose a different engine (e.g. `auto` or `llm`) to retry.
- What happens to previously stored readings and their extraction history when
  the in-process OCR path is removed — the change affects only how future
  extraction requests are served, not previously stored reading data.
- How does the system behave if both the LLM and the OCR service are unavailable
  at request time — the user must get a clear, actionable error rather than a
  crash or a silent failure, and manual reading entry must remain available.
- What happens during a rolling upgrade where the backend image (without OCR) is
  deployed before the optional OCR service — the backend must already tolerate
  "OCR not configured" as a normal state (this is the P1 baseline), so no
  special migration handling is needed.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The backend build MUST NOT include the OCR engine or its
  dependencies (e.g. EasyOCR and its model/runtime weight).
- **FR-002**: OCR-based reading extraction MUST be provided by a separate,
  independently buildable and deployable service, decoupled from the backend's
  build and runtime.
- **FR-003**: The backend MUST start and serve all core functionality
  (properties, meters, readings, RBAC, dashboard) with no OCR service deployed
  or configured.
- **FR-004**: When an OCR service endpoint is configured, the backend MUST call
  it over the network to obtain OCR-based extraction results, preserving the
  existing extraction contract (reading value + serial number).
- **FR-005**: When no OCR service is configured, or the configured OCR service
  is unreachable, the backend MUST report OCR as unavailable via the existing
  health-check mechanism without changing overall system health status. When a
  caller explicitly requests OCR-only extraction (`engine=ocr`) and the OCR
  service is unreachable, the backend MUST return a clear error for that
  request rather than silently substituting a different engine.
- **FR-006**: The LLM (Ollama vision) extraction path MUST remain the primary,
  recommended reading-extraction method; OCR MUST be presented and treated as a
  secondary/optional fallback only.
- **FR-007**: The Docker Compose stack MUST define the OCR service as an
  optional, separately startable component (operators can run the stack with or
  without it).
- **FR-008**: The Helm chart MUST support deploying the OCR service as an
  optional component, toggleable via chart values, off by default.
- **FR-009**: Configuration for reaching the OCR service (e.g. its endpoint URL)
  MUST be exposed as backend configuration, documented alongside existing
  environment variables.
- **FR-010**: Reading-extraction behavior available through OCR today (accepted
  image inputs, output format, accuracy characteristics) MUST be preserved after
  it is moved into the separate service — this is a relocation, not a
  behavioral rewrite.
- **FR-011**: An outbound OCR call MUST NOT be allowed to block or hang backend
  request handling indefinitely; it MUST be bounded to a maximum of 30 seconds
  so a stuck OCR service cannot degrade the rest of the application.

### Key Entities

- **OCR Service**: A standalone, optional component that accepts a meter image
  and returns an extracted reading value and serial number. Independently
  built, deployed, versioned, and lifecycle-managed apart from the backend.
- **Reading Extraction Pipeline**: The backend's orchestration logic that
  attempts extraction via the LLM (primary) and, where configured, the OCR
  service (optional fallback), producing a single extraction result for a
  submitted meter photo.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The backend's built container image no longer carries OCR-engine
  weight — an operator building the backend without the OCR service present
  gets an image that is at least 30% smaller than before this change.
- **SC-002**: An operator can run the full application stack (Docker Compose or
  Helm), with OCR disabled, and every non-OCR feature (uploads, readings, RBAC,
  dashboard, LLM extraction) works with zero configuration of any OCR
  component.
- **SC-003**: An operator who wants OCR can enable it by deploying one
  additional, pre-defined optional service and setting its endpoint — no
  backend code changes are required.
- **SC-004**: Reading extraction accuracy and output for users who rely on OCR
  is unchanged before and after this change (no functional regression from the
  relocation).
- **SC-005**: A Helm install with the OCR component disabled succeeds with zero
  errors related to the missing OCR service; a Helm install with it enabled
  provisions a reachable OCR service that the backend can call.
- **SC-006**: When the OCR service is unreachable or not deployed, users
  uploading a meter photo still get a usable outcome (LLM extraction or a clear
  manual-entry path) rather than an application error.

## Assumptions

- Extracting OCR out-of-process means it becomes an HTTP-reachable service; the
  backend talks to it over the network the same way it already talks to the
  optional Ollama LLM service (unauthenticated within the trusted deployment
  network by default, configurable via an endpoint URL), consistent with
  Constitution Principle II (Graceful Degradation for Optional Subsystems).
- The existing Docker Compose file and Helm chart already model optional
  components (e.g. the Ollama container); the new OCR service follows the same
  established pattern rather than introducing a new deployment paradigm.
- "Codebase review" mentioned in the request is a quality/audit activity, not a
  buildable product requirement, and is therefore out of scope for this
  specification. It is better served by a dedicated code-review pass run
  separately (see Next Actions when this spec is delivered).
- No data migration is required: this change affects how future reading
  extraction requests are served, not the schema or content of previously
  stored readings.
- The Helm chart referenced already exists in the repository (`chart/`) and is
  being extended with a new optional component, not created from scratch.
