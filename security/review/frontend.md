# Manual review: frontend (T029)

Scope: `frontend/src`, `frontend/index.html`, `frontend/Dockerfile`, `frontend/package.json`, root `nginx.conf` (Compose proxy), CSP in `chart/templates/middleware-security-headers.yaml`. Method: reading + grep for sinks. Status: F-candidates, not yet confirmed by experiment.

## F-candidates

### FE-01 JWT delivered in URL query string (`/auth/token?token=...`)
- OWASP 2025: A07 Authentication Failures (also A09 Logging)
- Component: backend redirect + `frontend/src/views/AuthCallback.vue`
- Severity guess: Medium
- Location: `backend/app/routers/auth.py:154` (redirect), `frontend/src/views/AuthCallback.vue:11-13` (reads `route.query.token`, then `router.replace`)
- Description: after the OIDC callback the 60-minute bearer JWT is placed in the query string of a GET redirect. The SPA consumes it and calls `router.replace('/dashboard')`, but the URL has already been requested from the server.
- Impact: the token lands in the ingress/Traefik/nginx access logs (`GET /auth/token?token=...` is proxied to the static-web-server, which logs it too), in the browser history/session-restore entry until replaced, and in any monitoring that records URLs. Anyone with log access can replay it for up to 60 min (no server-side revocation, `/logout` is a no-op). `Referrer-Policy: same-origin` limits cross-origin leakage, no external resources are loaded so low risk there.
- How to confirm: run the rig, complete a fake-OIDC login, grep the proxy and static-server access log for `token=eyJ`; check the browser history entry.
- Recommendation: deliver the token in the URL fragment (`/auth/token#token=...`, never sent to servers) or, better, an HttpOnly SameSite=Lax cookie / one-time code exchanged via POST. Short term: strip the query from access logs.
- Confidence: high (code path is explicit)

### FE-02 JWT kept in `sessionStorage`, no server-side logout/revocation
- OWASP 2025: A07, A04 (token handling)
- Component: `frontend/src/stores/auth.ts`
- Severity guess: Low (Medium if any XSS is found elsewhere)
- Location: `auth.ts:22,26,52`; `backend/app/routers/auth.py:198-201`
- Description: token readable by any script on the origin; logout only deletes it client-side, the JWT stays valid until `exp` (default 60 min, `config.py`). The router guard (`router/index.ts:132`) is client-side UX only.
- Impact: an XSS or malicious browser extension exfiltrates a usable token; a leaked token (see FE-01) cannot be revoked.
- How to confirm: log in, copy token, call `/api/auth/logout`, reuse token against `/api/auth/me` (expect still 200).
- Recommendation: accepted trade-off for an SPA with strict CSP; document it. Optional: `jti` deny list, shorter lifetime, or cookie session.
- Confidence: high

### FE-03 CSP incomplete / `style-src 'unsafe-inline'`; header set differs between paths
- OWASP 2025: A02 Security Misconfiguration
- Component: `nginx.conf:5-9` (Compose), `chart/templates/middleware-security-headers.yaml:19-23` (k8s)
- Severity guess: Low
- Description: CSP `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' blob:; font-src 'self'; frame-ancestors 'none'`. Missing `object-src 'none'`, `base-uri 'self'`, `form-action 'self'` (these do NOT fall back to `default-src` for base-uri/form-action). `'unsafe-inline'` for styles is probably needed (Vue/ApexCharts/Cropper inline styles). Compose proxy lacks HSTS, Permissions-Policy, COOP/CORP, which the chart sets ("the Kubernetes path must not be weaker than compose" comment holds the wrong way round: k8s is stronger). `X-Frame-Options SAMEORIGIN` (nginx) contradicts `frame-ancestors 'none'` (harmless, CSP wins). `X-XSS-Protection` is deprecated. The frontend container itself (static-web-server) sets no headers, so direct access to port 8080 or any ingress that bypasses the middleware gets none.
- Impact: defence-in-depth only; no script-injection path found (see no-finding list).
- How to confirm: `curl -sI` against the rig proxy and the live ingress, compare headers; run a CSP evaluator.
- Recommendation: add `object-src 'none'; base-uri 'self'; form-action 'self'`, align both configs (one source of truth), drop `X-XSS-Protection`.
- Confidence: high

