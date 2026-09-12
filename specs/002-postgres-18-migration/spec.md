# Feature Specification: Postgres 16→18 Data Migration to Kubernetes

**Feature Branch**: `002-postgres-18-migration`

**Created**: 2026-09-12

**Status**: Draft

**Input**: User description: "Migration der bestehenden Postgres-Datenbank (lokale Docker-Compose-Instanz, aktuell postgres:16-alpine mit realen Daten im db-data-Volume) auf postgres:18-alpine, sowie Bereitstellung derselben Migration für das Helm-Chart (bestehende PVC-gestützte Deployments, Wechsel von postgres:18-trixie/glibc auf postgres:18-alpine/musl). Muss abdecken: Major-Version-Sprung 16→18 (kein in-place pg_upgrade zwischen den offiziellen Images möglich, da unterschiedliche Datenverzeichnis-Formate — Dump/Restore-Strategie nötig), Downtime-Fenster, Collation-/libc-Wechsel glibc→musl für bestehende Helm-Releases mit befülltem PVC (Risiko: stille Index-Korruption bei Textspalten ohne REINDEX), Rollback-Plan falls Migration fehlschlägt, sowie Zielumgebung: sowohl die lokale Docker-Compose-Entwicklungsinstanz als auch ein produktives k8s-Cluster des Nutzers, in das die Anwendung anschließend deployed werden soll."

## Clarifications

### Session 2026-09-12

- Q: Should the meter-photo uploads volume (referenced by `Reading` records) move together with the database, or is only the Postgres data in scope? → A: Yes — the uploads volume moves as part of the same migration. A `Reading` whose image file didn't make the trip is a worse outcome than a slightly bigger migration procedure.
- Q: How strictly must the database dump (contains user accounts with password hashes and property/address data) be secured during transport and any intermediate storage? → A: The dump MUST be encrypted at rest and in transit, and MUST be deleted from every intermediate location once the verification step (User Story 3) has passed.
- Q: What exactly counts as a target "already containing application data" for the purpose of refusing to restore into it (FR-009)? → A: Only real data rows count — the sum of rows across application tables (properties/meters/readings/users) being greater than zero. A schema that only has Alembic's own bookkeeping (`alembic_version`) applied, with zero rows elsewhere, counts as empty and may be restored into.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Upgrade the local development database in place (Priority: P1)

An operator running Zähl-O-Mat locally via Docker Compose has a `db-data`
volume created under PostgreSQL 16 with real property/meter/reading data.
After the application's configuration moved to PostgreSQL 18 (alpine), the
operator needs to bring their existing local data forward to that version
without losing anything, so `docker compose up` works again and all
previously recorded readings are still there.

**Why this priority**: This is the immediate, already-present breakage —
the app's Compose file now declares `postgres:18-alpine` while the
operator's disk still holds a PostgreSQL 16 data directory, which refuses
to start under the new image. Nothing else in this feature can be
validated until this baseline path works.

**Independent Test**: Starting from a Compose `db-data` volume created
under PostgreSQL 16 with known content (specific property/meter/reading
rows), run the migration procedure, then start the stack on
`postgres:18-alpine` and confirm every previously known row is present
and the application serves it correctly.

**Acceptance Scenarios**:

1. **Given** a local Docker Compose stack with a populated PostgreSQL 16
   `db-data` volume, **When** the operator runs the migration procedure,
   **Then** a new PostgreSQL 18 (alpine) data volume is produced containing
   every row from every application table, and the old volume is left
   intact and untouched.
2. **Given** the migration has completed, **When** the operator starts the
   stack, **Then** the backend connects successfully, `alembic_version`
   reflects the same migration state as before, and previously recorded
   properties/meters/readings (including their images) are visible and
   correct in the UI.
