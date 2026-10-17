# Tasks: Session Hardening (F-007, F-008)

Tests first for every task (red -> green).

## Backend
- [x] T001 [P] [US2] Tests (red): `backend/tests/test_session_hardening.py` - logout revokes, anonymous logout 200, role change revokes, sign-in role change revokes, refused IdP sign-in revokes and keeps row, tokens without `tv` rejected, second device unaffected, events without tokens
- [x] T002 [P] [US1] Tests (red): same file - callback `Location` has fragment code and no JWT, exchange ok/replay/expired/unknown/malformed/inactive, only hash stored, rate limit, failure events without code
- [x] T003 Model `User.token_version`, `LoginCode`; migration `backend/alembic/versions/0007_session_hardening.py` (reversible, tested up/down on throw-away PostgreSQL)
- [x] T004 `app/auth.py`: `tv`/`iat` claims, check in `get_current_user`, `revoke_sessions`, bump in `get_or_create_user_from_oidc`
- [x] T005 `routers/auth.py` (callback, `/exchange`, `/logout`), `routers/admin.py` (bump); adapt `tests/conftest.py::auth_headers`
## Frontend
- [x] T006 [P] Tests (red): `frontend/src/utils/__tests__/authCode.test.ts` for `extractCode`
- [x] T007 `utils/authCode.ts`, `AuthCallback.vue`, `AppHeader.vue`/`stores/auth.ts` logout with token; `npm run build`, `vitest`, `lint:security`
## Integration
- [x] T008 Full backend suite, bandit-style hooks via pre-commit, commit
- [ ] T009 (later) security rig E-A06-003, E-A04-001, grep `token=eyJ`; mark F-007/F-008 fixed in `security/REPORT.md`; release note: users re-login once

## Notes
- Red: `pytest tests/test_session_hardening.py` -> 18 failed, 1 passed (the passing one is the anonymous-logout no-op) after adding only the model columns; vitest `authCode.test.ts` failed on the missing module.
- Green: full backend suite 570 passed; frontend vitest 25 passed, build and `lint:security` 0 errors.
- Existing tests changed: only `tests/conftest.py::auth_headers` (adds the `tv` claim). No test encoded the old redirect or logout behaviour.
- Migration 0007 verified up/down/up on throw-away `postgres:18-trixie`.
