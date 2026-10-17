# Review checklist (component x OWASP Top 10 2025)

Answer each row: method (read/tool/experiment ID), result (`no finding` or F-###), evidence `path:line`. All rows are answered; full text per cell is in `REPORT.md` section 2, findings in section 3.

## backend
- [x] A01 Broken Access Control: R all routers, E-A01-001..006, E-UP-001. Direct ID access, listings and streaming bulk scan do not leak foreign objects; traversal, symlink and stored-path variants refused (E-A01-004 3/3 pass); mass assignment ignored (E-A01-005 2/3 pass). Result: F-004, F-005, F-006, F-036, F-037, F-045.
- [x] A02 Security Misconfiguration: R config/main/CORS, E-A02-001/002, E-A05-001 enum. CORS correct, `debug` default false, no server banner, security headers via documented ingress (pass). Result: F-009, F-014, F-031.
- [x] A03 Software Supply Chain Failures: T pip-audit, trivy fs/image, R requirements. Runtime pins exact, `python-jose` not in runtime requirements. Result: F-038, F-044, F-049.
- [x] A04 Cryptographic Failures: R auth.py, E-A04-001 (17 tests, 46 passing items). alg=none, wrong key, HS/RS confusion, tamper, expiry, role-claim, bearer-only pass; PKCE S256, 128-bit state, single-use state, fixed redirect URI read. Result: F-034.
- [x] A05 Injection: R grep (no subprocess/eval/pickle/verify=False), T bandit 0 issues, E-A05-001/002. Only raw SQL is `retention.py` with static fragments and bound values (B608 nosec reviewed); SQL payload stored inert and tables intact, stored XSS round trip returns raw JSON, errors never HTML (pass). Result: F-023, F-046.
- [x] A06 Insecure Design: R, E-A06-001..004. First-sign-in race is closed by unique constraint + retry (E-A06-001 passes in the quick profile); state replay/parallel reuse/expiry pass. Result: F-003, F-017, F-019, F-022, F-026, F-033.
- [x] A07 Authentication Failures: R, E-A07-001..004. Constant-time compares, generic error, superadmin disabled without password, PKCE parameters and verifier mismatch pass (E-A07-002 2/2). Result: F-001, F-002, F-007, F-008, F-018.
- [x] A08 Software or Data Integrity Failures: R, E-A08-001, E-UP-001. Server-side image hash (client hash ignored), gateway errors mapped to fixed messages, bulk scan stops after outage, model serial cannot reach foreign meters (pass). Result: F-021, F-029, F-032.
- [x] A09 Logging and Alerting Failures: R grep of logger calls, E-A09-001. httpx loggers pinned to WARNING, gateway key is `SecretStr`, no tokens/passwords logged by application code. Result: F-010, F-027.
- [x] A10 Mishandling of Exceptional Conditions: R, E-A10-001/002, E-A08-001. Fail-closed token decode, health 503 only on DB/scheduler, outbound calls have timeouts, file deleted after commit; gateway down/unauthorized/slow map to 503/502 without blocking (pass). Result: F-011, F-012, F-013, F-020, F-024, F-025, F-028, F-035.

## frontend
- [x] A01 Broken Access Control: R router guard (UX only) + server-side enforcement verified by E-A01-001; 401 handling logs out (`utils/api.ts:75-79`). Admin gating is server side. Result: no finding.
- [x] A02 Security Misconfiguration: R nginx.conf and chart middleware, E-A02-002 security headers (pass). CSP has `script-src 'self'`, no inline script in the built `index.html`. Result: F-041.
- [x] A03 Software Supply Chain Failures: T npm audit (0 production vulnerabilities, 10 dev-only), eslint-plugin-security (0 errors, 5 informational warnings), R lockfile + `npm ci`, no CDN or third-party loads. Result: F-049.
- [x] A04 Cryptographic Failures: R `stores/auth.ts`; `localStorage` only holds the theme, no cookies set client side. Result: F-042.
- [x] A05 Injection: R grep for `v-html`, `innerHTML`, `outerHTML`, `insertAdjacent*`, `document.write`, `eval`, `new Function` (none), E-A05-002 `frontend_has_no_raw_html_sinks` (pass), `:src` only blob URLs, no `:href` on user data, i18n parameters not concatenated. Result: no finding.
- [x] A06 Insecure Design: R: flow is server driven (state/PKCE server side), SPA never handles code or verifier; no client-side business control relied upon (server checks, E-A01-001..006). Result: no finding.
- [x] A07 Authentication Failures: R `AuthCallback.vue`, `Login.vue`, no open redirect (all redirect targets are literals; server redirect target from `app_base_url`). Result: F-007, F-008.
- [x] A08 Software or Data Integrity Failures: R: no dynamic code loading, no `postMessage`/`window.open`, single module script, no secrets in bundle (`vite.config.ts` define only injects the version), no sourcemaps by default. Result: no finding.
- [x] A09 Logging and Alerting Failures: R: no telemetry or third-party hosts in `index.html`/`src`; the token reaching access logs is recorded under A07 (F-008). Result: no finding.
- [x] A10 Mishandling of Exceptional Conditions: R `BulkImport.vue`, `OcrCapture.vue`, `Login.vue`; escaped interpolation of server `detail`. Result: no finding.

## ocr-service
- [x] A01 Broken Access Control: R main.py: stateless, no tenant data, no stored results; network reachability is covered by F-016/F-030. Result: no finding.
- [x] A02 Security Misconfiguration: R, T bandit B104 (bind 0.0.0.0 by design, triaged), trivy; Compose does not publish port 8100, chart pod hardened (non-root, read-only root fs, drop ALL). Result: no finding.
- [x] A03 Software Supply Chain Failures: T pip-audit (pydantic-settings only), trivy image (Python layer 0 HIGH/CRITICAL), R Dockerfile (model download without checksum, F-038). Result: F-038, F-044.
- [x] A04 Cryptographic Failures: R: no cryptography, tokens or secrets handled in `ocr-service/app`; trivy secret scan clean. Result: no finding.
- [x] A05 Injection: R, T bandit (0 issues): no subprocess/eval/pickle; images are magic-byte validated and written to a random per-process tmp dir. Result: no finding.
- [x] A06 Insecure Design: R: stateless request/response design; resource limits set in the chart (2Gi). Result: no finding.
- [x] A07 Authentication Failures: R main.py, README: no authentication by design. Result: F-030.
- [x] A08 Software or Data Integrity Failures: R `pipeline.py` returns `raw_texts`, `detected_*` unfiltered; backend-side handling in F-029, E-A08-001 `ocr_hostile_response_validated`. Result: F-029.
- [x] A09 Logging and Alerting Failures: R grep of `logger` in `ocr-service/app`: two error lines log file path and exception text, no image content, secrets or request bodies. Result: no finding.
- [x] A10 Mishandling of Exceptional Conditions: R, E-A08-001 `ocr_unavailable_maps_to_503` and `ocr_slow_bounded_wait` (pass); no size limit on the service (F-011). Result: F-011.

## images
- [x] A01 Broken Access Control: T trivy image + R Dockerfiles: backend and OCR run as `appuser` (uid 1000, verified in the built image), no docker.sock, no secrets in layers. Result: no finding.
- [x] A02 Security Misconfiguration: R, T trivy config, hadolint; backend/OCR non-root, multi-stage frontend, no `ARG` credentials. Result: F-050.
- [x] A03 Software Supply Chain Failures: T trivy fs/image, hadolint, R Dockerfiles; base images stale locally (OS counts inflated, rescan after `--pull`). Result: F-038, F-044.
- [x] A04 Cryptographic Failures: T trivy secret scan on all three images (0 secrets); no keys or certificates copied in. Result: no finding.
- [x] A05 Injection: T hadolint (no shell-injection patterns), R: no `ADD` of remote URLs, no `curl | sh`. Result: no finding.
- [x] A06 Insecure Design: R: minimal runtime packages, multi-stage build for the frontend (final image has only `dist`). Result: no finding.
- [x] A07 Authentication Failures: R: no default accounts or SSH in any image. Result: F-002.
- [x] A08 Software or Data Integrity Failures: R `build.yaml`, `_helpers.tpl`: images unsigned and tags mutable. Result: F-040.
- [x] A09 Logging and Alerting Failures: R: no logging configuration baked in; HEALTHCHECK absent is in F-050. Result: no finding.
- [x] A10 Mishandling of Exceptional Conditions: R hadolint DL3025: shell-form CMD leaves `sh` as PID 1 and SIGTERM is not forwarded (part of F-038); liveness/readiness probes defined in the chart. Result: no finding.

## chart
- [x] A01 Broken Access Control: R all templates, T helm lint/template + kubeconform: all Services ClusterIP, `/api` -> backend and the rest -> frontend, SA token not automounted (`serviceAccount.yaml:10`), no RBAC created. Result: no finding.
- [x] A02 Security Misconfiguration: R, T helm lint, trivy config, kubeconform strict (8 valid, 0 invalid, 2 CRDs skipped). Hardening positive: runAsNonRoot, seccomp RuntimeDefault, no privilege escalation, read-only root fs, drop ALL on every container incl. postgres. Result: F-016, F-041, F-048.
- [x] A03 Software Supply Chain Failures: T helm lint (0 failed), R: chart has no dependencies; image tags default to `appVersion`. Result: no finding.
- [x] A04 Cryptographic Failures: R: no Secret objects or credentials in templates/values, TLS terminated at Traefik, HSTS set. Result: no finding.
- [x] A05 Injection: R: templates take no user-controlled input; `helm template` renders 10 resources. Result: no finding.
- [x] A06 Insecure Design: R: replicas pinned to 1 (documented), probes present, liveness independent of DB. Result: no finding.
- [x] A07 Authentication Failures: R: no default credentials; secrets only via `existingSecret`. Result: F-030.
- [x] A08 Software or Data Integrity Failures: R `ingressroute.yaml`, `_helpers.tpl`: mutable tags, `IfNotPresent` (F-040). Result: F-040.
- [x] A09 Logging and Alerting Failures: R values: `DEBUG` false by default; no log configuration in the chart. Result: no finding.
- [x] A10 Mishandling of Exceptional Conditions: R: memory requests/limits on all pods, startup/liveness/readiness probes, `emptyDir` for writable paths. Result: F-012.

## compose
- [x] A01 Broken Access Control: R docker-compose.yaml: no privileged mode, host network or docker.sock; db and ocr have no published ports; published backend/Ollama ports are recorded under A02 (F-015). Result: no finding.
- [x] A02 Security Misconfiguration: R, T trivy config; see findings. Result: F-009, F-015, F-041, F-050, F-047.
- [x] A03 Software Supply Chain Failures: R: unpinned `ollama:latest`, `nginx:alpine` (F-038), postgres `18-alpine`. Result: F-038.
- [x] A04 Cryptographic Failures: R: dev stack serves plain HTTP on 8081 behind no TLS (documented as local development); literal secrets recorded in F-009/F-015. Result: no finding.
- [x] A05 Injection: R: no shell interpolation of external input, fixed commands. Result: no finding.
- [x] A06 Insecure Design: R: db has a healthcheck, the other services none (F-050); backend not forced behind the proxy (F-015). Result: no finding.
- [x] A07 Authentication Failures: R: default superadmin credentials are recorded under A02 (F-009). Result: F-002.
- [x] A08 Software or Data Integrity Failures: R: images pulled by moving tags, no verification (F-040). Result: no finding.
- [x] A09 Logging and Alerting Failures: R `nginx.conf`: `X-Request-ID` set for correlation; `DEBUG=true` is in F-015. Result: no finding.
- [x] A10 Mishandling of Exceptional Conditions: R: `restart: unless-stopped` without healthchecks cannot detect hangs (F-050). Result: no finding.

## ci
- [x] A01 Broken Access Control: R workflows: triggers only push/pull_request/tags, no `pull_request_target`, no self-hosted runners, image push and login skipped for PRs, chart push only on `v*` tags. Result: no finding.
- [x] A02 Security Misconfiguration: R: `build.yaml` job permissions scoped to `contents: read, packages: write`; missing `permissions` in `test.yaml` is recorded under A03 (F-039). Result: no finding.
- [x] A03 Software Supply Chain Failures: R, T gitleaks (git history clean), bandit/eslint-security/trivy fs present; gaps in F-039/F-040. Result: F-039, F-049.
- [x] A04 Cryptographic Failures: R: no secrets other than the implicit `GITHUB_TOKEN`; CI test values are literal dummies. Result: no finding.
- [x] A05 Injection: R: no untrusted `${{ github.event.* }}` expression in `run:` steps. Result: no finding.
- [x] A06 Insecure Design: R: tests run before build; branch protection is not visible in the repository. Result: no finding.
- [x] A07 Authentication Failures: R: no credentials handled besides `GITHUB_TOKEN`. Result: no finding.
- [x] A08 Software or Data Integrity Failures: R `build.yaml`, `test.yaml`; see findings. Result: F-040.
- [x] A09 Logging and Alerting Failures: R: coverage artifact contains no secrets; no custom logging of secrets. Result: no finding.
- [x] A10 Mishandling of Exceptional Conditions: R: Trivy `exit-code: 1` fails the build on HIGH/CRITICAL fixable findings. Result: no finding.

## migrations
- [x] A01 Broken Access Control: R alembic/versions 0000-0004: plain DDL, no data-dependent SQL, no grants. Result: no finding.
- [x] A02 Security Misconfiguration: R, E-A05-001 `enum_members_do_not_crash`; DDL rights of the runtime role in F-050. Result: F-014, F-050.
- [x] A03 Software Supply Chain Failures: T trivy/pip-audit: Mako 1.4.1 advisory only affects template rendering of Alembic (informational, F-044). Result: no finding.
- [x] A04 Cryptographic Failures: R: no secrets or keys in migrations or `alembic.ini`; `env.py` reads `DATABASE_URL` from the environment. Result: no finding.
- [x] A05 Injection: R: op API only, no string-built SQL in `versions/`. Result: no finding.
- [x] A06 Insecure Design: R: five linear revisions, `down_revision` chain consistent; concurrent start of several replicas would race (chart pins 1, F-050). Result: no finding.
- [x] A07 Authentication Failures: R: no accounts or credentials created by migrations. Result: no finding.
- [x] A08 Software or Data Integrity Failures: R: no downloads or code loading; 0004 drops a table and columns irreversibly for data (F-050). Result: no finding.
- [x] A09 Logging and Alerting Failures: R: no logging configuration; `alembic.log` only in the rig. Result: no finding.
- [x] A10 Mishandling of Exceptional Conditions: R: a failing migration crash-loops the API at start (F-050). Result: no finding.

## scripts
- [x] A01 Broken Access Control: R scripts/, backend/scripts: dev tooling only, not copied into runtime images (backend/ocr Dockerfiles copy their own directories). Result: no finding.
- [x] A02 Security Misconfiguration: R: hard-coded host/paths in dev scripts (F-043). Result: F-047.
- [x] A03 Software Supply Chain Failures: R: imports are stdlib + httpx/Pillow; not part of any image; dependency scan limited to the requirements files. Result: no finding.
- [x] A04 Cryptographic Failures: R: `ocr_gateway_eval.py` reads the gateway key from the environment and does not print it. Result: no finding.
- [x] A05 Injection: R: `subprocess.run` with list arguments (no `shell=True`), `seed_oil_prices.py` uses bound parameters and fixed data. Result: no finding.
- [x] A06 Insecure Design: R: dev tooling, no business-control logic. Result: no finding.
- [x] A07 Authentication Failures: R: see findings. Result: F-043.
- [x] A08 Software or Data Integrity Failures: R: `seed_oil_prices.py` uses `ON CONFLICT DO NOTHING`, fixed constants; committed `benchmark_results.json` contains no secrets. Result: no finding.
- [x] A09 Logging and Alerting Failures: R, T gitleaks: no secrets in scripts or in `benchmark_results.json`. Result: no finding.
- [x] A10 Mishandling of Exceptional Conditions: R: dev tooling, failure behavior not security relevant. Result: no finding.
