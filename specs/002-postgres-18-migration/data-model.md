# Phase 1 Data Model: Postgres 16→18 Data Migration to Kubernetes

This feature introduces no new persisted schema (no new tables, no new
Alembic migration) — it moves the *existing* application schema and data
between two PostgreSQL instances unchanged. This document instead
describes the conceptual entities the migration procedure itself
operates on (per spec.md's Key Entities section) and the one real schema
fact the procedure depends on: the exact set of application tables.

## Conceptual Entities

### Source Instance

| Field | Description |
|---|---|
| `kind` | `compose` (only kind supported as a source in this feature — User Stories 1 and 2 both start from the local Compose instance) |
| `db_dsn` | Connection string to the running source PostgreSQL 16 container (local, e.g. `postgresql://zaehlwart:***@localhost:5432/zaehlwart` via `podman exec`/port-forward) |
| `uploads_path` | Path to the source `uploads` Docker named volume's mount point |
| Invariant | MUST NOT be written to by any step of the migration procedure (FR-003) |

### Target Instance

| Field | Description |
|---|---|
| `kind` | `compose` (local, post-image-bump) or `k8s` (Helm-deployed) |
| `db_dsn` | Connection string to the freshly initialized target PostgreSQL 18 (alpine) — local `db` service, or the Helm chart's bundled StatefulSet reached via `kubectl exec`/port-forward |
| `uploads_target` | Local `uploads` volume (already shared, no transfer needed for `kind=compose`) or the Helm chart's `-uploads` PersistentVolumeClaim, reached via the backend pod that already mounts it (`kind=k8s`) |
| Invariant | MUST be empty of application data before restore (FR-009 — see "Empty target" below); MUST NOT already be non-empty from a prior partial attempt |

### Migration Procedure

The dump → transfer → restore → verify sequence (`scripts/pg-migrate/
migrate.sh`), parameterized by `--target=compose|k8s`. Stateless between
runs — it reads Source/Target Instance connection details from operator-
supplied flags/environment, not from any persisted state file.

### Verification Report

| Field | Description |
|---|---|
| `table` | Application table name (one row per entry in `app.database.Base.metadata.tables`) |
| `source_count` | `SELECT count(*)` on the Source Instance |
| `target_count` | `SELECT count(*)` on the Target Instance |
| `match` | `source_count == target_count` |
| `overall_pass` | `true` iff every table's `match` is `true` |

Produced by `scripts/pg-migrate/verify.py`; consumed by the operator to
decide whether to proceed to decommissioning the source (a separate,
manual action per FR-010 — never triggered by the report itself).

## Application Tables (authoritative source: `backend/app/models.py`)

The verification step and the FR-009 "does the target already have data"
check both operate over exactly this set — obtained programmatically via
`app.database.Base.metadata.tables` after `import app.models`, listed
here for reference only (the code MUST NOT hardcode this list):

| Table | Primary key style | Notes |
|---|---|---|
| `users` | UUID | Accounts — password hashes; covered by FR-012's dump-encryption requirement |
| `properties` | UUID | Address data; covered by FR-012 |
| `property_users` | BigInt (sequence) | Join table — sequence current-value MUST carry over (FR-006) |
| `meters` | UUID | |
| `readings` | BigInt (sequence) | References `serial_image_path`/image path resolved against the uploads volume (FR-004) |
| `price_entries` | BigInt (sequence) | |
| `oil_deliveries` | BigInt (sequence) | |
| `oil_market_prices` | BigInt (sequence) | |
| `oidc_pkce_states` | (see model) | Transient OIDC state — expected near-empty at migration time, included for completeness/correctness, not because its content matters long-term |

`alembic_version` (Alembic's own bookkeeping table, not a
`Base.metadata` entry) is carried over automatically by the
whole-database `pg_dump`/`pg_restore` (FR-005) but is deliberately
**excluded** from the application-table set used by FR-008's
verification and FR-009's empty-target check — per this feature's
Clarifications, a schema with only `alembic_version` populated (the
normal state right after the backend's own startup `alembic upgrade
head` runs against a fresh target) counts as empty.

## State Flow

```text
[Source: PG16 + uploads]  --pg_dump (18-image client, read-only)-->  encrypted dump file
encrypted dump file  --transfer (kubectl cp / local copy)-->  target host
encrypted dump file  --gpg decrypt-->  plaintext dump  --pg_restore (18-image client)-->  [Target: PG18 + uploads, freshly initialized]
[Target restored]  --verify.py (row counts per table)-->  Verification Report
Verification Report: pass  --operator decision (manual)-->  decommission [Source] (optional, separate action)
Verification Report: fail  --operator decision (manual)-->  discard [Target]; [Source] still fully intact and in use
```

Every arrow that carries the dump or uploads archive across a host
boundary carries it gpg-encrypted (Decision 5, `research.md`); every
plaintext/encrypted copy is deleted once the Verification Report passes
(FR-012).