3. **Given** the migration procedure is interrupted partway (e.g. the
   operator's machine loses power during the dump step), **When** the
   operator inspects the state afterward, **Then** the original PostgreSQL
   16 volume is unmodified and still usable — the failed attempt has cost
   nothing but time.

---

### User Story 2 - Provision the production Kubernetes deployment with existing data (Priority: P2)

An operator who has been running Zähl-O-Mat locally is now deploying it to
their own Kubernetes cluster via the project's Helm chart for the first
time. They want the new, cluster-hosted instance (PostgreSQL 18 on the
chart's bundled StatefulSet, plus the uploads volume) to start with all of
the data and meter photos already recorded locally — not an empty
database.

**Why this priority**: This is the actual stated goal ("Migration in mein
k8s-Cluster durchführen") — it depends on User Story 1's procedure
existing first (the same dump-and-restore mechanics apply, just against a
different target), and on the uploads volume being carried over too so
`Reading` records don't end up pointing at missing images.

**Independent Test**: Starting from the migrated (or original) local data,
run the migration procedure against a fresh Helm installation, then
confirm the cluster-hosted application shows the same
properties/meters/readings and meter photos as the local instance did.

**Acceptance Scenarios**:

1. **Given** a Helm release installed with the bundled PostgreSQL 18
   (alpine) StatefulSet and no application data yet, **When** the operator
   runs the migration procedure against it, **Then** the cluster's database
   contains every row from the source instance and the cluster's uploads
   volume contains every previously uploaded meter photo.
2. **Given** the migration to the cluster has completed, **When** the
   operator opens the application through the cluster's ingress, **Then**
   they see the same properties, meters, readings, and reading images as
   in the local instance, and can log in with existing accounts.
3. **Given** an existing Helm release that already has *some* data in its
   PostgreSQL 18 database (e.g. a prior partial attempt or test data),
   **When** an operator attempts to run the migration procedure again,
   **Then** the procedure MUST refuse to silently overwrite or merge into
   non-empty target data — it fails clearly rather than corrupting or
   duplicating records.

---

### User Story 3 - Verify and safely roll back a failed or unwanted migration (Priority: P3)

An operator who has performed the migration wants to confirm it actually
succeeded before treating the old instance as disposable, and — if
something looks wrong — wants to fall back to the pre-migration state
without having lost anything in the meantime.

**Why this priority**: Reduces the risk of the higher-priority stories by
giving the operator a safety net; it is not required for the migration to
technically function, but it is required for an operator to be able to
trust and safely execute it, especially against production data.

**Independent Test**: After a completed migration, run the verification
step and confirm it reports a match; then simulate an operator deciding
not to proceed (e.g. finding a discrepancy) and confirm the original
instance is still fully intact and the application can be pointed back at
it with zero data loss.

**Acceptance Scenarios**:

1. **Given** a completed migration, **When** the operator runs the
   verification step, **Then** they get a clear, per-table comparison
   (row counts at minimum) between the source and target, and a clear
   pass/fail result.
2. **Given** a migration whose verification step reports a mismatch,
   **When** the operator decides not to proceed, **Then** the application
   can continue running against the original (source) database and
   uploads volume with no data loss, and the target can be discarded.
3. **Given** a successfully verified migration, **When** the operator
   explicitly decides to decommission the old instance, **Then** that is a
   distinct, deliberate action — decommissioning never happens
   automatically as a side effect of the migration or verification steps.

---

### Edge Cases

- What happens if there isn't enough free disk space to hold both the
  source data directory and the dump/restore output at the same time? The
  procedure MUST check for and report this before it starts destructive or
  long-running work, not fail partway through.
- What happens if the application (backend) is still writing to the source
  database while the dump is being taken? Rows written after the dump
  starts are not guaranteed to be in it — the procedure MUST require the
  application to be stopped (or the source made read-only) for the
  duration of the dump, and MUST make this requirement explicit to the
  operator.
- What happens to database roles/permissions and sequence current-values
  (e.g. auto-increment counters) across the restore? They MUST be
  preserved exactly — a restored table must continue numbering from where
  the source left off, not restart from 1.
- What happens if a meter-photo file referenced by a `Reading` row is
  missing from the source uploads volume at migration time (e.g. already
  lost previously)? The procedure MUST report this as a warning per
  affected row rather than failing the entire migration, since it reflects
  pre-existing data state, not a migration defect.
- What happens on the production (Kubernetes) target if the migration is
  run against a database that already has rows in it? Per User Story 2's
  acceptance scenario 3, the procedure MUST detect this and refuse rather
  than merge or overwrite.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The migration procedure MUST NOT rely on in-place binary
  upgrade of the PostgreSQL data directory (e.g. `pg_upgrade` across
  different major versions or across different libc/collation providers)
  — it MUST use a logical export-and-import approach (dump of all data,
  restore into a freshly initialized target) so every index and text
  ordering is rebuilt natively under the target's own collation rules.
- **FR-002**: The same migration procedure MUST work, without
  environment-specific rewriting, against both the local Docker Compose
  target and the Helm-chart-deployed Kubernetes target — one documented
  and/or scripted process, not two divergent ones (per this project's
  Deployment Parity convention).
- **FR-003**: The migration procedure MUST leave the source database and
  source uploads volume completely unmodified — it is read-only with
  respect to the source at every step, so a failed or aborted attempt
  costs nothing.
- **FR-004**: The migration procedure MUST include the uploads volume
  (meter-photo files referenced by `Reading.serial_image_path` and the
  reading's own image) alongside the database dump/restore, so restored
  `Reading` rows continue to resolve to their images on the target.
- **FR-005**: The migration procedure MUST preserve the Alembic migration
  state (the `alembic_version` table) exactly, so the restored database is
  recognized by the application as being at the same schema revision as
  the source — no reapplication or divergence of migrations.
- **FR-006**: The migration procedure MUST preserve sequence/counter state
  (e.g. auto-increment or UUID-generation state, whichever the schema
  uses) so newly created rows on the target do not collide with
  previously migrated ones.
- **FR-007**: The migration procedure MUST require and clearly document a
  maintenance window: the source application MUST be stopped (or the
  source database made read-only) before the dump is taken, and this MUST
  be stated as a precondition, not discovered by the operator after data
  loss.
- **FR-008**: The migration procedure MUST provide a verification step
  that compares source and target (at minimum: row counts per table) and
  reports a clear pass/fail result before the operator is expected to
  decommission the source.
- **FR-009**: The migration procedure MUST refuse to run its restore step
  against a target database that already contains application data —
  defined as the sum of row counts across the application's own tables
  (e.g. properties, meters, readings, users) being greater than zero —
  rather than merging or silently overwriting it. A target whose schema
  has only Alembic's own bookkeeping applied (`alembic_version`, populated
  automatically on backend startup) with zero rows elsewhere counts as
  empty and MAY be restored into.
- **FR-010**: Decommissioning the source (deleting the old volume/data)
  MUST be a separate, explicit, manually-triggered action — never an
  automatic consequence of running the migration or verification steps.
- **FR-011**: The migration procedure MUST check for sufficient available
  disk space for the dump/export output before starting, and fail fast
  with a clear message if there isn't enough, rather than partway through.
- **FR-012**: The database dump (which contains user accounts with
  password hashes and property/address data) MUST be encrypted at rest and
  in transit for any leg of the procedure where it leaves the source or
  target host's local disk, and MUST be deleted from every intermediate
  or transfer location once the verification step (User Story 3) has
  passed — an unencrypted or lingering copy of the dump is a defect in
  the procedure, not an acceptable operator tradeoff.

### Key Entities

- **Source Instance**: The existing PostgreSQL 16 database (local Docker
  Compose) plus its associated uploads volume — read-only input to the
  migration, never modified by it.
- **Target Instance**: A freshly initialized PostgreSQL 18 (alpine)
  database — either the local Compose `db` service after this feature, or
  the Helm chart's bundled PostgreSQL StatefulSet in the operator's
  Kubernetes cluster — plus its associated uploads volume/PVC.
- **Migration Procedure**: The documented and/or scripted dump → transfer
  → restore → verify sequence that moves data from a Source Instance to a
  Target Instance without loss.
- **Verification Report**: The per-table comparison result produced after
  a restore, used by the operator to decide whether to trust the target
  and decommission the source.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of rows present in every application table on the
  source are present and unchanged on the target after migration (zero
  data loss), confirmed by the verification step.
- **SC-002**: 100% of meter-photo files referenced by migrated `Reading`
  rows that existed on the source are present and correctly linked on the
  target.
- **SC-003**: An operator can complete the local development migration
  (User Story 1) end-to-end, using only the documentation/scripts produced
  by this feature, without needing outside research.
- **SC-004**: An operator can complete the production Kubernetes migration
  (User Story 2) and have the application fully functional against the
  new instance with zero manual data correction afterward.
- **SC-005**: An aborted or failed migration attempt leaves the source
  fully intact and the application able to keep running against it, 100%
  of the time (verified by User Story 1's Acceptance Scenario 3 and User
  Story 3's Acceptance Scenario 2).

## Assumptions

- The application's scale (self-hosted, single-tenant-per-deployment, per
  the project's existing technical context) makes an offline maintenance
  window an acceptable and industry-standard approach; this feature does
  not need to design a zero-downtime/online migration path.
- `postgres:18-alpine` is already the target version for both Docker
  Compose and the Helm chart (delivered separately, ahead of this
  feature) — this feature is about moving *data* onto that version, not
  about choosing or changing the target image itself.
- The Helm chart's own capability to install and run the application in a
  Kubernetes cluster (ingress, secrets, storage) already exists and is out
  of scope here — this feature covers only the data-migration procedure
  layered on top of an already-working chart installation.
- The operator has shell/`kubectl`/`docker`/`psql` access to both the
  source and target environments — the migration procedure may be a
  script and/or a runbook, not a self-service UI feature.
- "Real data" in the local instance is modest in scale (self-hosted,
  personal/small-property use per this project's stated scope), so a
  logical dump/restore completing within a single maintenance session is
  a reasonable expectation; this feature does not need to design for
  very-large-database (multi-hour) dump/restore optimization.
