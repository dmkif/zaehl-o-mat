# Tasks: Low findings - data, upload and OCR area

Tests first (red -> green).

- [x] T001 Tests (red): `backend/tests/test_low_data_validation.py` (F-023, F-024, F-037, F-021), `test_low_duplicate_race.py` (F-022), `test_low_error_handling.py` (F-035), `test_image_hygiene.py` (F-032, F-033), `test_migration_0009.py`, `ocr-service/tests/test_auth.py` (F-030)
- [x] T002 F-023/F-037: `schemas/common.py` (safe strings, bounded Decimal), schemas, meters router (replaced_by_id, integration_config JSON), offset bound, hint-rescan NUL
- [x] T003 F-021: server-derived `source`; retention skips rows with evidence
- [x] T004 F-022: model index + migration `0009` (de-duplicate, reversible), 409 on IntegrityError
- [x] T005 F-024: `User.property_users` `passive_deletes`
- [x] T006 F-032/F-033: `services/image_hygiene.py`, used by scan, serial crop, bulk scan; hash over stored bytes
- [x] T007 F-035: `error_handlers.py`, response built before commit
- [x] T008 F-030: ocr-service auth + startup warning; Compose and chart key wiring; README
- [x] T009 Migration 0009 up/down on throw-away PostgreSQL 18 (podman, de-dup of 3 same-hash rows kept all rows, unique insert refused, downgrade drops the index)
- [ ] T010 (integrator) run the security rig; mark F-021..F-024, F-030, F-032, F-033, F-035, F-037 fixed in `security/REPORT.md` (not edited here to avoid merge conflicts)
