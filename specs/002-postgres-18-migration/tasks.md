---

description: "Task list template for feature implementation"
---

# Tasks: Postgres 16→18 Data Migration to Kubernetes

**Input**: Design documents from `/specs/002-postgres-18-migration/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md — all present and current.

**Tests**: Included — Constitution Principle I (Test-Backed Changes, NON-NEGOTIABLE) requires coverage for every backend-data-affecting change; a migration script that mishandles real operator data is exactly the risk this principle exists for. Both a pure-logic unit test and a real dump/restore/verify integration test (against ephemeral, throwaway containers) are included per `plan.md`'s Constitution Check.

**Organization**: Tasks are grouped by user story (spec.md P1/P2/P3) to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Exact file paths are included in every description

---

## Phase 1: Setup

**Purpose**: Scaffold the new `scripts/pg-migrate/` tool per `plan.md`'s Project Structure.

- [X] T001 Create `scripts/pg-migrate/` skeleton: `migrate.sh`, `lib.sh`, `verify.py`, `tests/test_verify_unit.py`, `tests/test_migration_integration.py` (empty/stub files with correct shebangs and executable bits on the `.sh` files)
- [X] T002 [P] Add version/config constants to the top of `scripts/pg-migrate/lib.sh` — the exact `postgres:18-alpine` client image tag to use for `pg_dump`/`pg_restore`/`psql` (Decision 2, `research.md`) and the `gpg` cipher (`AES256`, Decision 5)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared verification/safety infrastructure every user story depends on — the table-enumeration and comparison mechanism (used by US1 informally to confirm success, by US2's FR-009 guard, and by US3's formal verification report) and the encryption/disk-space helpers no story can skip.

**⚠️ CRITICAL**: Must complete before any User Story implementation task.

- [X] T003 Implement table-count collection in `scripts/pg-migrate/verify.py`: given a DSN, imports the backend's `app.database.Base` and `app.models` (via `PYTHONPATH` into `backend/`, per Decision 6) to enumerate application table names, connects via `psycopg2`, and returns a `{table_name: row_count}` mapping — never a hardcoded table list
- [X] T004 [P] Implement pure comparison logic in `scripts/pg-migrate/verify.py`: given two `{table_name: row_count}` mappings, return a per-table match/mismatch structure plus an overall pass/fail boolean (FR-008) — no DB access in this function, testable in isolation
- [X] T005 [P] Implement the `verify.py` CLI in `scripts/pg-migrate/verify.py`: `--source`/`--target` DSN arguments, calls T003 for each side and T004 to compare, prints a per-table report, exits `0` (pass) / `1` (row mismatch) / `2` (connection or setup error)
- [X] T006 [P] Implement the disk-space precheck in `scripts/pg-migrate/lib.sh` (FR-011): checks free space at the dump-output path and the restore-target path before any destructive or long-running work begins, aborts with a clear message if insufficient
- [X] T007 [P] Implement the `gpg` helpers in `scripts/pg-migrate/lib.sh` (FR-012, Decision 5): `encrypt_to_file` (prompts for a passphrase interactively, never logs or echoes it), `decrypt_from_file`, and `secure_delete` (`shred -u`, falling back to `rm -f` where `shred` isn't supported)
- [X] T008 Implement the `migrate.sh` skeleton in `scripts/pg-migrate/migrate.sh`: argument parsing; `--target` validated against an explicit `compose`/`k8s` allowlist, never executed as a free-form string (Constitution Principle IX); a required `--confirm-maintenance-window` flag that aborts with a clear message if absent (FR-007); sources `lib.sh` (depends on T006, T007)
- [X] T009 Implement the "target already has data" guard in `scripts/pg-migrate/migrate.sh` (FR-009): calls T003's table-enumeration against the target DSN, sums row counts across application tables, and aborts before any restore step if the sum is greater than zero — a target with only `alembic_version` populated counts as empty per spec.md's Clarifications (depends on T003, T008)

**Checkpoint**: Foundation ready — User Story 1 implementation can begin.

---

## Phase 3: User Story 1 - Upgrade the local development database in place (Priority: P1) 🎯 MVP

**Goal**: An operator can bring their existing local PostgreSQL 16 `db-data` volume forward to PostgreSQL 18 (alpine) with zero data loss, using only this feature's script, with the source volume left fully intact throughout.

**Independent Test**: Starting from a Compose `db-data` volume created under PostgreSQL 16 with known content, run `migrate.sh --target=compose`, then confirm every row is present on the new volume and the original volume is untouched.

### Tests for User Story 1 ⚠️

> Write these tests FIRST, confirm they FAIL before the implementation tasks below.

- [X] T010 [P] [US1] Unit test for the comparison logic (T004) in `scripts/pg-migrate/tests/test_verify_unit.py`: hand-built `{table: count}` dict pairs covering an exact match, a single-table mismatch, and an empty-vs-populated case, asserting the correct per-table result and overall pass/fail
- [X] T011 [US1] Integration test in `scripts/pg-migrate/tests/test_migration_integration.py`: start two ephemeral `postgres:18-alpine` containers (`--rm`, no persisted volume — same pattern already hand-validated for this feature's own research), seed the "source" with known fixture rows across at least one UUID-keyed table, one sequence-keyed table (plus a non-default sequence position), and a known `alembic_version` value, run `migrate.sh`'s dump→restore path between them, then `verify.py`, and assert: an overall `PASS` with exact row, sequence-position, and `alembic_version` parity (FR-005, FR-006); the source container's own row counts are unchanged after the dump completes (FR-003); and no plaintext or encrypted dump file remains anywhere `migrate.sh` staged one (FR-012) — this MUST fail red before T012-T015 exist

### Implementation for User Story 1

- [X] T012 [US1] Implement the dump step in `scripts/pg-migrate/migrate.sh`: run `pg_dump -Fc` from an ephemeral `postgres:18-alpine` container against the local source DSN (Decision 2/3, read-only per FR-003), then encrypt the resulting file via T007's `encrypt_to_file` (FR-012) (depends on T009, and on T011 failing red)
- [X] T013 [US1] Implement the `--target=compose` restore step in `scripts/pg-migrate/migrate.sh`: initialize a fresh local `postgres:18-alpine` data volume, decrypt the dump via T007's `decrypt_from_file`, and `pg_restore` into it (depends on T012)
- [X] T014 [US1] Wire `migrate.sh` to call `verify.py` (T005) after the `--target=compose` restore, print the report, and on `PASS` securely delete every plaintext/encrypted dump copy via T007's `secure_delete` (FR-012); on `FAIL`, leave the target in place for operator inspection and print a clear failure message without deleting anything (depends on T005, T013)
- [X] T015 [US1] On a successful `--target=compose` run, have `migrate.sh` print the exact next-step commands for the operator (rename the old `db-data` volume aside, rename the new restored volume to `db-data`, `podman-compose up -d`) — `migrate.sh` MUST NOT perform this rename itself, since repointing/decommissioning is a separate, explicit operator action (FR-010) (depends on T014)

**Checkpoint**: User Story 1 fully functional — local dev migration works end-to-end, source volume untouched and verified, ready for `/speckit-implement` to demo independently.

---

## Phase 4: User Story 2 - Provision the production Kubernetes deployment with existing data (Priority: P2)

**Goal**: An operator can migrate their local database and uploaded meter photos into a fresh Helm-deployed Kubernetes release, ending up with full data + image parity and zero manual fix-up.

**Independent Test**: Starting from the local data, run `migrate.sh --target=k8s` against a Helm release with an empty application database, then confirm the cluster-hosted application shows the same properties/meters/readings/images as the local instance.

### Tests for User Story 2 ⚠️

- [X] T016 [P] [US2] Extend `scripts/pg-migrate/tests/test_migration_integration.py`: exercise the uploads-archive transfer path using two local temp directories standing in for the source uploads volume and the target-mounted PVC path (keeps the test runnable without a live cluster, per Decision 7) — assert every file present on the "source" directory is present and byte-identical on the "target" directory after the tar → encrypt → transfer → decrypt → extract cycle, and that no plaintext or encrypted archive copy remains at any intermediate location afterward (FR-012); this MUST fail red before T018 exists

### Implementation for User Story 2

- [X] T017 [US2] Implement the `--target=k8s --namespace=<ns> --release=<name>` restore step in `scripts/pg-migrate/migrate.sh`: `kubectl cp` the encrypted dump into the target's own `postgres:18-alpine` pod, then `kubectl exec` that pod's own `pg_restore` locally against `localhost` — no separate client container needed on the restore side (Decision 8, `research.md`) (depends on T012)
- [X] T018 [US2] Implement the uploads-volume transfer for `--target=k8s` in `scripts/pg-migrate/migrate.sh` (Decision 4): tar the local uploads directory, encrypt via T007, `kubectl cp` into the release's backend pod (already mounts the `-uploads` PersistentVolumeClaim per `chart/templates/deployment-backend.yaml`), then decrypt and extract there (depends on T007, and on T016 failing red)
- [X] T019 [US2] Wire the `--target=k8s` path through T009's empty-target guard and T005's `verify.py` call, both reached via `kubectl port-forward` to the in-cluster Postgres Service since `verify.py` is an external Python process, not something `exec`'d inside a pod (Decision 8, `research.md`) — mirroring T014's PASS/FAIL handling and secure-deletion of both local and in-cluster dump/archive copies (depends on T009, T017, T018)
- [X] T020 [US2] Add an explicit assertion to `scripts/pg-migrate/tests/test_migration_integration.py` for US2 Acceptance Scenario 3: running `migrate.sh --target=k8s` a second time against a now-populated target MUST abort via T009's guard rather than overwrite or duplicate rows (depends on T019)

**Checkpoint**: User Story 2 fully functional — cluster migration works, uploads carried over, second-run safety confirmed.

---

## Phase 5: User Story 3 - Verify and safely roll back a failed or unwanted migration (Priority: P3)

**Goal**: An operator can independently re-run verification at any time and trust that a failed/aborted/unwanted migration never costs the source anything, with decommissioning always a distinct, deliberate action.

**Independent Test**: After a completed migration, run `verify.py` standalone and confirm a match; then simulate a mismatch and confirm the source remains fully intact and usable throughout.

- [X] T021 [US3] Harden `verify.py`'s CLI in `scripts/pg-migrate/verify.py`: confirm/adjust exit codes (`0`=pass, `1`=row mismatch, `2`=connection/setup error) and confirm it is fully standalone-invocable with just `--source`/`--target` DSNs, independent of having just run `migrate.sh` in the same session (depends on T005)
- [X] T022 [P] [US3] Integration test in `scripts/pg-migrate/tests/test_migration_integration.py`: after a successful migration between two ephemeral containers, deliberately insert one extra row into the "target" container, re-run `verify.py` standalone, assert it reports that table as a mismatch and exits `1` — then assert the "source" container's row counts are unchanged throughout (FR-003)
- [X] T023 [US3] Create `scripts/pg-migrate/decommission.sh`: the only script permitted to remove a source volume/database (FR-010) — requires the operator to type back the source's identifier (e.g. the Compose volume name) as an interactive confirmation before deleting anything; never invoked by `migrate.sh` or `verify.py` (depends on T015 for the compose-mode volume-naming convention it targets)

**Checkpoint**: All three user stories independently functional and testable; a bad or aborted migration is provably reversible at every stage.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final verification and the Constitution gap identified in `plan.md`.

- [X] T024 [P] Add a `scripts-sast` CI job (`bandit -r scripts`) to `.github/workflows/test.yaml`, mirroring the existing `ocr-service-sast` job — closes the Constitution Principle X gap noted in `plan.md` (today's `bandit` jobs only cover `backend/app` and `ocr-service/app`)
- [X] T025 [P] Add a "Migrating existing data to Kubernetes" subsection under the existing `## Helm Chart (Kubernetes)` heading in `README.md`, pointing at `scripts/pg-migrate/` and this feature's `specs/002-postgres-18-migration/quickstart.md`
- [X] T026 [P] Run `quickstart.md` Scenarios 1-3 end-to-end where a live Kubernetes cluster is available in the executing session, or at the component level (as this feature's own research already validated the ephemeral-container dump/restore mechanics) where it is not — record which scenarios ran live vs. at the component level, matching the precedent set by `specs/001-ocr-optional-container/tasks.md`'s T037. Result: **Scenario 1 ran fully live** against the real local Compose stack's actual production-like data (4 meters, 1 property, 1 user, 27 readings, 839 oil_market_prices rows) — dumped, restored into a fresh `postgres:18-alpine` target, verified PASS with exact row parity, dump securely deleted, source (`zaehl-o-mat_db_1`) confirmed unmodified throughout. **Scenario 3 ran fully live** too: `verify.py` re-run standalone (outside `migrate.sh`) against that same real source/target pair independently confirmed PASS. **Scenario 2** (Kubernetes) could not run live — no cluster is configured in this session (`kubectl config current-context` reports none) — its component mechanisms are covered instead by the automated integration tests (dump reuse identical to Scenario 1; uploads tar/encrypt/decrypt/extract round-trip tested with two local directories; exit-code/refusal logic tested against two ephemeral containers) plus a manual argument/gate smoke test of `migrate.sh`'s CLI validation. A genuine, unrelated bug surfaced only by running Scenario 1 for real: `postgres:18+` images refuse to start at all when data is mounted directly at `/var/lib/postgresql/data` (a breaking upstream change) — this affected the already-merged `docker-compose.yaml` `db` service (not previously restarted since the 18-alpine version bump) and was fixed here (added `PGDATA=/var/lib/postgresql/data/pgdata`, matching the Helm chart's pre-existing convention) since `migrate.sh`'s own target-creation needed the identical fix to function.
- [X] T027 Manual review pass over every `scripts/pg-migrate/*.py`/`*.sh` file and its log/print output: confirm no DB password, `gpg` passphrase, JWT secret, or raw connection string with embedded credentials is ever printed or written to a log (Constitution Principle VII A09, Principle X). Result: reviewed every `pgmigrate::log`/`pgmigrate::die`/`echo` call and verify.py's error path — none interpolate a DSN, password, or passphrase; only container/pod names, step numbers, table names/counts, and file paths are logged. `bandit -r scripts/pg-migrate --exclude scripts/pg-migrate/tests` reports zero findings.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately.
- **Foundational (Phase 2)**: Depends on Setup — blocks all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational. This is the priority baseline (MVP) — it is also the mechanism User Story 2 reuses for its own dump step (T012).
- **User Story 2 (Phase 4)**: Depends on Foundational and on User Story 1's dump step (T012) and restore/verify wiring pattern (T013-T014). Not independently deployable before US1.
- **User Story 3 (Phase 5)**: Depends on Foundational (T003-T005) and, for T023 specifically, on US1's volume-naming convention (T015). Its verification/re-run tasks (T021-T022) could technically run right after Foundational, but are sequenced last here because they exercise the full migration path US1/US2 build.
- **Polish (Phase 6)**: Depends on all three user stories being complete.

### Within Each User Story

- Tests are written first and must fail before the corresponding implementation task.
- The dump step (T012) precedes both the compose restore (T013) and the k8s restore (T017) — one dump, two possible restore destinations.
- `verify.py`'s core (T003-T005) precedes every place it's called (T009, T014, T019, T021-T022).
- `lib.sh`'s helpers (T006, T007) precede `migrate.sh`'s skeleton (T008) and every step that uses them (T009, T012-T014, T017-T019).

### Parallel Opportunities

- T002 (Setup) has no dependency on T001's file creation content and can be drafted in parallel once the files exist.
- T004, T005, T006, T007 (Foundational) — different concerns, T004/T005 both touch `verify.py` sequentially with each other but are parallel to T006/T007 (`lib.sh`).
- T010 (US1 unit test) is parallel to T011 (US1 integration test) — different files.
- T016 (US2 test) is parallel to nothing else in its phase (single test task) but independent of US1's already-complete implementation tasks.
- T022 (US3) is parallel to T021 — different concerns, though both touch `verify.py`/its tests; sequence if conflicts arise in review.
- T024, T025, T026 (Polish) are independent — parallel.

---

## Parallel Example: User Story 1

```bash
# Once Foundational (T003-T009) is done, write both tests together:
Task: "Unit test for comparison logic in scripts/pg-migrate/tests/test_verify_unit.py"          # T010 [P]
Task: "Integration test for dump/restore/verify cycle in scripts/pg-migrate/tests/test_migration_integration.py"  # T011
```

## Parallel Example: Foundational lib.sh vs verify.py

```bash
# T004/T005 (verify.py) and T006/T007 (lib.sh) touch different files:
Task: "Pure comparison logic in scripts/pg-migrate/verify.py"           # T004 [P]
Task: "verify.py CLI wrapper"                                           # T005 [P]
Task: "Disk-space precheck in scripts/pg-migrate/lib.sh"                # T006 [P]
Task: "gpg encrypt/decrypt/secure-delete helpers in scripts/pg-migrate/lib.sh"  # T007 [P]
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001-T002)
2. Complete Phase 2: Foundational (T003-T009)
3. Complete Phase 3: User Story 1 (T010-T015)
4. **STOP and VALIDATE**: run the local migration against a real (or realistic fixture) `db-data` volume, confirm zero data loss and that the original volume is untouched.
5. This alone satisfies the acute, already-present breakage (Compose declares `postgres:18-alpine`, existing volumes are still PG16) — ship it before touching the Kubernetes path.

### Incremental Delivery

1. Setup + Foundational → shared verification/safety infrastructure ready.
2. User Story 1 → local dev migration works. **MVP.**
3. User Story 2 → Kubernetes migration works, uploads carried over, second-run safety confirmed.
4. User Story 3 → independent verification/rollback safety net, explicit decommission path.
5. Polish → CI SAST gap closed, README pointer, quickstart validation, secret-leak review.

## Notes

- User Story 2 is **not** independent of User Story 1 the way the generic template assumes — it reuses US1's dump step (T012) and PASS/FAIL/secure-deletion pattern (T014) rather than re-implementing them, per spec.md's own priority ordering ("depends on User Story 1's procedure existing first").
- Every code-touching task maps to a Functional Requirement or Success Criterion in `spec.md` — see inline `FR-###`/decision references above for traceability.
- The integration tests (T011, T016, T020, T022) are the concrete implementation of Constitution Principle I for this feature — they exercise a *real* dump/restore/verify cycle against ephemeral containers, never a mocked one (research.md Decision 7).
- Commit after each task or logical group, per this project's normal git workflow (PR required — see constitution Development Workflow).
