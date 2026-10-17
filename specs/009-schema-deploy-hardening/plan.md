# Implementation Plan: Schema and Deployment Hardening

**Branch**: `009-schema-deploy-hardening` | **Spec**: [spec.md](spec.md)

## Constitution Check
I tests first PASS (guard test red before migration); III secure-by-default PASS; IV reversible migration PASS (upgrade additive/idempotent, downgrade no-op documented); V Compose/Helm parity PASS (healthchecks, hardening mirrored where the platform has the notion); VII A02/A03 PASS.

## Decisions
- **F-014**: migration `0008_align_enums.py`, `down_revision = "0006"` (the parallel session-hardening feature creates `0007`; the integrator re-points to `0007` after merge). Uses `op.get_context().autocommit_block()` + `ALTER TYPE <t> ADD VALUE IF NOT EXISTS '<label>'` for every name of `MeterUnit`, `MeterType`, `IntegrationType`, `UserRole`, `PropertyUserRole`, `ReadingSource` (labels hard-coded in the migration, not imported from models, so history stays stable). Types that do not exist are skipped (checked via `pg_type`). No-op on SQLite. Old labels (`kWh`, `L`, `MWh`, `GJ`, `other`, `modbus`, `api`) stay: unused but harmless; rows can never hold them (the models never write them), so no data migration.
- A pure-python guard test `backend/tests/test_migration_enums.py` compares the migration's label table with the model enums (names) and with 0000 (labels only added).
- Throw-away PostgreSQL verification script `scripts/verify-migration-enums.sh` (podman, `postgres:18-trixie`, random loopback port, removed after): (a) `alembic upgrade head` then create one meter per unit/type via SQL insert through the ORM; (b) `create_all` + `alembic stamp 0006`, `upgrade head` no-op, label list unchanged.
- **F-015**: Compose pins `postgres:18-trixie`-family consistent with chart? Chart uses `18-alpine`; keep alpine, pin by exact minor tag, `ollama/ollama:<version>`, `nginx:1.<x>-alpine`; remove Ollama `ports`; add hardening and healthchecks. Ollama keeps default caps (GPU/CDI).
- **F-016**: values `networkPolicy.enabled: null`, `ingressFrom` default `{}` -> `required`; helper `zaehl-o-mat.networkPolicyEnabled`; egress template section. Cluster (k3s-gitops) unaffected while `ocr.enabled=false` and `networkPolicy.enabled` unset.
- **DEP**: files only; digests pinned with `docker.io/library/<img>:<tag>@sha256:...` only where the digest was resolved via `podman`/registry in this session.

## Parallel session note
Migration `0007` is created elsewhere; this feature touches `backend/alembic/versions/0008_*`, `backend/tests/test_migration_enums.py`, `chart/`, `docker-compose.yaml`, Dockerfiles, `.dockerignore`, workflows, README/quickstart.
