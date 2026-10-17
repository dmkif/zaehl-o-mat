# Tasks: Security Review Follow-up (medium findings)

Tests first for every task (red -> green). Three packages run in parallel (disjoint files, see plan.md).

## Package A - uploads (F-004, F-011)
- [x] T001 [P] [US1] Tests (red): `backend/tests/test_upload_ownership.py` - attach foreign file, read via foreign reading, hint-rescan foreign path (no gateway call), path shape (traversal, absolute, other ext), delete keeps referenced file, legacy reading still served
- [x] T002 [US1] Model `UploadFile` + migration `backend/alembic/versions/0006_upload_files.py` (reversible, tested up/down on throw-away PostgreSQL), record on scan/bulk-scan/upload in `routers/ocr.py` and `routers/uploads.py`
- [x] T003 [US1] Validate `image_path`/`serial_image_path` in `schemas/readings.py` + `routers/readings.py`; ownership in serve (`routers/uploads.py`), `hint-rescan` (`routers/ocr.py`), delete (`routers/readings.py`)
- [x] T004 [P] [US6] Tests (red): `backend/tests/test_image_limits.py` and `ocr-service/tests/` - 20000x20000 PNG, 65535 GIF header, 40000 BMP header rejected before storing; normal image ok; `already_cropped` upscale capped
- [x] T005 [US6] Header-based pixel/side check in `routers/ocr.py` (scan, bulk-scan, serial crop) + settings `MAX_IMAGE_PIXELS`/`MAX_IMAGE_SIDE` in `config.py` (only these two settings; package C owns the rest of config.py: coordinate by appending at the end of the Settings fields), Compose/chart env parity, OCR service limit and upscale cap

## Package B - roles (F-005, F-006)
- [x] T006 [P] [US2,US3] Tests (red): `backend/tests/test_role_escalation.py` - manager add/remove/change member and own role, property PATCH -> 403; last property admin protected; admin and property admin still work; admin demotes/deletes superadmin -> 403; last superadmin protected; log events emitted via `security_log`
- [x] T007 [US2] `routers/properties.py`: membership + property edit require property-admin (`delete` level) or global admin; last-admin guard
- [x] T008 [US3] `routers/admin.py`: target-superadmin guard, last-superadmin guard, self-change rules; `log_event` on role change / user delete

## Package C - secrets + logging (F-009, F-010)
- [x] T009 [P] [US4] Tests (red): `backend/tests/test_config_secrets.py` - weak JWT, deny-list, short superadmin password, half-configured superadmin, error text never contains the value; strong config starts; update existing config tests that used weak fixtures
- [x] T010 [US4] Validation in `config.py`; `docker-compose.yaml` required variables (`${VAR:?}`), `DEBUG` default false, README (generation commands), chart values comment; fix any test/rig fixture that used weak values (`security/rig/up.sh` generates strong ones - verify)
- [x] T011 [P] [US5] Tests (red): `backend/tests/test_security_events.py` - events for login ok/failed, sso rejected (no group names), superadmin ok/failed, 401/403 denial, and log filter redacting `code`/`state` in the uvicorn access log format
- [x] T012 [US5] Use `security_log.log_event` in `auth.py`/`routers/auth.py`; access-log redaction filter installed in `main.py`; superadmin login re-asserts `UserRole.superadmin` (F-006)

## Integration (after A, B, C)
- [ ] T013 Merge packages, run full backend suite + `ocr-service` tests + bandit; fix conflicts in `config.py`/`models.py`
- [ ] T014 Run the security rig for the affected experiments (E-A01-002/003/005, E-UP-001, E-A02-001, E-A09-001, E-A07) and record results in `security/REPORT.md` (mark F-004/005/006/009/010/011 fixed)
- [ ] T015 PR, CI, merge, tag release, GitOps chart bump

## Notes

### Package B
- Red (before implementation): `pytest tests/test_role_escalation.py` -> 13 failed, 6 passed. Failures were the expected ones: manager add/self-promote/remove/PATCH returned 2xx instead of 403, last property admin removable/demotable (204/201 instead of 409), own property role changeable, admin could demote superadmin (200), last superadmin demotable/deletable, superadmin could not delete another superadmin (403 instead of 204), no `member_*`/`user_*` security events. The 6 passing tests are the unchanged allowed flows (property admin, global admin, meter access for managers).
- Green: `tests/test_role_escalation.py`, `test_admin.py`, `test_properties.py` all pass; full suite 482 passed, remaining 23 failures are package C's in-progress tests (`test_config_secrets.py`, `test_security_events.py`).
- Existing test changed: `test_properties.py::TestUpdateProperty::test_property_manager_can_update` -> renamed `..._cannot_update`, now expects 403 (it encoded the unsafe behaviour).
- Decisions: last property/superadmin guard returns 409; self-targeted membership add/remove returns 403 (applies to everyone incl. global admin); a superadmin may demote/delete another superadmin; events: `member_added|member_changed|member_removed|property_update|user_role_changed|user_deleted` (denied ones carry `reason=`).


