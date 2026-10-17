# Tasks: Full Security Review of the Application

**Input**: Design documents from `/specs/004-security-review/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/experiment-catalogue.md, contracts/report-format.md

**Tests**: The experiments ARE the tests (pytest marker `security`). Regression tests for fixes go into `backend/tests/` and must fail before / pass after the fix.

**Organization**: By user story. US2 (experiments) feeds US1 (report) and US3 (fixes); US4 (repeatability) closes.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on open tasks)
- Paths relative to the repository root `~/zaehl-o-mat-004-security-review`

---

## Phase 1: Setup

- [x] T001 Create directory skeleton `security/{rig/standins,experiments,mutants}` with `security/README.md` (scope, safety rules: loopback only, synthetic data, never production)
- [x] T002 Register pytest marker `security` in `security/pytest.ini` (separate from backend suite, opt-in) and add dev-only `security/requirements.txt`
- [x] T003 [P] Create report skeleton `security/REPORT.md` per `contracts/report-format.md` (sections 1–7, empty coverage matrix components × A01–A10)
- [x] T004 [P] Create `security/CHECKLIST.md` with component × A01–A10 rows (backend, frontend, ocr-service, images, chart, compose, ci, migrations, scripts), all unanswered

---

## Phase 2: Foundational (rig — blocks all experiments)

- [ ] T005 [P] Write `security/rig/standins/fake_oidc.py`: discovery, authorize, token, jwks endpoints; configurable claim shapes (list, string, nested, huge), forged/expired/`alg=none` token modes, PKCE verification toggle
- [ ] T006 [P] Write `security/rig/standins/fake_gateway.py`: OpenAI-compatible `/v1/chat/completions` with modes ok / hostile (huge, nested, wrong types, reading == serial) / slow / down / 401
- [ ] T007 [P] Write `security/rig/standins/fake_ocr.py`: OCR service stand-in with same modes as T006
- [ ] T008 Write `security/rig/seed.py`: synthetic superadmin, admin, manager, user A, user B, user without group; two properties with disjoint access; meters, readings, small generated images (no real data)
- [ ] T009 Write `security/rig/up.sh` / `down.sh`: podman Postgres 18 on loopback with throw-away password, alembic migrations, uvicorn backend, stand-ins; random free ports; refuses non-loopback targets
- [ ] T010 Write `security/experiments/conftest.py`: rig fixtures, token/client factory per role, asyncio barrier helper for N-parallel requests, repeat helper reporting failure rate, log capture for A09
- [ ] T011 Write `scripts/security-rig.sh`: rig up → pytest `-m security` → rig down (always, on trap); exit code from pytest
- [ ] T012 Smoke check: `scripts/security-rig.sh` starts the rig, one trivial experiment passes, rig is torn down cleanly

**Checkpoint**: rig runs from clean checkout; experiments can be added.

---

## Phase 3: User Story 2 — Prove weaknesses by experiment (P1) 🎯 MVP

**Goal**: Experiment suite per catalogue, run against the rig.
**Independent Test**: `scripts/security-rig.sh` gives pass/fail per experiment ID; mutants make the matching experiment fail.

- [ ] T013 [P] [US2] `security/experiments/test_a01_access_control.py`: E-A01-001 (all endpoints unauthenticated, enumerate routes from OpenAPI), E-A01-002 (cross-user/property by id and filename incl. streaming and file-serving), E-A01-003 (role matrix), E-A01-004 (path traversal), E-A01-005 (extra fields), E-A01-006 (outbound URL SSRF)
- [ ] T014 [P] [US2] `security/experiments/test_a02_misconfiguration.py`: E-A02-001 (default JWT secret / DB URL refuse start), E-A02-002 (headers, `/docs`, error bodies, CORS)
- [ ] T015 [P] [US2] `security/experiments/test_a04_crypto_tokens.py`: E-A04-001 (alg none, wrong key, expired, tampered, algorithm confusion)
- [ ] T016 [P] [US2] `security/experiments/test_a05_injection.py`: E-A05-001 (SQL/header/CRLF/filename payloads in every field), E-A05-002 (stored XSS round trip: API returns raw text; review frontend rendering)
- [ ] T017 [P] [US2] `security/experiments/test_a06_races.py`: E-A06-001 (100 parallel first sign-ins), E-A06-002 (same reading / same image hash), E-A06-003 (role change vs sign-in), E-A06-004 (OIDC state replay/expiry); each ≥ 100 repetitions with failure rate
- [ ] T018 [P] [US2] `security/experiments/test_a07_authentication.py`: E-A07-001 (parallel brute force of superadmin login), E-A07-002 (PKCE), E-A07-003 (group claim shapes incl. string `xadminx` substring seed finding)
- [ ] T019 [P] [US2] `security/experiments/test_a08_integrity.py`: E-A08-001 (hostile gateway/OCR responses validated before storing)
- [ ] T020 [P] [US2] `security/experiments/test_a09_logging.py`: E-A09-001 (DEBUG run, grep for token/code/secret/e-mail; required security events present)
- [ ] T021 [P] [US2] `security/experiments/test_a10_exceptional.py`: E-A10-001 (stop Postgres mid-request, no partial write), E-A10-002 (IdP/gateway/OCR down or slow, bounded wait); both optional subsystems configured and unconfigured
- [ ] T022 [P] [US2] `security/experiments/test_uploads.py`: E-UP-001 (type vs magic bytes, polyglot, over limit, decompression bomb, EXIF abuse, path tricks in filename)
- [ ] T023 [US2] Coverage list: script/test that compares OpenAPI routes and external inputs against experiments touched and writes the list for the report (SC-003)
- [ ] T024 [P] [US2] Mutants in `security/mutants/*.patch`: access check removed, unique/retry removed, `alg=none` accepted, size check removed, gateway output trusted; `scripts/security-rig.sh --mutant <name>` applies the patch to a temp copy only
- [ ] T025 [US2] Run full suite and every mutant; record that each mutant makes its experiment fail (SC-006) and note results in `security/REPORT.md` section 4

**Checkpoint**: experiments run, raw findings (suspected/confirmed) exist.

---

## Phase 4: User Story 1 — Know where the application is weak (P1)

**Goal**: Report covering all components × A01–A10.
**Independent Test**: Every cell of the coverage matrix filled with method and result; each finding has all fields.

- [ ] T026 [P] [US1] Static tooling run, results into `security/REPORT.md` section 5: bandit on `backend/app`, pip-audit, eslint-security and npm audit on `frontend`
- [ ] T027 [P] [US1] Image/secret/config scanning via podman: trivy (fs + images), gitleaks, hadolint on Dockerfiles, kubeconform/helm lint on chart, review `docker-compose.yml`; triage false positives
- [ ] T028 [P] [US1] Manual review backend routers and services (`backend/app/routers`, `services`, `auth`): answer CHECKLIST rows for A01, A04, A05, A07 with `path:line` evidence
- [ ] T029 [P] [US1] Manual review frontend (`frontend/src`): XSS sinks, token storage, redirect handling, CSP; answer CHECKLIST rows
- [ ] T030 [P] [US1] Manual review deployment: Dockerfiles, Helm chart (securityContext, secrets, NetworkPolicy), Compose, CI workflows (pinning, permissions, secrets), migrations, `scripts/`; answer A02, A03, A08 rows; list items not verifiable from the repo (section 6)
- [ ] T031 [US1] Record all findings as `F-###` per data-model.md (severity, location, impact, reproduction experiment ID, evidence, recommendation); include seed finding for string group claim (FR-016)
- [ ] T032 [US1] Fill coverage matrix and summary counts; confirm no secrets/real data in report (FR-017)

**Checkpoint**: report complete except fix status.

---

## Phase 5: User Story 3 — Fix what is serious, track the rest (P2)

**Goal**: All confirmed critical/high fixed with regression test; rest decided.
**Independent Test**: Each regression test fails on pre-fix commit, passes after.

> Concrete fix tasks are added here once T031 lists findings; each follows the pattern below.

- [ ] T033 [US3] Fix string group-claim substring match in `backend/app/routers/auth.py` (`get_or_create_user_from_oidc`): treat string as single group, exact match; regression test in `backend/tests/test_oidc_groups.py` (fails before, passes after)
- [ ] T034 [US3] For each further critical/high finding F-###: write failing regression test in `backend/tests/`, verify it fails, fix, verify it passes, and apply config changes to Compose and Helm together (constitution V); update finding status and PR link in `security/REPORT.md`
- [ ] T035 [US3] Re-run full experiment suite: confirmed races show 0 failures in 100 repetitions (SC-004); update section 4
- [ ] T036 [US3] Decide every medium/low/informational finding (fix, track, accept with reason) in section 7; open tracking entries (issues or `docs/` list) and link them
- [ ] T037 [US3] Final scan: bandit, pip-audit, trivy, gitleaks show no new critical/high (SC-007); log grep from E-A09-001 clean

---

## Phase 6: User Story 4 — Keep the review repeatable (P3)

- [ ] T038 [US4] Finish `security/README.md`: prerequisites (podman, python), the one command, expected duration (< 30 min, SC-005), how to add an experiment, how to run mutants
- [ ] T039 [US4] Verify from a fresh clone (or `git worktree`) that `scripts/security-rig.sh` runs without manual steps; record duration
- [ ] T040 [P] [US4] Add pointer to `security/` in `README.md` and note the opt-in marker in contributor docs; ensure CI default run excludes `security` marker but includes new regression tests

---

## Phase 7: Polish

- [ ] T041 [P] Pre-commit run over all changed files (detect-secrets allowlist only for rig throw-away values with `# pragma: allowlist secret`)
- [ ] T042 Final report review against `contracts/report-format.md`; update `specs/004-security-review/checklists/requirements.md`; release note and version bump decision (patch release if fixes shipped)

---

## Dependencies & Order

- Phase 1 → Phase 2 → Phase 3 (US2) → Phase 4 (US1 report; T026–T030 can start in parallel with Phase 3 since they need no rig) → Phase 5 (US3, needs T031) → Phase 6 (US4) → Phase 7.
- T005–T007 parallel; T008–T010 need stand-in interfaces; T013–T022 parallel after T012.
- T033 can start as soon as T018 confirms the weakness.

## Parallel Example

```text
After T012: T013 T014 T015 T016 T017 T018 T019 T020 T021 T022 (distinct files)
Without rig: T026 T027 T028 T029 T030
```

## Implementation Strategy

- MVP = Phase 1–3 plus T031/T033: rig, experiments, first confirmed finding fixed.
- Then complete report (US1), remaining fixes (US3), repeatability (US4).
- Do not commit or push without operator request.
