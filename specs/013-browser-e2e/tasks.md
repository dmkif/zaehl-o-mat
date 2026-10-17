# Tasks: Browser end-to-end tests

- [x] T001 `security/rig/browser_proxy.py`: TLS edge, header set from chart + nginx cross-check, API proxy with forwarded headers, SPA fallback
- [x] T002 `security/rig/up.sh` `SEC_E2E=1` + `up-e2e.sh`, `PROXY_URL`/`PUBLIC_URL` in `rig.env`
- [x] T003 `e2e/` package (Playwright 1.64.1), config, `lib/{rig,personas,api,guard,fixtures}.ts`, synthetic JPEG fixture
- [x] T004 Tests: `sso-login`, `logout`, `role-ui`, `dashboard-meter-detail`, `oil-deliveries`, `status-footer`, `scan-upload`, `admin-users`
- [x] T005 Finding: reload of `/admin` bounced admins to the dashboard -> vitest `router/__tests__/guard.test.ts` (red) + fix in `router/index.ts`, `stores/auth.ts`
- [x] T006 `scripts/e2e.sh`, `.github/workflows/e2e.yaml`, README sections
- [x] T007 Proof: red on v0.6.0 backend (redirect bug, no details endpoint), green on main (results in `e2e/README.md`)
- [x] T008 Sanity: `scripts/security-rig.sh --quick --only E_A01_001` passes
