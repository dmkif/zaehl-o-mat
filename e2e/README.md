# Browser end-to-end tests (feature 013)

Playwright (Chromium) against the real SPA build and the backend of the security rig, behind an HTTPS edge that
reproduces production: `security/rig/browser_proxy.py` stands in for Traefik (throw-away self-signed certificate,
the security headers read from `chart/templates/middleware-security-headers.yaml` and cross-checked against
`nginx.conf`, `X-Forwarded-Proto: https` / `X-Forwarded-For` on `/api/*`, SPA fallback). The API-level rig
cannot see what happens between the page CSP, the proxy and a redirect; this suite can.

```bash
scripts/e2e.sh                                  # build, rig + proxy up, tests, tear down (about 70 s)
scripts/e2e.sh tests/oil-deliveries.spec.ts -g probable
scripts/e2e.sh --mutant no-forwarded-proto      # proof runs, see below
E2E_SKIP_BUILD=1 E2E_SKIP_INSTALL=1 scripts/e2e.sh   # reuse frontend/dist and node_modules
SEC_BACKEND_DIR=/path/to/older/backend scripts/e2e.sh # test another backend with this SPA
```

Needs podman (postgres), python3, node 22. The script runs `npx playwright install chromium`; if the network
blocks the browser download, run the tests in the official image against a rig started on the host:
`security/rig/up-e2e.sh && podman run --rm --network host -v "$PWD/e2e":/e2e -v "$PWD/security/rig/.state":/security/rig/.state:ro -w /e2e mcr.microsoft.com/playwright:v1.64.1-noble sh -c 'npm ci && npx playwright test'`,
then `security/rig/down.sh` (the path of `rig.env` is relative to the repository: mount the repo at the same place if
the suite is moved). The standard path does not need it.

## What every test checks implicitly (`lib/guard.ts`)

CSP violations (browser console message and `securitypolicyviolation`), requests to `http://` (the fake IdP is the
only exception), `/api` responses >= 400 and network errors unless declared with `guard.expect(status, /url/)`,
uncaught page errors, tokens in any visited URL.

## Tests

| File | Covers |
|---|---|
| `sso-login` | full SSO round trip, no token in URLs/localStorage/cookies, binder cookie cleared, callback URL and login code single use (second tab, replay), user without group refused |
| `logout` | reload keeps the session, logout revokes the token server side, protected pages redirect, revoked session sent to login |
| `role-ui` | admin / manager / plain user: navigation, create/edit/delete buttons, `/admin` guard, foreign tenant |
| `dashboard-meter-detail` | meter cards, readings table, **regression for the http redirect (v0.6.1)**, production headers on the page |
| `oil-deliveries` | tile ~3.4 L/day and range ~240 d (8-reading reference tank), CRUD, probable delivery confirm/dismiss, read-only user |
| `status-footer` | backend/OCR/LLM lights from `/api/health/details`, admin-only version, anonymous health = status only |
| `scan-upload` | upload -> crop -> scan (fake gateway) -> prefilled value -> save, duplicate image |
| `admin-users` | role change persists, user deletion with confirm dialog, superadmin not deletable |

Test data is created per test through the API (`lib/api.ts`, dates relative to today) and deleted afterwards; the
seeded rig users/properties are only read. One worker: the fake IdP's `claims` mode is global.

## Proof that it catches the bugs

Results of 2026-10-17 (Chromium 156, Playwright 1.64.1):

| Backend | Result |
|---|---|
| `main` (v0.7.0 + router fix) | 25 passed, 40 s (69 s with build/rig) |
| tag `v0.6.0` backend, `main` SPA | 24 failed, 1 passed: the `http://` redirect (CSP + insecure request) and the missing `/api/health/details` (404 on every signed-in page) |
| mutant `no-forwarded-proto` (main without `ForwardedProtoMiddleware`) | 10 failed, among them `readings table loads through the trailing-slash redirect`: *CSP violation: Connecting to 'http://127.0.0.1:.../readings/?limit=200' violates ... "connect-src 'self' blob:"* |
| mutant `details-anonymous` (details answers without sign-in) | `status-footer` 2 failed (anonymous 401 expected, lights) |
| mutant `details-admin-only` (plain user gets 403 on details) | 14 failed incl. `status-footer` plain user (grey lights) |

Mutants are `e2e/mutants/*.patch` applied to a temp copy of `backend/`.

## Findings of the suite

* Reloading `/admin` (or opening it via bookmark) bounced admins to the dashboard: the router guard ran before
  `/auth/me` returned the role. Fixed (`router/index.ts`, `stores/auth.ts`), regression test
  `frontend/src/router/__tests__/guard.test.ts`.
* Observations, not fixed: the oil delivery edit form shows litres as `1500.00`; the oil deliveries page shows
  "Noch keine Lieferungen erfasst." next to an error (foreign/unknown property); the add-reading form is shown
  to read-only users (the save is answered with 403); the scan dialog stays on the crop step if "Weiter" is
  clicked before the cropper decoded the image (no feedback).
