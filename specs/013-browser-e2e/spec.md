# Feature Specification: Browser end-to-end tests

**Feature Branch**: `013-browser-e2e`

**Created**: 2026-10-17

**Status**: Implemented

**Input**: "a repeatable browser end-to-end test suite that would have caught the production bugs we shipped past the API-level rig".

## Background

The security rig (feature 004) talks to the backend over plain HTTP with a Python client. Two bugs reached production because nothing ran the real SPA behind the real edge:

1. **Trailing-slash redirect to `http://`** (fixed in v0.6.1): the SPA requests `.../readings?limit=200`, FastAPI redirects to `.../readings/?limit=200`; behind Traefik (TLS terminated, `X-Forwarded-Proto: https`) the `Location` was `http://...`, which the page CSP (`connect-src 'self' blob:`) blocks. Result: empty tables, no visible error.
2. **Status footer lights** after the health hardening (v0.7.0): footer data moved from the anonymous `/api/health` to `/api/health/details` (sign-in required); a mismatch between frontend and backend leaves the lights grey/red.

Pod/readiness problems are out of scope (they need a cluster).

## User Scenarios & Testing

### User Story 1 - Production edge in the loop (P1)
The browser talks to an HTTPS origin whose response headers are the production set and whose `/api` requests reach the backend with `X-Forwarded-Proto: https` / `X-Forwarded-For`, SPA routes fall back to `index.html`.

**Acceptance**: with the backend of v0.6.0 the meter-detail test fails on the `http://` redirect; on `main` it passes.

### User Story 2 - Guard rails on every test (P1)
Every test fails on: a CSP violation (console message or `securitypolicyviolation`), any request to `http://` (the fake IdP is the only exception), an unexpected `/api` response >= 400 or network error, an uncaught page error, a token (JWT or `token=`) in any URL.

### User Story 3 - Flows (P1)
SSO sign-in/logout/replay, role dependent UI (admin, manager, plain user, foreign tenant), dashboard + meter detail, oil deliveries (CRUD, consumption tile and range, probable delivery confirm/dismiss), status footer, scan upload through the crop dialog, admin user management.

### User Story 4 - One command, CI (P2)
`scripts/e2e.sh` builds, starts the rig, runs, tears down (trap). A GitHub Actions workflow runs it on pull requests as a separate, initially non-required check.

## Requirements
- **FR-001** `security/rig/browser_proxy.py` reads its security headers from `chart/templates/middleware-security-headers.yaml` and cross-checks them against `nginx.conf`; a disagreement aborts start-up.
- **FR-002** The proxy uses a generated throw-away certificate and binds 127.0.0.1 only; the backend gets `APP_BASE_URL`/`OIDC_REDIRECT_URI` = proxy origin and `TRUSTED_PROXIES=127.0.0.0/8`.
- **FR-003** `up.sh` is unchanged unless `SEC_E2E=1` (existing experiments unaffected).
- **FR-004** Tests build their own data through the API (fresh property per test, deleted afterwards) and use the seeded users/properties read-only; total runtime < 5 minutes.
- **FR-005** Real application bugs found are reported; small fixes carry a regression test.

## Success Criteria
- SC-001 Suite green on `main`, < 5 min. SC-002 Red on v0.6.0 (redirect bug) and on a backend without `/api/health/details`. SC-003 Security rig `--quick --only E_A01_001` still passes.