### FE-04 Hard-coded German error strings, no 401/429 differentiation on login
- OWASP 2025: A09 / A10 (minor), not security-relevant per se
- Component: `frontend/src/views/Login.vue:63,71`
- Severity guess: Info
- Description: login uses raw `fetch`, any non-OK (including 429 from the 5/min limiter) shows "Ungueltige Anmeldedaten". Not an information leak, but users are not told they are rate-limited; also not i18n'd.
- Impact: usability only. Confidence: high. Recommendation: map 429 to a lockout message.

### FE-05 No `.dockerignore` for the frontend build context; floating base images
- Covered under deployment finding DEP-03/DEP-04 (cross-reference). `COPY . .` (`frontend/Dockerfile:6`) after `npm ci` copies a host `node_modules`/`.env*`/`dist` into the build stage if present on the build host; the final image only receives `dist`, so impact is build integrity (stale host `node_modules` overwriting the clean install).

### FE-06 Dependency pinning in `package.json` uses caret ranges; `vitest ^1.6` vs `vite ^8`
- OWASP 2025: A03 Software Supply Chain
- Severity guess: Low
- Location: `frontend/package.json:13-38`
- Description: all deps are `^` ranges. Reproducibility is provided by `package-lock.json` (7002 lines) and `npm ci` in CI and in the Dockerfile, so effective pins exist. Dev tooling majors are far apart (`vitest ^1.6.1`/`@vitest/ui ^1.6.1` with `vite ^8`, `eslint ^10`, `typescript ^6`) which suggests peer-dependency warnings; vitest 1.x is old and has had advisories (dev-only, not shipped). No `npm audit`/`osv` step except the Trivy fs scan (HIGH/CRITICAL, unfixed ignored), which does read `package-lock.json`.
- How to confirm: `npm ci && npm audit --omit=dev` and `npm audit` in `frontend/`; `npm ls vitest vite`.
- Recommendation: add `npm audit --omit=dev --audit-level=high` to CI; bump vitest; consider `save-exact` for runtime deps. Add Dependabot/Renovate (none present in `.github/`).
- Confidence: medium (audit not run in this task)

### FE-07 `err.detail` from API shown to user
- OWASP 2025: A10 Exceptional Conditions
- Location: `components/BulkImport.vue:799` (`throw new Error(err.detail || ...)`), `OcrCapture.vue:331-332,377-380`
- Severity guess: Info
- Description: server `detail` strings are rendered through `{{ }}` interpolation (HTML-escaped), so no XSS. If the backend returns a validation error (FastAPI 422 → `detail` is an array) the UI would show `[object Object]`/raw JSON-ish text; if any backend path puts internal exception text in `detail`, it is shown verbatim. OcrCapture restricts display to 502/503 with string `detail`, which is the right pattern; BulkImport does not.
- How to confirm: rig fault-injection (stop DB/gateway) and look at what BulkImport shows.
- Recommendation: reuse the OcrCapture pattern (whitelist status + string type). Confidence: medium

## Checked, no finding

