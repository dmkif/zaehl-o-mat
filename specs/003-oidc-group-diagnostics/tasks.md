# Tasks: OIDC Group Diagnostics

Tests first (red -> green). Reconciled with spec 006: the always-on rejection line is the existing `security event=sso_login outcome=denied` (via `security_log.log_event`), extended with the sign-in name as `actor`; the diagnostic lines live in logger `app.oidc_audit`.

## Setup / config
- [x] T001 [P] [US2] Tests (red): `backend/tests/test_oidc_audit.py` - setting default false, `true`/`false` parse, invalid value fails with clear error; start-up warning only when on
- [x] T002 [US2] `config.py`: `oidc_group_diagnostics: bool = False` appended after the last field; `main.py` lifespan WARNING when on; Compose / `chart/values.yaml` / README parity

## US1 rejected sign-in (P1)
- [x] T003 [P] [US1] Tests (red): switch on -> one `oidc.group_diagnostic` line with delivered + expected names; missing/empty/unusable claim -> `oidc.group_claim_missing` (no names); switch off -> zero lines with group names, always-on security event has the user; hostile name (newline, quotes, long) -> one line; >20 names bounded with `omitted=`; no e-mail/sub/token in any line; user label fallbacks (`sub:<hash>`, `unknown`)
- [x] T004 [US1] `app/services/oidc_audit.py` (user label, sanitising, `log_rejected`, `log_accepted`); call sites in `get_or_create_user_from_oidc` (minimal)

## US3 accepted (P3)
- [x] T005 [P] [US3] Tests (red): switch on -> one `oidc.signin_accepted user role groups=N`, no names; switch off -> none
- [x] T006 [US3] `log_accepted` call after role is derived

## Cross-cutting
- [x] T007 Outcome identical with switch on/off (SC-005), full backend suite, bandit via pre-commit
