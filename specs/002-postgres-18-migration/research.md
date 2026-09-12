# Phase 0 Research: Postgres 16→18 Data Migration to Kubernetes

## Decision 1: Dump/restore, never `pg_upgrade` or a physical volume copy

**Decision**: Use logical export/import (`pg_dump` → `pg_restore`) against
a freshly initialized target. Never attempt `pg_upgrade` between the two
`postgres:16-alpine`/`postgres:18-alpine` (or `18-trixie`) container
images, and never copy the raw data directory between them.

**Rationale**:
- `pg_upgrade` requires both the old and new server binaries to be
  present in the same filesystem/environment and, critically, requires
  the same libc/collation provider on both sides unless every
  collation-dependent index is rebuilt anyway — which is exactly the
  16-alpine→18-alpine (musl→musl, fine) but 18-trixie→18-alpine
  (glibc→musl, **not** fine) case this feature must also handle for any
  already-existing Helm release. A logical dump/restore sidesteps this
  entirely: every index is built fresh by `CREATE INDEX`/constraint
  creation during restore, under whatever collation the *target*
  actually uses. This is also PostgreSQL's own documented guidance for
  moving data across a libc/collation-provider change.
- The two official images do not ship both a 16 and an 18 server binary
  side by side, so an in-place `pg_upgrade` isn't even mechanically
  available without building a custom image — extra complexity for a
  path that would still leave the collation problem unsolved for the
  production (Helm) target.

**Alternatives considered**:
- `pg_upgrade` with `--link`: rejected — fastest option, but doesn't
  apply here (different images/collation providers) and would still need
  a custom multi-version image.
- Physical volume/file copy: rejected outright — `PG_VERSION` mismatch
  refuses to start (confirmed in this repo's own PR #9 review), and even
  if forced, glibc/musl collation divergence risks silent index
  corruption on text columns.

## Decision 2: Run `pg_dump`/`pg_restore`/`psql` from the *newer* (18) image

**Decision**: Perform the dump by running `postgres:18-alpine`'s
`pg_dump` binary, connecting over the network to the *source* (16)
server — not the 16 image's own `pg_dump`. Use the same 18-image binaries
for `pg_restore` and the verification `psql`/`psycopg2` connections.

**Rationale**: This is upstream PostgreSQL's own recommended pattern for
major-version upgrades via dump/restore — newer client tools understand
both the older server's wire protocol/catalog and the newer server's
target format, whereas the older client's dump is not guaranteed to be
optimal for restoring into a newer server. Since this project already
builds/runs `postgres:18-alpine` as a container, getting its client
binaries costs nothing extra — no new host dependency, just an ephemeral
`podman run --rm postgres:18-alpine pg_dump ...`-style invocation
(exactly how this feature's own research was validated: an ephemeral,
`--rm`, no-persisted-volume container was already used to test the
18-alpine image directly against this repo's Alembic migrations).

**Alternatives considered**:
- Host-installed `postgresql-client`: rejected — adds a new host
  dependency this project doesn't otherwise require; every environment
  that can run this app can already run `podman`/`docker` and `kubectl`.
- The 16 image's own `pg_dump`: rejected per upstream guidance above.

## Decision 3: `pg_dump -Fc` (custom format), whole-database dump

**Decision**: Dump the entire application database (`zaehlwart`) in
PostgreSQL's custom archive format (`-Fc`), not `pg_dumpall` and not a
plain-SQL dump.

**Rationale**:
- A whole-database dump automatically includes every table's data, every
  sequence's *current value* (via the `SETVAL` calls `pg_dump` emits),
  and the `alembic_version` table — satisfying FR-005 and FR-006 with
  zero bespoke code. Nothing needs to special-case sequences or the
  Alembic bookkeeping table.
- `pg_dumpall` also dumps cluster-wide roles/tablespaces, which this
  project doesn't need to migrate — the target's own role
  (`zaehlwart`/`POSTGRES_USER`) is already provisioned by the target
  container/StatefulSet's own init environment (matching the existing
  Compose/Helm secret pattern), so a single-database `pg_dump` is the
  right scope.
- Custom format (`-Fc`) is compressed by default, supports
  `pg_restore`'s selective/parallel restore options if ever needed, and
  carries an internal table-of-contents `pg_restore` validates on read —
  a concrete integrity control (OWASP A08).

**Alternatives considered**:
- Plain SQL dump (`-Fp`): rejected — larger, no built-in integrity
  check, and behaves less predictably on restore error (a plain SQL
  script keeps executing past some error classes unless run with strict
  flags, whereas `pg_restore` reports failures per object).
- `pg_dumpall`: rejected — out-of-scope cluster-level objects, and would
  fight with the target's own container-managed role provisioning.

## Decision 4: Uploads volume transfer via `tar` piped through `kubectl exec`/`cp`

**Decision**: For the Kubernetes target, package the source uploads
directory as a `tar` stream and transfer it into a pod that mounts the
target's `-uploads` PersistentVolumeClaim (the backend pod itself, since
it already mounts that PVC at `/data/uploads` per `chart/templates/
deployment-backend.yaml`), then extract there. For the local Compose
target, no transfer is needed at all — the `uploads` named volume is
already shared between the old and new `db`-adjacent containers
regardless of the Postgres image version, since it isn't tied to
Postgres at all.

**Rationale**: Reuses infrastructure that already exists (the backend
pod already mounts the uploads PVC) instead of provisioning a new
transfer-only pod or sidecar. `kubectl cp`/`kubectl exec` are already a
required operator tool for this project (Helm deployments). Keeping the
archive encrypted end-to-end (Decision 5) means the transfer channel's
own transport security is a secondary control, not the only one.

