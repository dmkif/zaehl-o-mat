# Tasks: UI fixes, per-property role

Tests first (red -> green).

- [x] T001 [US1] Tests (red): `backend/tests/test_property_my_role.py` (property admin/manager/user, global admin/superadmin, global manager as property user, per-property, foreign property, create response)
- [x] T002 [US1] `permissions.property_roles`, `PropertyResponse.my_role`, `routers/properties.py`
- [x] T003 [P] [US1] Tests (red): `frontend/src/utils/__tests__/propertyRole.test.ts`
- [x] T004 [US1] `utils/propertyRole.ts`, `utils/usePropertyPerms.ts`; `Meters`, `MeterDetail`, `OilDeliveries`, `Properties`, `Dashboard`
- [x] T005 [P] [US2] Tests (red): `formatInputNumber` in `oilDeliveries.test.ts`; implement and use in `openEdit`
- [x] T006 [US3] `OilDeliveries.vue`: empty text only without error
- [x] T007 [P] [US4] Tests (red): `frontend/src/components/__tests__/OcrCapture.test.ts` (disabled until `ready`, error keeps it disabled)
- [x] T008 [US4] `OcrCapture.vue` gate for crop and serial crop step, i18n `ocr.crop_loading`/`ocr.crop_error`
- [x] T009 [US1-4] E2E: `role-ui` (read-only detail page, per-property role cases, foreign page text), `oil-deliveries` (edit field values), `scan-upload` (no retry workaround, gate test)
- [x] T010 Run backend, frontend (build, vitest, lint:security) and `scripts/e2e.sh`; update `e2e/README.md`

## Notes
Evidence: see the commit message / PR description.
