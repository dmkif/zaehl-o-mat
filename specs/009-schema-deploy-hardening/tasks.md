# Tasks: Schema and Deployment Hardening

Tests first where code is involved (red -> green).

## US1 - F-014
- [x] T001 [US1] Test (red): `backend/tests/test_migration_enums.py` - every model enum name is covered by the migration label table
- [x] T002 [US1] `backend/alembic/versions/0008_align_enums.py` idempotent, autocommit, no-op downgrade
- [x] T003 [US1] `scripts/verify-migration-enums.sh` + run (a) alembic-built, (b) create_all+stamp; evidence in `quickstart.md`

## US3 - F-016
- [x] T004 [US3] chart: tri-state enabled, required ingressFrom, egress policies, postgres automount false
- [x] T005 [US3] `helm lint`, template (default / ocr on / policy on / egress on), kubeconform --strict

## US2 - F-015
- [x] T006 [US2] docker-compose.yaml hardening; `podman compose config`

## US4 - DEP
- [x] T007 [US4] `.dockerignore` x3, HEALTHCHECK, frontend non-root, UID 1000, digests
- [x] T008 [US4] `test.yaml` permissions, action pinning (if verifiable)
- [x] T009 README/quickstart, REPORT.md disposition, commit
