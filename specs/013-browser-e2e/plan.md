# Implementation Plan: Browser end-to-end tests

**Branch**: `013-browser-e2e` | **Spec**: [spec.md](spec.md)

## Summary
Playwright (Chromium, `@playwright/test` pinned in `e2e/package.json`) against the existing security rig plus a new HTTPS edge. No application code changes except the router fix found by the suite.

## Decisions
- **Edge**: `security/rig/browser_proxy.py` (Starlette + httpx under uvicorn TLS). Rig `up.sh` gets one switch, `SEC_E2E=1` (wrapper `up-e2e.sh`): picks the proxy port first, points `APP_BASE_URL`/`OIDC_REDIRECT_URI` (and the fake IdP's redirect) at it, trusts loopback proxies, starts the proxy, writes `PROXY_URL` to `rig.env`. The proxy passes redirects through unchanged like Traefik does.
- **Headers**: read from the chart middleware (production) and compared with `nginx.conf` (compose); HSTS from `chart/values.yaml`.
- **Users**: the fake IdP's `claims` mode returns the identity of the seeded users (`rig-sub-<username>`), so SSO sign-ins land on accounts with known property memberships. One Playwright worker (the IdP mode is global).
- **Data**: `e2e/lib/api.ts` creates properties/meters/readings/members as superadmin; dates are relative to today (consumption window 365 d). Reference tank: 8 readings 1374 -> 816 L over 164 days -> ~3.4 L/day, range ~240 days. Probable delivery: 700 -> 1600 L.
- **Guard**: `e2e/lib/guard.ts` is an auto fixture; tests declare expected failures with `guard.expect(status, urlRegex)`.
- **Entry**: `scripts/e2e.sh` (build, up, test, down in a trap). CI: `.github/workflows/e2e.yaml`.
- **Browser fallback**: if `playwright install` is blocked, run the tests in `mcr.microsoft.com/playwright:v1.64.1-noble` (see `e2e/README.md`).

## Constitution Check
Tests first for the router fix (vitest red -> green); no secrets (generated per run, `.state` is git-ignored); loopback only; CI actions pinned by SHA, `contents: read`.
