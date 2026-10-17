# Tasks: Security Review Follow-up (low findings, auth and HTTP)

Tests first (red -> green).

- [x] T001 [US1,US2,US3] Tests (red): `backend/tests/test_oidc_login_hardening.py`
- [x] T002 [US4,US5,US6] Tests (red): `backend/tests/test_health_docs_misc.py`; adapt `test_health.py`, `test_ocr_gateway_endpoint.py`, three `_consume_pkce_state` stubs
- [x] T003 [US2] `auth.py`: sub validation, unique username, e-mail conflict handling
- [x] T004 [US1,US3] `routers/auth.py`: binder cookie, nonce, rate limit, pending cap, IdP error mapping, negative cache
- [x] T005 [US4,US5,US6] `main.py`: docs switch, request id, health split, guarded oil job, log redaction; `services/oil_price.py`
- [x] T006 settings (`config.py`), Compose, chart values and probes, README rows
- [x] T007 `security/REPORT.md` statuses; F-045 documented
- [ ] T008 (not this feature) security rig re-run; frontend footer to `/api/health/details`; k3s-gitops values (`ENABLE_API_DOCS` stays default false)
