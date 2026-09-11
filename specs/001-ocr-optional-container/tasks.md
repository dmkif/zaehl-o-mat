---

description: "Task list template for feature implementation"
---

# Tasks: OCR as an Optional External Service

**Input**: Design documents from `/specs/001-ocr-optional-container/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ocr-service-api.md, quickstart.md — all present and current against Constitution v1.1.1.

**Tests**: Included — Constitution Principle I (Test-Backed Changes, NON-NEGOTIABLE) requires pytest coverage for every backend behavior change; this applies to the new `ocr-service/` code too.

**Organization**: Tasks are grouped by user story (spec.md P1/P2/P3) to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Exact file paths are included in every description

---

## Phase 1: Setup

**Purpose**: Scaffold the new `ocr-service/` container per `plan.md`'s Project Structure.

- [ ] T001 Create `ocr-service/` skeleton: `ocr-service/app/__init__.py`, `ocr-service/tests/__init__.py`
- [ ] T002 [P] Write `ocr-service/requirements.txt` — pinned versions: `easyocr==1.7.2` (pulls in opencv/numpy/torch), `pillow`, `filetype`, `fastapi`, `uvicorn[standard]`, `python-multipart`, matching `backend/requirements.txt`'s exact-pin convention
- [ ] T003 [P] Write `ocr-service/Dockerfile` — `python:3.12-slim` base, `apt` deps `libgomp1 libgl1 libglib2.0-0`, `pip install -r requirements.txt`, EasyOCR model pre-download `RUN` step (adapted from `backend/Dockerfile`)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared backend configuration both US1 (health check) and US2 (HTTP client target) depend on.

**⚠️ CRITICAL**: Must complete before US1 implementation tasks.

- [ ] T004 Add `ocr_url: str = ""` and `ocr_api_key: Optional[str] = None` to `Settings` in `backend/app/config.py` (data-model.md "Configuration Entity: Backend OCR-Service Settings")

**Checkpoint**: Foundation ready — User Story 1 implementation can begin.

---

## Phase 3: User Story 1 - Deploy backend without OCR bundled (Priority: P1) 🎯 MVP

**Goal**: Backend builds/runs with zero OCR-engine weight; OCR reported unavailable via health check; LLM path unaffected.

**Independent Test**: Build and run the backend with no OCR service configured. Confirm it starts, all core functionality + LLM extraction works, `/api/health` reports `ocr: false` without degrading overall status, and `easyocr` is not importable in the backend image.

### Tests for User Story 1 ⚠️

> Write these tests FIRST, confirm they FAIL before the implementation tasks below.

- [ ] T005 [US1] Add test: `engine=ocr` with `settings.ocr_url` unset returns 503 "not configured" (mirrors the existing `engine=llm`-no-URL test) in `backend/tests/test_ocr_endpoint.py`
- [ ] T006 [P] [US1] Add test: `GET /api/health` reports `ocr: false` when `ocr_url` is unset, and overall `status` is unaffected, in new file `backend/tests/test_health.py`
- [ ] T007 [US1] Add regression test: `engine=llm` extraction still returns a reading correctly after EasyOCR removal (import-time regression guard) in `backend/tests/test_ocr_endpoint.py`

### Implementation for User Story 1

- [ ] T008 [US1] Remove EasyOCR-only functions from `backend/app/services/ocr_pipeline.py`: `_preprocess_image`, `_fix_seven_segment`, `_extract_numeric`, `_extract_serial_sync`, `_run_ocr_on_file_sync`, `_get_reader`, `_reader_lock`, `_is_easyocr_available`
- [ ] T009 [US1] Add `_ocr_service_scan(filepath, already_cropped)` and `_ocr_service_serial(filepath)` HTTP client functions to `backend/app/services/ocr_pipeline.py` using `httpx` with a 30-second timeout (FR-011), request/response shapes per `data-model.md` and `contracts/ocr-service-api.md` (depends on T008)
- [ ] T010 [US1] Update `backend/app/routers/ocr.py`: `engine="ocr"` now calls `_ocr_service_scan`/`_ocr_service_serial`; returns 503 immediately when `settings.ocr_url` is empty; on an `httpx` connect/timeout error returns a clear error without falling back to the LLM path (FR-005, no silent engine substitution) (depends on T009)
- [ ] T011 [US1] Update `health()` in `backend/app/main.py`: replace the `_is_easyocr_available()` import check with a `GET {ocr_url}/health` ping (3-second timeout), mirroring the existing `llm_ok` pattern (depends on T004, T009)
- [ ] T012 [US1] Remove `easyocr==1.7.2` from `backend/requirements.txt` (depends on T008)
- [ ] T013 [US1] Remove the EasyOCR-only apt packages (`libgl1`, `libglib2.0-0`) and the EasyOCR model pre-download `RUN` step from `backend/Dockerfile` (depends on T012)
- [ ] T014 [P] [US1] Delete `backend/tests/test_ocr_scoring.py` (logic and its tests relocate to `ocr-service/` in User Story 2)

**Checkpoint**: User Story 1 fully functional — backend image carries no OCR-engine weight, LLM-only operation works, OCR reported unavailable.

---

## Phase 4: User Story 2 - Enable OCR as a separate optional service (Priority: P2)

**Goal**: The OCR service exists as a standalone container; the backend calls it over the network; unreachable-service failures are handled cleanly.

**Independent Test**: Deploy the OCR service alongside the OCR-less backend from US1, configure `OCR_URL`, upload a meter photo requesting OCR extraction, confirm a reading is returned matching the pre-change contract. Separately, confirm an unreachable OCR service produces a clear per-request error within 30 seconds, never a silent LLM substitution.

### Tests for User Story 2 ⚠️

- [ ] T015 [P] [US2] Move `backend/tests/test_ocr_scoring.py`'s content to new `ocr-service/tests/test_scoring.py` (update imports from `app.services.ocr_pipeline` to `app.pipeline`; assertions unchanged) (depends on T014)
- [ ] T016 [US2] Add `ocr-service/tests/test_endpoints.py`: `POST /scan` happy path → `detected_value` populated, `detection_method: "ocr"` per `contracts/ocr-service-api.md`
- [ ] T017 [US2] Add to `ocr-service/tests/test_endpoints.py`: `POST /serial` happy path → `detected_serial` populated
- [ ] T018 [US2] Add to `ocr-service/tests/test_endpoints.py`: `GET /health` → `200 {"status": "ok"}`
- [ ] T019 [US2] Add test to `backend/tests/test_ocr_endpoint.py`: `engine=ocr` with `ocr_url` configured, mocked successful OCR-service response → reading/serial returned correctly (mocks `_ocr_service_scan`)
- [ ] T020 [US2] Add test to `backend/tests/test_ocr_endpoint.py`: `engine=ocr` with `ocr_url` configured but unreachable (mocked `httpx.ConnectError`/`TimeoutException`) → clear error returned, no silent LLM substitution (FR-005, US2/AC2)

### Implementation for User Story 2

- [ ] T021 [US2] Move the EasyOCR-only functions removed in T008 into `ocr-service/app/pipeline.py` verbatim (image preprocessing, 7-segment correction, numeric scoring, serial extraction, reader/lock) (depends on T008, T002)
- [ ] T022 [US2] Create `ocr-service/app/main.py`: FastAPI app exposing `POST /scan`, `POST /serial`, `GET /health` per `contracts/ocr-service-api.md`, calling into `app/pipeline.py` (depends on T021)
- [ ] T023 [US2] Add an `ocr` service to `docker-compose.yaml` (`build: ./ocr-service`, no `depends_on` from `backend` — stays optional per FR-007) and an example (commented) `OCR_URL` on the `backend` service (depends on T022)
- [ ] T024 [P] [US2] Update `README.md`: add `OCR_URL`/`OCR_API_KEY` rows to the environment-variable table, and a short "OCR Service (Optional)" section (Constitution Principle V — Deployment Parity)
- [ ] T025 [US2] Add an `ocr-service-tests` job to `.github/workflows/test.yaml` running pytest against `ocr-service/`, mirroring the existing `backend-tests` job (depends on T022)
- [ ] T026 [US2] Add an `ocr-service-sast` job to `.github/workflows/test.yaml` running `bandit -r app` inside `ocr-service/`, mirroring the existing `backend-sast` job (depends on T022)
- [ ] T027 [P] [US2] Add an `ocr-service` image build step to `.github/workflows/build.yaml` (`ghcr.io/dmkif/zaehl-o-mat-ocr`, same pattern as the existing backend/frontend build steps) (depends on T022)

**Checkpoint**: User Story 2 fully functional — OCR service works standalone and via the backend; unreachable case handled cleanly; new code has CI coverage (Constitution Principles I, X).

---

## Phase 5: User Story 3 - Deploy the optional OCR service via Helm (Priority: P3)

**Goal**: The Helm chart can deploy the OCR service as an optional, off-by-default component, matching the Compose path (Constitution Principle V).

**Independent Test**: `helm template`/`helm install` with default values → no OCR Deployment rendered, backend LLM-only. With `ocr.enabled=true` → OCR service provisioned and reachable by the backend.

### Implementation for User Story 3

- [ ] T028 [P] [US3] Add an `ocr:` block to `chart/values.yaml` (`enabled: false`, `image.repository`/`image.tag`, `resources`) per `data-model.md`'s "Configuration Entity: Helm `ocr` values block"
- [ ] T029 [US3] Create `chart/templates/deployment-ocr.yaml`, guarded by `{{- if .Values.ocr.enabled }}`, referencing `.Values.ocr.image.*` / `.Values.ocr.resources` (depends on T028)
- [ ] T030 [P] [US3] Add an `ocr` Service entry to `chart/templates/services.yaml`, guarded the same way as T029 (depends on T029)
- [ ] T031 [P] [US3] Add an `ocr` NetworkPolicy block to `chart/templates/networkpolicy.yaml` — ingress only from backend pods (Constitution Principle VII, OWASP A01) (depends on T029)
- [ ] T032 [P] [US3] Wire the backend Deployment's `OCR_URL` env var in `chart/templates/deployment-backend.yaml` to the in-cluster `ocr` Service DNS name when `.Values.ocr.enabled` is true (depends on T029)

**Checkpoint**: All three user stories independently functional and testable.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final verification across all stories.

- [ ] T033 [P] Run `quickstart.md` validation scenarios 1-6 end-to-end, confirm each expected outcome
- [ ] T034 [P] Confirm the existing Trivy CI job (`security-scan`) picks up `ocr-service/requirements.txt` automatically — verification only, no code change expected
- [ ] T035 [P] Confirm the backend image size reduction meets SC-001 (≥30% smaller than before this feature) by comparing built image sizes before/after

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately.
- **Foundational (Phase 2)**: Depends on Setup — blocks User Story 1.
- **User Story 1 (Phase 3)**: Depends on Foundational. This is the priority baseline — everything else depends on it (spec.md: "the baseline everything else builds on").
- **User Story 2 (Phase 4)**: Depends on User Story 1 (T021 moves functions T008 removed; T009's HTTP client needs a server to call). Not independently deployable before US1.
- **User Story 3 (Phase 5)**: Depends on User Story 2 (needs the `ocr-service` image to deploy).
- **Polish (Phase 6)**: Depends on all three user stories being complete.

### Within Each User Story

- Tests are written first and must fail before the corresponding implementation task.
- `ocr_pipeline.py` changes (T008, T009) precede the router changes that call them (T010).
- Config (T004) precedes anything that reads it (T011).

### Parallel Opportunities

- T002, T003 (Setup) — different files.
- T006 (US1, new file `test_health.py`) is parallel to T005/T007 (same file, sequential with each other).
- T014 (US1) is parallel to T005-T013 — different file, no dependency.
- T015 (US2 test) is parallel to T016-T018 (different file); T016/T017/T018 are sequential (same file).
- T024, T027 (US2) are parallel to the rest of US2 — different files, no blocking dependency once T022 lands.
- T030, T031, T032 (US3) are parallel to each other once T029 lands — three different files.
- T033, T034, T035 (Polish) are all independent verification tasks — parallel.

---

## Parallel Example: User Story 1

```bash
# Once T004 (Foundational) is done, run the new-file test in parallel with the others:
Task: "Add health-check test in backend/tests/test_health.py"        # T006 [P]
Task: "Add engine=ocr-not-configured test in test_ocr_endpoint.py"   # T005
Task: "Add engine=llm regression test in test_ocr_endpoint.py"       # T007
```

## Parallel Example: User Story 3

```bash
# Once T029 (deployment-ocr.yaml) lands:
Task: "Add ocr Service entry to chart/templates/services.yaml"                  # T030 [P]
Task: "Add ocr NetworkPolicy to chart/templates/networkpolicy.yaml"             # T031 [P]
Task: "Wire OCR_URL in chart/templates/deployment-backend.yaml"                 # T032 [P]
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001-T003)
2. Complete Phase 2: Foundational (T004)
3. Complete Phase 3: User Story 1 (T005-T014)
4. **STOP and VALIDATE**: build the backend, confirm no `easyocr` import, run T005-T007, confirm `/api/health` and LLM extraction behave correctly.
5. This alone satisfies the direct ask ("OCR nur als optional gelten, mit LLM bessere Ergebnisse") — ship it before touching the OCR service itself.

### Incremental Delivery

1. Setup + Foundational → foundation ready.
2. User Story 1 → backend clean, LLM-only, OCR reported unavailable. **MVP.**
3. User Story 2 → OCR service deployable, backend calls it, failure handled. CI covers the new service.
4. User Story 3 → Helm parity with Compose.
5. Polish → cross-cutting verification (quickstart, image-size target, CI coverage confirmation).

## Notes

- Unlike the generic template, US2 and US3 here are **not** independent of US1/US2 respectively — the spec's own priority ordering (`spec.md`: "This is the mechanism that replaces the removed in-process OCR path... secondary to the P1 baseline") makes this a sequential dependency chain, not three parallel tracks. Each story is still independently *testable* once its prerequisites are done.
- Every code-touching task maps to a Functional Requirement or Success Criterion in `spec.md` — see inline `FR-###`/`SC-###`/`US#/AC#` references above for traceability.
- Commit after each task or logical group, per this project's normal git workflow (PR required — see constitution Development Workflow).
