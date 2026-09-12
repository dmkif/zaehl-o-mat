# Implementation Plan: Postgres 16→18 Data Migration to Kubernetes

**Branch**: `002-postgres-18-migration` | **Date**: 2026-09-12 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-postgres-18-migration/spec.md`

## Summary

Provide one parameterized, scripted migration procedure that moves the
application's Postgres data and uploaded meter photos from an existing
PostgreSQL 16 source (the local Docker Compose `db-data`/`uploads`
volumes) into a freshly initialized PostgreSQL 18 (alpine) target —
either the local Compose `db` service after its image bump, or the Helm
chart's bundled PostgreSQL StatefulSet in the operator's Kubernetes
cluster. The approach is logical dump/restore (never `pg_upgrade` or a
physical volume copy), because that is the only path that is both
version-safe (16→18) and collation-safe (glibc `trixie` → musl
`alpine`) — a restore rebuilds every index natively under the target's
own collation rules, and a `pg_dump`/`pg_restore` cycle carries sequence
state and the `alembic_version` table across for free. The source is
never written to; the operator explicitly decides when to decommission
it, only after an automated verification step confirms a match.

## Technical Context

**Language/Version**: Bash (orchestration: dump/restore/transfer
sequencing, `kubectl`/`podman`/`docker` invocations) + Python 3.12
(verification step — reuses the existing `backend` venv/dependencies)

**Primary Dependencies**:
- `pg_dump`/`pg_restore`/`psql` from a `postgres:18-alpine` container —
  per official PostgreSQL upgrade guidance, the *newer* version's client
  tools are used against both the older (16) source and the newer (18)
  target, so no new tool is installed on the operator's host beyond what
  `podman`/`docker` and `kubectl` already require.
- `gpg` (already present on any normal Linux/dev host) for symmetric
  encryption of the dump and uploads archive in transit/at rest (FR-012).
- The existing `backend` Python environment (`psycopg2`, `app.database`,
  `app.models`) for the verification step — table names are read from
  `Base.metadata.tables`, never hand-duplicated, so the check can't drift
  from the real schema (Constitution Principle VI).

**Storage**: No schema change — same PostgreSQL schema, moved between two
instances via logical dump/restore. No new database or table introduced
by this feature.

**Testing**: pytest, mirroring the existing backend suite's structure:
- A unit test for the pure comparison logic (given two
  table→row-count mappings, does it correctly report match/mismatch) —
  no real database needed.
- An integration test that runs the full
  dump → restore → verify cycle end-to-end against two ephemeral,
  throwaway PostgreSQL containers (one seeded with known fixture rows,
  one empty) started and torn down by the test itself — this is the same
  pattern already manually validated by hand for this feature's own
  research (spin up `postgres:18-alpine` with `--rm`, no persisted
  volume). Gated to run where a container runtime is available, matching
  how CI already provides Docker on its `ubuntu-latest` runners.

**Target Platform**: Linux — the same two deployment targets the rest of
the project already supports (Docker/Podman Compose host, Kubernetes via
the existing Helm chart). No new target platform introduced.

**Project Type**: Operational script + runbook, not a new application
feature. No backend/frontend request-handling code changes; a new
`scripts/pg-migrate/` directory plus documentation. No new HTTP
endpoint or CLI surface exposed to end users — this is operator tooling
run out-of-band from the running application.

**Performance Goals**: None — per spec Assumptions, this is a
self-hosted, single-tenant-scale, offline-maintenance-window operation;
optimizing dump/restore throughput for very large databases is
explicitly out of scope.

**Constraints**:
- The source database and source uploads volume MUST NOT be written to
  by the migration procedure at any step (FR-003) — every command that
  touches the source is read-only (`pg_dump`, a read-only tar/copy of the
  uploads directory).
- The application MUST be stopped (or the source made read-only) for the
  duration of the dump (FR-007) — the script MUST refuse to proceed
  without an explicit operator confirmation of this precondition.
- The dump and the uploads archive MUST be `gpg`-encrypted for any leg
  where they leave the source or target host's local disk, and MUST be
  deleted from every intermediate/transfer location once verification
  (User Story 3) passes (FR-012) — extended here, as a plan-level
  decision, to the uploads archive as well as the database dump, since
  both traverse the same transfer step and both contain data covered by
  Constitution Principle VII's threat model (personal data, no reason to
  encrypt one and not the other).
- The restore step MUST refuse to run against a target whose application
  tables already contain rows (FR-009, threshold defined in spec
  Clarifications) rather than merge or overwrite.
- One script, parameterized by `--target=compose|k8s`, not two
  divergent implementations (FR-002, Constitution Principle V).

**Scale/Scope**: Unchanged from the rest of the project — self-hosted,
single-tenant-per-deployment scale (spec Assumptions: modest data
volume, single maintenance-window dump/restore is sufficient).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*
*Checked against Constitution v1.2.0 (10 principles).*

| Principle | Check | Result |
|---|---|---|
| I. Test-Backed Changes | A migration script that mishandles real operator data is exactly the "silent regression costs users real meter data" risk this principle exists for. Requires a unit test for the comparison logic and an integration test exercising the real dump→restore→verify cycle against ephemeral containers before this can merge. | **PASS** (planned — see Phase 1 test tasks in `/speckit-tasks`) |
| II. Graceful Degradation for Optional Subsystems | N/A — this feature adds no new optional runtime subsystem to the running application; it's an offline, out-of-band operator tool. | **PASS (N/A)** |
| III. Secure-by-Default Secrets & Access | The `gpg` passphrase protecting the dump/uploads archive MUST be supplied interactively by the operator (or via an env var read once at invocation) and MUST NOT be logged, echoed, or written to any file the script controls. No new persistent secret is introduced. | **PASS** |
| IV. Schema Changes via Migrations | No schema change — `pg_dump`/`pg_restore` moves the existing schema (including `alembic_version`) as-is; the target ends up at the exact same Alembic revision as the source, not a re-run of migrations. | **PASS (N/A)** |
| V. Deployment Parity (Compose ⇄ Helm) | FR-002 requires one script covering both targets; `--target=compose` and `--target=k8s` share the same dump/verify core and differ only in how they reach the database (local `podman exec` vs `kubectl exec`/port-forward) and the uploads volume (local bind mount vs `kubectl cp` into a PVC-mounted pod). | **PASS** (explicit design goal) |
| VI. Brownfield Respect | Reuses the existing `scripts/` directory convention (already holds ad hoc bash/Python ops tooling), the existing `backend` venv/dependencies and `app.database`/`app.models` for verification (no hand-maintained table list), and the existing `ollama`/`ocr` bundled-vs-bring-your-own precedent for how the two deployment targets already coexist in this repo. | **PASS** |
| VII. Security by OWASP Top 10 (NON-NEGOTIABLE) | See OWASP mapping table below — every relevant category has a concrete control. | **PASS** |
| VIII. Authenticated Communication by Default | N/A — no new inbound endpoint is added. All connections the script makes (`pg_dump`/`psql` to the source/target Postgres, `kubectl exec`/`cp` to the cluster) are outbound, operator-authenticated administrative connections using credentials that already exist (the DB password from the existing secret, the operator's own kubeconfig) — nothing this principle's inbound-endpoint scope governs. | **PASS (N/A)** |
| IX. Input Validation Is Server-Side Authoritative | N/A in the usual sense (no new user-facing HTTP input), but the script's own `--target` flag MUST be validated against an explicit allowlist (`compose`/`k8s`) rather than executed as a free-form string, and the operator-supplied Kubernetes namespace/release name MUST be passed to `kubectl` as separate argv elements (never interpolated into a shell string) to avoid command injection. | **PASS** |
| X. Verifiable Security (CI Gates) | The new `scripts/pg-migrate/*.py` code touches secrets (the gpg passphrase) and shells out to subprocesses — exactly the code SAST exists to catch — but today's `test.yaml` only runs `bandit` over `backend/app` and `ocr-service/app`, not `scripts/`. This feature MUST add a `scripts-sast` CI job (`bandit -r scripts`), mirroring the existing `ocr-service-sast` job, to close that gap rather than ship new secret-handling code with zero SAST coverage. Trivy (`scan-ref: .`) and gitleaks already cover the whole repository, so no gap there. | **PASS** (new CI job scoped into Phase 1 design below) |

### OWASP Top 10 Mapping (Principle VII)

| Category | Applicable? | Control |
|---|---|---|
| A01 Broken Access Control | Yes | The script creates no new access path — it uses the operator's own already-provisioned credentials (existing DB password, existing `kubectl` context/RBAC). It exposes no new network-reachable endpoint. |
| A02 Security Misconfiguration | Yes | `pg_dump`/`pg_restore`/`psql` run from an ephemeral, non-persisted `postgres:18-alpine` container/pod — no new long-lived service, no new default credentials. |
| A03 Software Supply Chain | Yes | No new pinned dependency: `postgres` (official image, already used), `gpg` (host tool, no package to pin), `kubectl`/`podman` (already required by this project's existing deployment docs). Trivy already scans everything in the repo. |
| A04 Cryptographic Failures | Yes | FR-012: the dump and uploads archive are `gpg`-encrypted (AES256 symmetric) for every leg where they leave local disk; the passphrase is never persisted (Principle III). |
| A05 Injection | Yes | All shell invocations use argv arrays (no string-built shell commands); the verification step uses SQLAlchemy/psycopg2 parameterized queries, never string-formatted SQL; the `--target` flag and any namespace/release name are validated against an allowlist/passed as discrete argv elements before reaching `kubectl`/`psql`. |
| A06 Insecure Design | Yes | Documented threat model: the main new attack surface is the dump-at-rest/in-transit, mitigated by FR-012 (encrypt + mandatory deletion); the main new failure mode is data loss, mitigated by FR-003 (source is always read-only) and FR-009 (refuse non-empty target). |
| A07 Authentication Failures | No new surface | No new login/auth mechanism is introduced; existing DB and Kubernetes credentials are reused unchanged. |
| A08 Software/Data Integrity | Yes | FR-008's verification step (row-count comparison per table, driven by `Base.metadata.tables`) is the concrete control gating trust in the target before the source may be decommissioned; `pg_dump`'s custom format (`-Fc`) carries its own internal TOC consistency checks that `pg_restore` validates on read. |
| A09 Logging & Alerting | Yes | The script's output MUST NOT print the DB password, the `gpg` passphrase, or JWT/session secrets — only progress and row-count/verification results. |
| A10 Exceptional Conditions | Yes | FR-011 (disk-space precheck before starting), FR-009 (non-empty-target refusal), and fail-closed sequencing (any failed step aborts before the next one runs — no partial dump used for a restore) are the concrete, explicit-handling controls; nothing is left to an unhandled exception mid-migration. |

No violations — **Complexity Tracking is not needed.**

## Project Structure

### Documentation (this feature)

```text
specs/002-postgres-18-migration/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md         # Phase 1 output (/speckit-plan command)
├── quickstart.md         # Phase 1 output (/speckit-plan command)
└── tasks.md              # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

No `contracts/` directory: this feature exposes no new HTTP API, CLI
consumed by other systems, or other machine-readable interface — it's an
operator-run script/runbook, so there is no external contract to
document beyond the script's own `--help` and this feature's
`quickstart.md`.

### Source Code (repository root)

```text
scripts/pg-migrate/                  # NEW — migration tooling
├── migrate.sh                       # Orchestrator: dump -> transfer -> restore,
│                                     #   --target=compose|k8s, calls into verify.py
│                                     #   and encrypt/decrypt helpers; refuses to run
│                                     #   without operator confirmation of the
│                                     #   maintenance-window precondition (FR-007)
├── verify.py                        # Row-count comparison (FR-008): connects to
│                                     #   source + target, enumerates tables via
│                                     #   app.database.Base.metadata.tables (imported
│                                     #   from backend/), reports per-table match/
│                                     #   mismatch and an overall pass/fail
├── lib.sh                           # Shared shell helpers: disk-space check
│                                     #   (FR-011), target-already-has-data check
│                                     #   invocation (FR-009, via verify.py), gpg
│                                     #   encrypt/decrypt-and-shred helpers (FR-012)
└── tests/
    ├── test_verify_unit.py          # Pure comparison-logic unit tests (no DB)
    └── test_migration_integration.py # Full dump/restore/verify cycle against two
                                      #   ephemeral postgres:18-alpine containers

.github/workflows/test.yaml          # + `scripts-sast` job (bandit -r scripts),
                                      #   mirroring the existing ocr-service-sast job
                                      #   (Constitution Principle X gap closed)

README.md                            # + short "Migrating existing data to
                                      #   Kubernetes" subsection under the existing
                                      #   "Helm Chart (Kubernetes)" heading, pointing
                                      #   at scripts/pg-migrate/ and this feature's
                                      #   quickstart.md
```

**Structure Decision**: This is operator tooling, not an application
feature — no `backend/`, `frontend/`, `ocr-service/`, `docker-compose.yaml`,
or `chart/` files are modified (both already declare `postgres:18-alpine`
as of the prior chore change; this feature only moves *data* onto that
already-declared target). The only touch-points are a new
`scripts/pg-migrate/` tool with its own tests, one new CI job, and a
short README pointer — matching Constitution Principle VI (Brownfield
Respect: this repo already keeps ad hoc ops scripts under `scripts/`)
and Principle V (the tool itself, not the app's deployment config, is
what needs to work identically against both targets).

## Complexity Tracking

*No Constitution Check violations — this section is intentionally empty.*