| Area / CHECKLIST row | Evidence |
|---|---|
| A05 XSS sinks `v-html`, `innerHTML`, `outerHTML`, `insertAdjacent*`, `document.write`, `eval`, `new Function` | `grep -rnE` over `frontend/src` (excluding locales) returns no match for any of them. All dynamic text uses `{{ }}` (escaped). |
| i18n injection | No `v-html`/`<i18n-t>` with user data; user values (`detail.value`, date) are passed as `t()` parameters, not concatenated into message strings (`OcrCapture.vue:380`). Locale files are static. |
| Dynamic URLs / `href` / `:src` with user input | Only `:src` bindings are `blob:` URLs created by `URL.createObjectURL` from local files or fetched blobs (`OcrCapture.vue:54,78,103,223,265,301`, `BulkImport.vue:113,304,324,445`, `MeterDetail.vue:103,305`). No `:href` on user data, no `javascript:` vector. CSP `img-src` allows `blob:`/`data:` only. |
| Image/upload rendering | Uploads are fetched with the bearer header via `apiFetch('/api/uploads/<filename>')` and shown as blob object URLs (`MeterDetail.vue:300-305`); content never executes as HTML (served as `<img>`). Filename is a template string into the fetch path only; path traversal is a backend question (see backend review). Note: object URLs are never revoked (`OcrCapture.vue:223`, ...) - memory only. |
| Open redirect | Frontend never redirects to a user-supplied URL: `window.location.href = '/api/auth/login'` is a constant (`Login.vue:51`... literal); `router.push/replace` targets are literals (`AuthCallback.vue`, `api.ts:78`, `router/index.ts`). No `?next=`/`redirect` query handling. Server redirect target is `settings.app_base_url` (operator config), not request input (`auth.py:153`). The OIDC authorize redirect is built from the discovery document. |
| OIDC state/PKCE (client side) | Flow is server-driven: `/api/auth/login` creates `state` (`token_urlsafe(16)`) + S256 PKCE verifier stored server-side (`auth.py:79-96`), callback requires `state` (`auth.py:102-108`). SPA never touches the code or verifier. Whether state is single-use is an experiment (A07) for the backend. |
| Token storage | `sessionStorage` (tab-scoped, not `localStorage`); `localStorage` only holds `theme` (`stores/theme.ts`). No cookies set client-side (`document.cookie` not used). Residual risk: FE-01/FE-02. |
| 401 handling | `apiFetch` logs out and redirects on 401 (`utils/api.ts:75-79`). Admin gating is also server-side (role checks, backend review). |
| CSP baseline | Present on both delivery paths, `script-src 'self'` (no `unsafe-inline`/`unsafe-eval` for scripts); built Vite output has a single module script, `index.html` has no inline script. |
| Postmessage / window.open / target=_blank | None found. |
| Secrets in bundle | `vite.config.ts` `define` only injects `__APP_VERSION__`; no `import.meta.env` secrets, no keys in `src`. |
| Third-party loads | No CDN scripts/fonts/analytics in `index.html` or `src`; everything `'self'`. |
| Dependencies | Lockfile + `npm ci` (CI `test.yaml:256-258`, `Dockerfile:5`). Runtime deps are a small set (vue, pinia, vue-router, vue-i18n, axios, apexcharts, vue-advanced-cropper). `axios` is listed but `apiFetch` uses `fetch`; confirm if axios is imported at all (unused dependency = needless surface): `grep -rn axios frontend/src`. |
| Static analysis | `eslint-plugin-security` runs in CI and pre-commit (`eslint.config.js`); it is weak for Vue sinks (no `vue/no-v-html` rule) - recommend adding `eslint-plugin-vue` `vue/no-v-html` as a guard (pairs with the grep above). |

## Not verifiable from the repository
- Actual response headers at the live ingress (Traefik middleware attachment, HSTS, whether the static-web-server also adds headers, `/auth/token` caching).
- Whether Traefik/nginx access logs retain query strings (FE-01 impact) and who can read them.
- IdP behavior: Authentik redirect-URI strictness, token lifetime, whether the IdP session is cleared on app logout, consent screens.
- Browser history / extension exposure of the token.
- Content-Type, `Content-Disposition`, `nosniff` headers on `/api/uploads/*` (backend response; check in rig).
- Whether the production build output matches the repo (no sourcemaps published? check `dist/` of the image: `vite build` default has no sourcemaps).
