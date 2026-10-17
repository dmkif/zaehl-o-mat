# Feature Specification: Low/Info Hygiene (frontend, CI, dependencies, scripts, docs)

**Feature Branch**: `012-low-hygiene`  **Created**: 2026-10-17  **Status**: Implemented
**Input**: fix the open LOW/INFO findings of `security/REPORT.md` outside backend logic: F-039..F-044, F-047..F-049.

## Scope and status of each finding
- F-039: SHA pinning and `permissions` already fixed (004b/009); added `.github/dependabot.yml` (pip, npm, actions, docker, weekly).
- F-040: new CI jobs `image-scan` (trivy image, fixed HIGH/CRITICAL fail), `chart-scan` (helm lint/template, trivy config), `dependency-audit` (pip-audit, npm audit --omit=dev). Existing job names unchanged. Open: digest pinning, cosign, SBOM.
- F-041: CSP gains `object-src 'none'; base-uri 'self'; form-action 'self'`; nginx header set aligned with the chart Middleware (X-Frame-Options DENY, Permissions-Policy, COOP/CORP), X-XSS-Protection dropped. `connect-src 'self' blob:` unchanged.
- F-042: decision: keep `sessionStorage`; logout and 401 already clear it; regression test added; documented.
- F-043: scripts read credentials/targets from env and fail with clear errors; `StrictHostKeyChecking=accept-new`.
- F-044: authlib 1.6.12 (only `authlib.jose` used), pydantic-settings 2.14.2; pip-audit clean for both requirement files.
- F-047: README port 8081, redirect URI `/api/auth/callback`, history routing.
- F-048: `ingress.hsts.{seconds,includeSubdomains,preload}` (defaults unchanged); envFrom/secretVolume documented.
- F-049: unused axios removed; python-jose already only in `requirements-test.txt`; npm audit gate in CI.

## Acceptance
Backend (644) and ocr-service (38) tests pass with the new pins; vitest, `vue-tsc` build, `lint:security` pass; `helm lint`/`template` and kubeconform pass.
