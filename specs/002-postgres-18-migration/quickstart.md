# Quickstart: Postgres 16→18 Data Migration to Kubernetes

This validates the feature end-to-end for both User Story 1 (local
in-place upgrade) and User Story 2 (migration to a Kubernetes cluster),
plus the User Story 3 safety net. It assumes `scripts/pg-migrate/` has
been implemented per `plan.md`/`tasks.md`.

## Prerequisites

- `podman` (or `docker`) and `helm`/`kubectl` already installed — no new
  host dependency beyond what this project already requires.
- `gpg` available on the operator's host (present on essentially any
  Linux/dev machine already).
- The local Docker Compose stack's `db-data` volume contains the
  operator's real data (User Story 1 starting state), or a copy of it
  has already been migrated locally and is now being moved to a cluster
  (User Story 2 starting state).
- For User Story 2: a Helm release already installed in the target
  cluster (`ocr` disabled or enabled, doesn't matter) with an *empty*
  application database — i.e. no properties/meters/readings/users rows
  yet (a fresh install's own auto-`alembic upgrade head` at backend
  startup is expected and fine — see spec.md's Clarifications).

## Scenario 1 — Local development upgrade (User Story 1)

1. Stop the application (satisfies FR-007's maintenance-window
   precondition): `podman-compose stop backend frontend proxy` (leave
   `db` running so it can be dumped).
2. Run the migration in local mode:
   ```bash
   scripts/pg-migrate/migrate.sh --target=compose --confirm-maintenance-window
   ```
   Expected: a disk-space check runs first (FR-011); the script prompts
   once for a `gpg` passphrase; produces an encrypted dump of the running
   PG16 `db` service; starts a fresh `postgres:18-alpine` target volume;
   restores into it; runs `verify.py`; prints a per-table row-count
   report ending in `PASS`.
3. Point Compose at the new volume (per `tasks.md`'s exact mechanism —
   e.g. swap the `db-data` volume name in `docker-compose.yaml` or via an
   operator-followed manual volume-rename step) and start the full stack:
   `podman-compose up -d`.
4. Confirm in the UI: previously known properties/meters/readings are
   present; log in with an existing account works (password hash carried
   over correctly).
5. Confirm the original PG16 `db-data` volume still exists and is
   untouched (`podman volume inspect db-data` — unchanged since before
   step 2).

**Expected outcome**: SC-001, SC-003, SC-005 — zero data loss, completed
using only this feature's scripts/docs, and the old volume is provably
intact throughout.

## Scenario 2 — Migrate into a Kubernetes cluster (User Story 2)

1. Stop the local application the same way as Scenario 1, step 1.
2. Run the migration in cluster mode:
   ```bash
   scripts/pg-migrate/migrate.sh --target=k8s \
     --namespace=<ns> --release=<helm-release-name> \
     --confirm-maintenance-window
   ```
   Expected: same dump/encrypt sequence as Scenario 1, then the encrypted
   dump and the encrypted `uploads` tar archive are transferred via
   `kubectl cp` into the release's backend pod (which already mounts the
   `-uploads` PVC); decrypted and restored/extracted there; `verify.py`
   runs against the in-cluster database (via `kubectl exec`/port-forward)
   and reports `PASS`.
3. Confirm no plaintext or encrypted dump/archive copies remain on either
   the operator's host or inside the cluster (`kubectl exec ... -- ls
   /tmp` or wherever the script staged files — should be empty per
   FR-012).
4. Open the application through the cluster's ingress; confirm the same
   properties/meters/readings/images and login as the local instance.
5. Attempt to re-run the same command against the now-populated cluster
   release: expect a clear refusal (FR-009 / User Story 2 Acceptance
   Scenario 3), not an overwrite or duplicate rows.

**Expected outcome**: SC-001, SC-002, SC-004 — full data + uploads parity
in the cluster, no manual fix-up needed, and the safety refusal on a
second run is verified directly.

## Scenario 3 — Verify and roll back a bad migration (User Story 3)

1. Run Scenario 1 or 2's migration once successfully.
2. Re-run only the verification step in isolation:
   ```bash
   python scripts/pg-migrate/verify.py --source=<source-dsn> --target=<target-dsn>
   ```
   Expected: the same per-table pass/fail report as printed during the
   full run, re-derivable independently of the dump/restore step.
3. Simulate a mismatch (e.g. manually insert one extra row into the
   target's `properties` table, outside the migration procedure) and
   re-run `verify.py`: expect it to report that table as a mismatch and
   an overall `FAIL`.
4. Confirm the source is unaffected by this whole exercise and the
   application can still be started against it at any point
   (`podman-compose up -d` using the original `db-data` volume).

**Expected outcome**: SC-005 and User Story 3's three acceptance
scenarios — a trustworthy, independently re-runnable verification step,
and proof that a bad migration never costs the source anything.