**Alternatives considered**:
- A dedicated one-off `busybox` pod mounting the PVC: viable, slightly
  more isolated (doesn't require the backend pod to be temporarily
  reused for a bulk file operation), but adds a manifest to maintain for
  no behavioral gain — noted as a valid tasks-level implementation
  choice if the backend pod turns out to be inconvenient to use this way
  (e.g. resource limits too tight for a large tar stream), but not the
  default.

## Decision 5: `gpg` symmetric encryption for the dump and uploads archive

**Decision**: Encrypt both the database dump and the uploads tar archive
with `gpg --symmetric --cipher-algo AES256` before they leave the source
host's local disk, decrypt only immediately before restore/extraction on
the target side, and securely delete (`shred -u`, falling back to `rm
-f` where the underlying filesystem doesn't support `shred`, e.g. some
overlay/CSI volumes) every plaintext and encrypted copy once the
verification step (User Story 3) passes.

**Rationale**: Directly implements FR-012 (dump) and this plan's
extension of the same requirement to the uploads archive (Constitution
Principle VII — both artifacts carry personal data under the same threat
model). `gpg` is already present on essentially any Linux host and needs
no new dependency or key-management infrastructure — a passphrase
supplied interactively by the operator (never logged, never written to a
file the script controls) is proportionate for a one-off, operator-run
migration, versus standing up asymmetric key distribution for a
single-use transfer.

**Alternatives considered**:
- Asymmetric (`gpg --recipient`) or `age` encryption: viable, slightly
  cleaner for a repeatable/automated pipeline, but requires the operator
  to first generate and safely handle a keypair — more setup for a
  single migration event than a symmetric passphrase.
- Relying solely on transport security (`kubectl`'s TLS, `ssh`/`scp`):
  rejected as the *only* control — it protects data in transit but not
  data at rest on whatever intermediate host briefly holds the dump file
  (e.g. the operator's own laptop), which FR-012 explicitly requires
  covering.

## Decision 6: Verification via `app.database.Base.metadata.tables`, not a hardcoded list

**Decision**: The verification step imports the backend's own
`app.database.Base` and `app.models` (setting `PYTHONPATH`/running from
within the `backend` venv) to enumerate the authoritative list of
application tables, then runs `SELECT count(*)` against both source and
target for each, reporting per-table and overall pass/fail.

**Rationale**: Matches Constitution Principle VI (Brownfield Respect) —
the exact same mechanism `alembic/env.py` already uses
(`import app.models  # ensure all models are registered`) to avoid a
hand-maintained table list drifting from the real schema. It also gives
a precise, unambiguous implementation of the FR-009 Clarification's
"application tables" threshold (`Base.metadata.tables`, which excludes
`alembic_version` since that table is Alembic's own bookkeeping, not a
declared SQLAlchemy model) — the same definition is reused for both the
pre-restore "is the target really empty" check and the post-restore
verification report, so the two checks can never disagree with each
other.

**Alternatives considered**:
- `information_schema.tables` introspection with a manual exclude-list
  for `alembic_version`: rejected — reintroduces exactly the
  hand-maintained-list drift risk this decision avoids, for no benefit
  over importing the models that already exist.

## Decision 7: Integration test uses ephemeral, `--rm` containers — no CI infrastructure change

**Decision**: The integration test (`test_migration_integration.py`)
starts two throwaway `postgres:18-alpine` containers itself (via
`subprocess`/the same container runtime already available in CI), seeds
one with known fixture rows, runs `migrate.sh`'s dump→restore→verify
path between them, asserts `verify.py` reports a match, and tears both
containers down — regardless of pass/fail.

**Rationale**: `ubuntu-latest` GitHub Actions runners already provide a
container runtime, so this needs no new CI infrastructure. It mirrors
exactly how this feature's own PR #9 predecessor work was manually
validated (an ephemeral `postgres:18-alpine` container, `--rm`, no
persisted volume, used only to prove Alembic migrations apply cleanly)
— turning that ad hoc manual check into a reusable, repeatable
automated test satisfies Constitution Principle I for this feature.

**Alternatives considered**:
- Mocking `pg_dump`/`pg_restore` entirely: rejected — the whole point of
  this feature is that a *real* dump/restore round-trip is safe across
  the version/collation change; mocking the actual tool invocation would
  defeat the test's purpose.
- A `docker-compose`-based test fixture checked into the repo
  permanently: rejected as unnecessary ongoing footprint for a test that
  only needs two short-lived containers for its own duration.

## Decision 8: k8s connection mechanism — exec for restore, port-forward for verify

**Decision**: For `--target=k8s`, restore via `kubectl cp` (encrypted
dump) into the target's own `postgres:18-alpine` pod, then `kubectl exec`
that pod's own `pg_restore` locally against `localhost` — no separate
client container needed on the restore side, since the target pod
already runs the exact image Decision 2 would otherwise spin up
separately. `verify.py`, being an external Python process (not something
exec'd inside a pod), reaches the in-cluster database via `kubectl
port-forward` to the Postgres Service instead.

**Rationale**: The target pod already *is* `postgres:18-alpine` — reusing
its own binaries via `exec` avoids provisioning a redundant client
container; Decision 2's "use the newer image's client tools" concern was
written for the *dump* side (reaching a 16 source, which has no 18
binaries available in-cluster). `verify.py` has no pod of its own to run
inside, so port-forward is the only way to reach a ClusterIP-only Service
from a host-run script.

**Alternatives considered**: A standalone client pod for both restore and
verify — rejected as redundant for restore (the target pod already has
what's needed) and more moving parts than a single `port-forward` for
verify.
