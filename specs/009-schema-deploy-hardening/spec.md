# Feature Specification: Schema and Deployment Hardening

**Feature Branch**: `009-schema-deploy-hardening` | **Status**: Draft | **Source**: `security/REPORT.md` F-014, F-015, F-016 and DEP-03/04/06/08 (`security/review/deployment.md`)

## Background
Three medium findings remain that touch the database schema and the deployment artefacts (not application logic): enum drift between models and the initial migration (F-014), weak Compose defaults (F-015), permissive chart NetworkPolicy (F-016). Related low findings on images and CI are folded in because they touch the same files.

## User Stories

### US1 - Meters of every unit and integration type can be created on a migration-built database (F-014, P1)
The models persist enum **names** (`m3`, `kwh`, `liter`; `smart_meter`, `shelly`), the initial migration created other labels (`kWh, m3, L, MWh, GJ, other`; `manual, homeassistant, modbus, api`).
Production state is unknown (`create_all` or Alembic), so the fix must be idempotent on both.
1. **Given** a database built by `alembic upgrade head`, **When** a meter with unit kWh / L / m3 or integration `smart_meter` / `shelly` is created, **Then** 201.
2. **Given** a database built by `create_all` and stamped, **When** the new migration runs, **Then** it changes nothing.
3. Every name of every Python enum used in a DB column exists as label after `upgrade head` (guarded by a unit test, so future drift fails CI).
4. Downgrade is a documented no-op (PostgreSQL cannot drop enum labels); no label is ever removed or renamed.

### US2 - Compose is safe to copy (F-015, P2)
Backend only on loopback (done in 004b), Ollama not published, no weak literals except the dev DB password inside the private network, pinned image tags, `no-new-privileges`, `cap_drop: ALL` where the image allows, `read_only` + tmpfs where the image allows, healthchecks for backend/frontend/ocr/proxy.

### US3 - Chart NetworkPolicy has safe defaults without breaking the cluster (F-016, P2)
1. `networkPolicy.enabled` is tri-state: unset = enabled iff `ocr.enabled` (protects the unauthenticated OCR service). Existing deployments without OCR render exactly as before (no policy).
2. When enabled, `ingressFrom` is **required** (`required` fails the render with a clear message); no more "every pod" default. A selector that is empty is rejected.
3. Optional egress policies (`networkPolicy.egress.enabled`, default false for backward compatibility): backend -> DNS, postgres, ocr, optional external HTTPS (non-private ranges) and `extraTo` rules (gateway); ocr, postgres, frontend: no egress.
4. Postgres pod: `automountServiceAccountToken: false` (own `serviceAccountName` not needed: the pod spec flag suffices) and `drop ALL` is verified in the render.

### US4 - Image and CI hygiene (DEP-03/04/08, P3)
`.dockerignore` for backend/frontend/ocr-service, `HEALTHCHECK` in backend/ocr Dockerfiles, frontend image runs as non-root (65534), explicit UID 1000 for `appuser`, base images pinned by digest (Renovate-updatable), `permissions: contents: read` in `test.yaml`, third-party actions pinned by commit SHA where resolvable offline-safe (see plan: only if the SHA can be verified, else left and listed as deviation).

## Out of scope
Separate migration Job with a privileged role (DEP-05), cosign/SBOM (DEP-07), hash-pinned pip locks (DEP-03 part), operational scripts (DEP-11).

## Success criteria
SC-001 migration verified on throw-away PostgreSQL in both build modes. SC-002 `helm lint` + `helm template` (default and policy-on) + `kubeconform --strict` clean; default render identical in kind set to before for the cluster values. SC-003 `podman compose config` valid. SC-004 pytest green.