## Notes

### Package C
- Red first: `test_config_secrets.py` + `test_security_events.py` -> `38 failed, 5 passed` (before any implementation).
- Green: both files + `test_config_gateway.py` -> `58 passed`; full backend suite: all remaining failures belong to packages A/B (upload ownership, role escalation, image limits and their in-progress knock-ons); package C files all green. bandit on touched app files: no issues (one B105 false positive annotated with `nosec`).
- Config (`config.py`): JWT secret >= 32 chars (stripped), deny-list (case-insensitive, entry repeated/short suffix, long entries as substring, single repeated char/empty); superadmin password >= 12 chars + deny-list; user/password both or neither; `hide_input_in_errors=True` so pydantic does not echo input values; new fields `max_image_pixels`/`max_image_side` (gt=0) appended for package A.
- Events: `login` (ok / failed: invalid_state, state_expired, token_exchange_failed, no_access_token), `sso_login` denied `no_permitted_group` (no groups/e-mail), `superadmin_login` ok/failed (not_configured, bad_credentials, user_inactive), `superadmin_role_restored`, `auth_denied` (401: no_credentials, invalid_token, user_inactive_or_unknown; 403: insufficient_role). Superadmin login re-asserts `UserRole.superadmin` (F-006).
- Access log: `AccessLogRedactionFilter` in `main.py` on `uvicorn.access` masks `code=`/`state=` values.
- Tests changed: `test_config_gateway.py` (`jwt_secret_key="x"*40` is now rejected as single-character -> strong fixture). Other existing fixtures (conftest JWT 35 chars, monkeypatched superadmin passwords) unaffected because `settings` is patched after validation.
- Compose: `JWT_SECRET_KEY`/`SUPERADMIN_PASSWORD` via `${VAR:?}`, `SUPERADMIN_USER` default admin, `DEBUG` default false. README and chart comments updated. `security/rig/up.sh` generates 64-char JWT and 24-char superadmin password: compliant, unchanged. `docker` not available here, Compose interpolation not run.
- Not touched: `scripts/test_ocr*.sh` still use `admin/admin123` (dev helper scripts, outside ownership).

## Notes

### Package A (F-004, F-011)
Red (before implementation, each run for the right reason):
- `backend/tests/test_upload_ownership.py` (model stub only, no logic): `19 failed, 11 passed` - foreign/unregistered/odd-path attach accepted (201), foreign unattached file served, hint-rescan of a foreign file reached the LLM mock, scan/bulk-scan wrote no `upload_files` row, delete removed a still-referenced file. First collection run failed with `ImportError: UploadFile` (model missing).
- `backend/tests/test_image_limits.py`: `9 failed, 1 passed` - 20000x20000 PNG, 65535 GIF, 40000 BMP, side>12000 accepted with 200 and written to the upload dir; `ImageTooLarge` missing.
- `ocr-service/tests/test_image_limits.py`: `9 failed, 1 passed` - bombs returned 200, `MAX_UPSCALED_SIDE` missing.

Green (after implementation): `backend`: `551 passed` (whole suite incl. the other packages' tests at that moment; only package C's `test_config_secrets.py` was still red while C worked); `ocr-service`: `38 passed`.
Migration `0006_upload_files` verified up -> `\d upload_files` -> down to 0005 (table gone) -> up again on a throw-away `postgres:18-trixie` (podman, random loopback port, container removed afterwards).
`helm template` renders `MAX_IMAGE_PIXELS`/`MAX_IMAGE_SIDE` for backend and ocr.

Decisions / deviations:
- Registry helpers live in new `backend/app/upload_registry.py` (path regex, `register_upload`, `may_use_upload`, `other_references`); the path regex also allows the `_serial` suffix and `.tiff` that scan produces.
- Rule: a file is usable by its uploader, a global admin, or (when attaching/rescanning for a property) when `upload_files.property_id` equals that property; serving an unattached file also allows members of the file's property. Unknown and foreign files give the same 400 on attach (404 on hint-rescan/serve for unknown files); a file referenced by readings is governed by those readings' property access (legacy path, now "any referencing reading the caller may read").
- Unregistered, unattached files are no longer served to non-admins (they were served to any logged-in user before). Existing tests in `test_uploads.py`, `test_readings.py`, `test_ocr_endpoint.py` (hint-rescan) were adapted to register their uploads; `ocr-service/tests/test_endpoints.py` fixture JPEG was missing the SOF marker and is now generated with PIL.
- Limits are read through `getattr(settings, "max_image_pixels"/"max_image_side", default)` in `services/ocr_pipeline.py::image_limits()`. Scan/serial -> 413; bulk-scan -> per-item `error`, nothing stored. Unparseable headers are also rejected there.
- Not done: `jobs/retention.py::cleanup_orphan_uploads` (not in package A) leaves orphaned `upload_files` rows behind when it deletes old files; harmless (serve gives 404), can be cleaned in a later change.
