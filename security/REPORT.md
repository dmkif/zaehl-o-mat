# Security Review Report (feature 004)

Status: review complete; high findings fixed in feature 004, medium findings F-004, F-005, F-006, F-009, F-010, F-011 fixed in feature 006 ("004b", `specs/006-security-medium-fixes/`), F-007 and F-008 in feature 007 (session hardening), F-012 and F-013 in feature 008 (bulk-scan limits), F-014, F-015 and F-016 in feature 009 (schema/deploy hardening); all released in 0.6.0 and verified by the rig (`review/rerun-3.md`); low findings F-028 (orphan files) and F-029 are fixed by feature 008. Format: `specs/004-security-review/contracts/report-format.md`, finding fields from `specs/004-security-review/data-model.md`.
Standard: OWASP Top 10:2025 (A01 Broken Access Control, A02 Security Misconfiguration, A03 Software Supply Chain Failures, A04 Cryptographic Failures, A05 Injection, A06 Insecure Design, A07 Authentication Failures, A08 Software or Data Integrity Failures, A09 Logging and Alerting Failures, A10 Mishandling of Exceptional Conditions).
Method: manual reading of every component (`security/review/*.md`), automated tooling (`review/tooling.md`), and 277 attack experiments against a loopback rig with synthetic data (`security/experiments/`, latest run `review/rerun-3.md`). Nothing in this report is derived from a production system; no secrets, tokens, personal data or real images are included. Locations (`path:line`) refer to the tree before the feature-004 fixes and drift as fixes merge.

## 1. Summary

### Counts by severity and status

| Severity | Findings | confirmed by experiment | confirmed by reading | suspected | Planned disposition |
|---|---|---|---|---|---|
| critical | 0 | 0 | 0 | 0 | fix in 004 |
| high | 3 | 3 | 0 | 0 | fix in 004 |
| medium | 13 | 8 | 4 | 1 | all 13 fixed (F-004, F-005, F-006, F-009, F-010, F-011 in 0.5.x; F-007, F-008, F-012, F-013, F-014, F-015, F-016 in 0.6.0) |
| low | 29 | 15 | 13 | 1 | F-029 fixed and F-028 (orphan files) fixed in 0.6.0 (F-028 quota part open); rest proposed (operator decides) |
| informational | 5 | 1 | 4 | 0 | proposed (operator decides) |
| **total** | **50** | 27 | 21 | 2 | |

Lifecycle status values of the data model (`fixed`, `tracked`, `accepted`) are set in section 7 and in the findings table once the operator has decided; all medium findings are `fixed`: F-004, F-005, F-006, F-009, F-010 and F-011 (feature 006, `review/rerun-2.md`) and F-007, F-008, F-012, F-013, F-014, F-015, F-016 (features 007, 008, 009, release 0.6.0, verified by the experiments listed in their sections, `review/rerun-3.md`); of the low findings F-029 is `fixed` and F-028 is `fixed` for the orphan-file part (per-user quota still open); all other low and informational findings stay `proposed`. The state of F-001 to F-003 (feature 004) is tracked in the next table and was not changed by feature 006.

### Critical and high findings (to be fixed in feature 004)

| ID | Title | Fix PR | State |
|---|---|---|---|
| F-001 | OIDC group match is a substring/dict-key test when the IdP claim is not a list | fixed (v0.4.1, PR #12) | closed |
| F-002 | Rate-limit key is client-controlled (X-Forwarded-For trusted from every peer) | fixed (v0.4.1, PR #12) | closed |
| F-003 | Event-loop stall: synchronous DB checkout in async OIDC callback wedges the whole process at about 30 parallel sign-ins | fixed (v0.4.1, PR #12) | closed |

No critical finding was identified. The three high findings are being fixed in parallel; PR links are added here when they merge, and the regression experiments (E-A07-003, E-A07-001, E-A06-001) must pass afterwards.

### Headline results

* 277 experiments in the latest run (profile `--quick`, `review/rerun-3.md`, release 0.6.0 = features 006 to 009): 257 passed, 19 failed (plus the intermittent E-A10-001, which passed in the final run), 1 skipped. Rerun-2 had 206 passed and 40 failed (247 tests), rerun-1 151 passed and 94 failed. Every remaining failure is a real, still open low-severity weakness (no test or rig defect remains; no medium or high finding is open); they collapse into the unfixed findings F-017, F-022, F-023, F-031, F-032, F-033, F-034, F-035, F-026, F-037, F-046.
* The access-control core holds: direct access by ID, listings and the streaming bulk scan never leaked foreign objects, traversal and symlink variants are refused, JWT forgery variants (alg none, wrong key, algorithm confusion, tampering) are rejected, SQL and XSS payloads are stored inert, and the frontend has no raw HTML sinks.
* The weaknesses cluster around: group claim handling and rate-limit key (F-001, F-002), a process-wide stall under parallel logins (F-003), tenant isolation of uploaded images (F-004), role boundaries (F-005, F-006), token lifecycle (F-007, F-008, fixed in 0.6.0), startup validation of secrets (F-009), missing audit logging (F-010) and input/resource limits (F-011, F-012, F-023; F-012 fixed in 0.6.0).
* Tooling found no hard-coded real secrets (gitleaks history clean), no High/Critical SAST findings, no production npm advisories and no exploitable dependency path.

## 2. Coverage matrix

Legend: **R** manual reading, **T** automated tool, **E-...** experiment ID. Result is `no finding` or the finding IDs of that component and category. Every cell was answered; findings recorded under another category are referenced by ID in the cell text.

| Component | A01 Broken Access Control | A02 Security Misconfiguration | A03 Software Supply Chain Failures | A04 Cryptographic Failures | A05 Injection | A06 Insecure Design | A07 Authentication Failures | A08 Software or Data Integrity Failures | A09 Logging and Alerting Failures | A10 Mishandling of Exceptional Conditions |
|---|---|---|---|---|---|---|---|---|---|---|
| backend | R all routers, E-A01-001..006, E-UP-001. Direct ID access, listings and streaming bulk scan do not leak foreign objects; traversal, symlink and stored-path variants refused (E-A01-004 3/3 pass); mass assignment ignored (E-A01-005 2/3 pass). **Result: F-004, F-005, F-006, F-036, F-037, F-045.** | R config/main/CORS, E-A02-001/002, E-A05-001 enum. CORS correct, `debug` default false, no server banner, security headers via documented ingress (pass). **Result: F-009, F-014, F-031.** | T pip-audit, trivy fs/image, R requirements. Runtime pins exact, `python-jose` not in runtime requirements. **Result: F-038, F-044, F-049.** | R auth.py, E-A04-001 (17 tests, 46 passing items). alg=none, wrong key, HS/RS confusion, tamper, expiry, role-claim, bearer-only pass; PKCE S256, 128-bit state, single-use state, fixed redirect URI read. **Result: F-034.** | R grep (no subprocess/eval/pickle/verify=False), T bandit 0 issues, E-A05-001/002. Only raw SQL is `retention.py` with static fragments and bound values (B608 nosec reviewed); SQL payload stored inert and tables intact, stored XSS round trip returns raw JSON, errors never HTML (pass). **Result: F-023, F-046.** | R, E-A06-001..004. First-sign-in race is closed by unique constraint + retry (E-A06-001 passes in the quick profile); state replay/parallel reuse/expiry pass. **Result: F-003, F-017, F-019, F-022, F-026, F-033.** | R, E-A07-001..004. Constant-time compares, generic error, superadmin disabled without password, PKCE parameters and verifier mismatch pass (E-A07-002 2/2). **Result: F-001, F-002, F-007, F-008, F-018.** | R, E-A08-001, E-UP-001. Server-side image hash (client hash ignored), gateway errors mapped to fixed messages, bulk scan stops after outage, model serial cannot reach foreign meters (pass). **Result: F-021, F-029, F-032.** | R grep of logger calls, E-A09-001. httpx loggers pinned to WARNING, gateway key is `SecretStr`, no tokens/passwords logged by application code. **Result: F-010, F-027.** | R, E-A10-001/002, E-A08-001. Fail-closed token decode, health 503 only on DB/scheduler, outbound calls have timeouts, file deleted after commit; gateway down/unauthorized/slow map to 503/502 without blocking (pass). **Result: F-011, F-012, F-013, F-020, F-024, F-025, F-028, F-035.** |
| frontend | R router guard (UX only) + server-side enforcement verified by E-A01-001; 401 handling logs out (`utils/api.ts:75-79`). Admin gating is server side. **Result: no finding.** | R nginx.conf and chart middleware, E-A02-002 security headers (pass). CSP has `script-src 'self'`, no inline script in the built `index.html`. **Result: F-041.** | T npm audit (0 production vulnerabilities, 10 dev-only), eslint-plugin-security (0 errors, 5 informational warnings), R lockfile + `npm ci`, no CDN or third-party loads. **Result: F-049.** | R `stores/auth.ts`; `localStorage` only holds the theme, no cookies set client side. **Result: F-042.** | R grep for `v-html`, `innerHTML`, `outerHTML`, `insertAdjacent*`, `document.write`, `eval`, `new Function` (none), E-A05-002 `frontend_has_no_raw_html_sinks` (pass), `:src` only blob URLs, no `:href` on user data, i18n parameters not concatenated. **Result: no finding.** | R: flow is server driven (state/PKCE server side), SPA never handles code or verifier; no client-side business control relied upon (server checks, E-A01-001..006). **Result: no finding.** | R `AuthCallback.vue`, `Login.vue`, no open redirect (all redirect targets are literals; server redirect target from `app_base_url`). **Result: F-007, F-008.** | R: no dynamic code loading, no `postMessage`/`window.open`, single module script, no secrets in bundle (`vite.config.ts` define only injects the version), no sourcemaps by default. **Result: no finding.** | R: no telemetry or third-party hosts in `index.html`/`src`; the token reaching access logs is recorded under A07 (F-008). **Result: no finding.** | R `BulkImport.vue`, `OcrCapture.vue`, `Login.vue`; escaped interpolation of server `detail`. **Result: no finding.** |
| ocr-service | R main.py: stateless, no tenant data, no stored results; network reachability is covered by F-016/F-030. **Result: no finding.** | R, T bandit B104 (bind 0.0.0.0 by design, triaged), trivy; Compose does not publish port 8100, chart pod hardened (non-root, read-only root fs, drop ALL). **Result: no finding.** | T pip-audit (pydantic-settings only), trivy image (Python layer 0 HIGH/CRITICAL), R Dockerfile (model download without checksum, F-038). **Result: F-038, F-044.** | R: no cryptography, tokens or secrets handled in `ocr-service/app`; trivy secret scan clean. **Result: no finding.** | R, T bandit (0 issues): no subprocess/eval/pickle; images are magic-byte validated and written to a random per-process tmp dir. **Result: no finding.** | R: stateless request/response design; resource limits set in the chart (2Gi). **Result: no finding.** | R main.py, README: no authentication by design. **Result: F-030.** | R `pipeline.py` returns `raw_texts`, `detected_*` unfiltered; backend-side handling in F-029, E-A08-001 `ocr_hostile_response_validated`. **Result: F-029.** | R grep of `logger` in `ocr-service/app`: two error lines log file path and exception text, no image content, secrets or request bodies. **Result: no finding.** | R, E-A08-001 `ocr_unavailable_maps_to_503` and `ocr_slow_bounded_wait` (pass); no size limit on the service (F-011). **Result: F-011.** |
| images | T trivy image + R Dockerfiles: backend and OCR run as `appuser` (uid 1000, verified in the built image), no docker.sock, no secrets in layers. **Result: no finding.** | R, T trivy config, hadolint; backend/OCR non-root, multi-stage frontend, no `ARG` credentials. **Result: F-050.** | T trivy fs/image, hadolint, R Dockerfiles; base images stale locally (OS counts inflated, rescan after `--pull`). **Result: F-038, F-044.** | T trivy secret scan on all three images (0 secrets); no keys or certificates copied in. **Result: no finding.** | T hadolint (no shell-injection patterns), R: no `ADD` of remote URLs, no `curl / sh`. **Result: no finding.** | R: minimal runtime packages, multi-stage build for the frontend (final image has only `dist`). **Result: no finding.** | R: no default accounts or SSH in any image. **Result: F-002.** | R `build.yaml`, `_helpers.tpl`: images unsigned and tags mutable. **Result: F-040.** | R: no logging configuration baked in; HEALTHCHECK absent is in F-050. **Result: no finding.** | R hadolint DL3025: shell-form CMD leaves `sh` as PID 1 and SIGTERM is not forwarded (part of F-038); liveness/readiness probes defined in the chart. **Result: no finding.** |
| chart | R all templates, T helm lint/template + kubeconform: all Services ClusterIP, `/api` -> backend and the rest -> frontend, SA token not automounted (`serviceAccount.yaml:10`), no RBAC created. **Result: no finding.** | R, T helm lint, trivy config, kubeconform strict (8 valid, 0 invalid, 2 CRDs skipped). Hardening positive: runAsNonRoot, seccomp RuntimeDefault, no privilege escalation, read-only root fs, drop ALL on every container incl. postgres. **Result: F-016, F-041, F-048.** | T helm lint (0 failed), R: chart has no dependencies; image tags default to `appVersion`. **Result: no finding.** | R: no Secret objects or credentials in templates/values, TLS terminated at Traefik, HSTS set. **Result: no finding.** | R: templates take no user-controlled input; `helm template` renders 10 resources. **Result: no finding.** | R: replicas pinned to 1 (documented), probes present, liveness independent of DB. **Result: no finding.** | R: no default credentials; secrets only via `existingSecret`. **Result: F-030.** | R `ingressroute.yaml`, `_helpers.tpl`: mutable tags, `IfNotPresent` (F-040). **Result: F-040.** | R values: `DEBUG` false by default; no log configuration in the chart. **Result: no finding.** | R: memory requests/limits on all pods, startup/liveness/readiness probes, `emptyDir` for writable paths. **Result: F-012.** |
| compose | R docker-compose.yaml: no privileged mode, host network or docker.sock; db and ocr have no published ports; published backend/Ollama ports are recorded under A02 (F-015). **Result: no finding.** | R, T trivy config; see findings. **Result: F-009, F-015, F-041, F-050, F-047.** | R: unpinned `ollama:latest`, `nginx:alpine` (F-038), postgres `18-alpine`. **Result: F-038.** | R: dev stack serves plain HTTP on 8081 behind no TLS (documented as local development); literal secrets recorded in F-009/F-015. **Result: no finding.** | R: no shell interpolation of external input, fixed commands. **Result: no finding.** | R: db has a healthcheck, the other services none (F-050); backend not forced behind the proxy (F-015). **Result: no finding.** | R: default superadmin credentials are recorded under A02 (F-009). **Result: F-002.** | R: images pulled by moving tags, no verification (F-040). **Result: no finding.** | R `nginx.conf`: `X-Request-ID` set for correlation; `DEBUG=true` is in F-015. **Result: no finding.** | R: `restart: unless-stopped` without healthchecks cannot detect hangs (F-050). **Result: no finding.** |
| ci | R workflows: triggers only push/pull_request/tags, no `pull_request_target`, no self-hosted runners, image push and login skipped for PRs, chart push only on `v*` tags. **Result: no finding.** | R: `build.yaml` job permissions scoped to `contents: read, packages: write`; missing `permissions` in `test.yaml` is recorded under A03 (F-039). **Result: no finding.** | R, T gitleaks (git history clean), bandit/eslint-security/trivy fs present; gaps in F-039/F-040. **Result: F-039, F-049.** | R: no secrets other than the implicit `GITHUB_TOKEN`; CI test values are literal dummies. **Result: no finding.** | R: no untrusted `${{ github.event.* }}` expression in `run:` steps. **Result: no finding.** | R: tests run before build; branch protection is not visible in the repository. **Result: no finding.** | R: no credentials handled besides `GITHUB_TOKEN`. **Result: no finding.** | R `build.yaml`, `test.yaml`; see findings. **Result: F-040.** | R: coverage artifact contains no secrets; no custom logging of secrets. **Result: no finding.** | R: Trivy `exit-code: 1` fails the build on HIGH/CRITICAL fixable findings. **Result: no finding.** |
| migrations | R alembic/versions 0000-0004: plain DDL, no data-dependent SQL, no grants. **Result: no finding.** | R, E-A05-001 `enum_members_do_not_crash`; DDL rights of the runtime role in F-050. **Result: F-014, F-050.** | T trivy/pip-audit: Mako 1.4.1 advisory only affects template rendering of Alembic (informational, F-044). **Result: no finding.** | R: no secrets or keys in migrations or `alembic.ini`; `env.py` reads `DATABASE_URL` from the environment. **Result: no finding.** | R: op API only, no string-built SQL in `versions/`. **Result: no finding.** | R: five linear revisions, `down_revision` chain consistent; concurrent start of several replicas would race (chart pins 1, F-050). **Result: no finding.** | R: no accounts or credentials created by migrations. **Result: no finding.** | R: no downloads or code loading; 0004 drops a table and columns irreversibly for data (F-050). **Result: no finding.** | R: no logging configuration; `alembic.log` only in the rig. **Result: no finding.** | R: a failing migration crash-loops the API at start (F-050). **Result: no finding.** |
| scripts | R scripts/, backend/scripts: dev tooling only, not copied into runtime images (backend/ocr Dockerfiles copy their own directories). **Result: no finding.** | R: hard-coded host/paths in dev scripts (F-043). **Result: F-047.** | R: imports are stdlib + httpx/Pillow; not part of any image; dependency scan limited to the requirements files. **Result: no finding.** | R: `ocr_gateway_eval.py` reads the gateway key from the environment and does not print it. **Result: no finding.** | R: `subprocess.run` with list arguments (no `shell=True`), `seed_oil_prices.py` uses bound parameters and fixed data. **Result: no finding.** | R: dev tooling, no business-control logic. **Result: no finding.** | R: see findings. **Result: F-043.** | R: `seed_oil_prices.py` uses `ON CONFLICT DO NOTHING`, fixed constants; committed `benchmark_results.json` contains no secrets. **Result: no finding.** | R, T gitleaks: no secrets in scripts or in `benchmark_results.json`. **Result: no finding.** | R: dev tooling, failure behavior not security relevant. **Result: no finding.** |

Component scope: `backend` = `backend/app`, `backend/scripts`, Alembic use at runtime; `images` = the three Dockerfiles and the built images; `chart` = Helm chart; `compose` = `docker-compose.yaml` and `nginx.conf`; `ci` = `.github/workflows`, pre-commit; `migrations` = `backend/alembic`; `scripts` = `scripts/` and `backend/scripts/`.
A03 experiments do not exist (supply chain is verified by tools, section 5); A03 cells therefore cite tools and reading.

## 3. Findings

Ordered by severity, then ID. Planned disposition: `fix in 004` for critical/high, `proposed` for medium/low/informational (the operator decides which to fix).

| ID | Severity | OWASP | Component | Title | Status | Planned disposition |
|---|---|---|---|---|---|---|
| F-001 | high | A07 | backend | OIDC group match is a substring/dict-key test when the IdP claim is not a list | confirmed by experiment E-A07-003 | fixed (004, v0.4.1) |
| F-002 | high | A07 | backend, images, compose | Rate-limit key is client-controlled (X-Forwarded-For trusted from every peer) | confirmed by experiment E-A07-001 | fixed (004, v0.4.1) |
| F-003 | high | A06 | backend | Event-loop stall: synchronous DB checkout in async OIDC callback wedges the whole process at about 30 parallel sign-ins | confirmed by experiment (ad-hoc measurement in rerun-1; regression E-A06-001 full profile) | fixed (004, v0.4.1) |
| F-004 | medium | A01 | backend | Client supplied `image_path` is trusted: cross-tenant file reference, foreign OCR, delete and serve-check bypass | confirmed by experiment E-A01-002 / E-UP-001 | fixed (spec 006) |
| F-005 | medium | A01 | backend | Property role `manager` can manage membership and promote itself to property admin | confirmed by experiment E-A01-003 | fixed (spec 006) |
| F-006 | medium | A01 | backend | Global admin can demote the superadmin; break-glass role is never restored | confirmed by experiment E-A01-003 | fixed (spec 006) |
| F-007 | medium | A07 | backend, frontend | No token revocation, logout is a no-op, `exp` not required, stale privileges after IdP removal | confirmed by experiment E-A06-003 / E-A04-001 | fixed (feature 007, 0.6.0) |
| F-008 | medium | A07 | backend, frontend | Session JWT is delivered in the URL query string (`/auth/token?token=...`) | confirmed by reading | fixed (feature 007, 0.6.0) |
| F-009 | medium | A02 | backend, compose | Weak JWT secret and weak or half-configured superadmin credentials are accepted at startup | confirmed by experiment E-A02-001 | fixed (spec 006) |
| F-010 | medium | A09 | backend | No security event logging; callback code and state appear in the access log | confirmed by experiment E-A09-001 | fixed (spec 006) |
| F-011 | medium | A10 | backend, ocr-service | Image decompression and 4x upscale memory amplification; no pixel cap | confirmed by experiment E-UP-001 (acceptance); resource effect not measured | fixed (spec 006) |
| F-012 | medium | A10 | backend, chart | Bulk scan reads all uploaded files into memory before any size check; no body limit on the Kubernetes path | confirmed by reading | fixed (feature 008, 0.6.0) |
| F-013 | medium | A10 | backend | DB connection pinned for the whole streamed LLM run in bulk scan | suspected, verified fixed by E-A10-004 | fixed (feature 008, 0.6.0) |
| F-014 | medium | A02 | backend, migrations | Enum drift between SQLAlchemy models and the initial migration breaks meter creation on a migration-built database | confirmed by experiment E-A05-001 | fixed in feature 009 (migration 0008, 0.6.0), verified by E-A02-003 |
| F-015 | medium | A02 | compose | Docker Compose ships weak defaults and publishes the backend and Ollama on all interfaces | confirmed by reading | fixed in feature 009 (0.6.0) |
| F-016 | medium | A02 | chart | NetworkPolicy off by default; when on, ingress source defaults to every pod in the cluster; no egress policy | confirmed by reading | fixed in feature 009 (chart, 0.6.0) |
| F-017 | low | A06 | backend | OIDC login CSRF: state is not bound to the initiating browser | confirmed by experiment E-A06-004 | fixed (010) |
| F-018 | low | A07 | backend | Missing or empty `sub` claim matches the superadmin row (`oidc_sub IS NULL`) | confirmed by reading | fixed (010) |
| F-019 | low | A06 | backend | Username/e-mail collisions give 500 on login or silent account confusion | confirmed by experiment (side effect in run 1; dedicated experiment pending) | fixed (010) |
| F-020 | low | A10 | backend | `compare_digest` on non-ASCII strings raises TypeError (500 on superadmin login) | confirmed by experiment E-A07-004 | proposed |
| F-021 | low | A08 | backend | Client-chosen `source` lets the retention job downsample and delete readings and image links | confirmed by reading | proposed |
| F-022 | low | A06 | backend | Duplicate-image check is a check-then-insert race without a unique constraint | confirmed by experiment E-A06-002 | proposed |
| F-023 | low | A05 | backend | Weak validation of numeric, string and structured input gives 500s and can poison data | confirmed by experiment E-A05-001 / E-A02-002 / E-A01-006 | proposed |
| F-024 | low | A10 | backend | `delete_user` probably fails with 500 for users with property links | suspected | proposed |
| F-025 | low | A10 | backend | Startup depends on an external fetch and unguarded oil-price DB writes | confirmed by reading | fixed (010) |
| F-026 | low | A06 | backend | Unauthenticated `/auth/login` writes to the database per call; IdP outage gives 500 and no negative cache | confirmed by experiment E-A10-002 | fixed (010) |
| F-027 | low | A09 | backend | Oil-price API key can be written to the log | confirmed by reading | fixed (010) |
| F-028 | low | A10 | backend | Any authenticated user can consume LLM/OCR resources; failed scans leave orphan files | confirmed by experiment E-A10-002 / E-A08-001 | partly fixed (feature 008, 0.6.0: orphan files) |
| F-029 | low | A08 | backend, ocr-service | LLM and OCR-service output is not validated or bounded; malformed OCR JSON gives 500; EXIF date is free text | confirmed by experiment E-A08-001 / E-UP-001 | fixed (feature 008, 0.6.0) |
| F-030 | low | A07 | ocr-service, chart | OCR service has no authentication and relies on network isolation | confirmed by reading | proposed |
| F-031 | low | A02 | backend | API documentation and verbose health endpoint are public | confirmed by experiment E-A02-002 | fixed (010) |
| F-032 | low | A08 | backend | Scan stores undecoded, truncated or broken images | confirmed by experiment E-UP-001 | proposed |
| F-033 | low | A06 | backend | EXIF GPS data is retained in stored and served images | confirmed by experiment E-UP-001 | proposed |
| F-034 | low | A04 | backend | JWT parsing robustness: non-string `alg` header or `sub` gives 500 | confirmed by experiment E-A04-001 | proposed |
| F-035 | low | A10 | backend | Database outage mid-request: unhandled 500, acknowledgement lost after commit | confirmed by experiment E-A10-001 (intermittent) | proposed |
| F-036 | low | A01 | backend | Unattached scan uploads are readable by every authenticated user | confirmed by experiment E-A01-002 | proposed |
| F-037 | low | A01 | backend | `replaced_by_id` may reference another tenant's meter; `integration_config` stored in plaintext | confirmed by experiment E-A01-005 | proposed |
| F-038 | low | A03 | images, compose, backend, ocr-service | Container build hygiene: floating tags, no hash-pinned dependencies, no .dockerignore, shell-form CMD, EasyOCR models without checksum | confirmed by reading | proposed |
| F-039 | low | A03 | ci | GitHub Actions pinned to mutable tags; `test.yaml` has no `permissions:` block; no Dependabot/Renovate | confirmed by reading | proposed |
| F-040 | low | A08 | ci, images, chart | Built images are mutable and unsigned; no SBOM/provenance; CI does not scan images or chart misconfiguration | confirmed by reading | proposed |
| F-041 | low | A02 | frontend, compose, chart | CSP lacks `object-src`/`base-uri`/`form-action`; header sets differ between Compose proxy and chart; static server sets none | confirmed by reading | proposed |
| F-042 | low | A04 | frontend | JWT kept in `sessionStorage` | confirmed by reading | proposed |
| F-043 | low | A07 | scripts | Operational scripts contain hard-coded default credentials, internal addresses and disabled SSH host-key checking | confirmed by reading | proposed |
| F-044 | low | A03 | backend, ocr-service, images | Dependency advisories without a reachable path: authlib 1.6.9, pydantic-settings 2.13.1, Mako, pip | confirmed by reading | proposed |
| F-050 | low | A02 | images, compose, migrations | Frontend image runs as root outside the chart; images have no HEALTHCHECK; migrations run at container start with the application DB role | confirmed by reading | proposed |
| F-045 | informational | A01 | backend | 404-before-403 ordering leaks existence of properties, meters and readings | confirmed by reading | proposed |
| F-046 | informational | A05 | backend | `X-Request-ID` is reflected unvalidated and unbounded; client-derived strings reach log lines | confirmed by experiment E-A02-002 | fixed (010) |
| F-047 | informational | A02 | compose, scripts | Documentation does not match the implementation (ports, redirect URI, routing mode, default login) | confirmed by reading | proposed |
| F-048 | informational | A02 | chart | Chart: secrets injected as env via `envFrom`; HSTS preload and includeSubdomains set unconditionally | confirmed by reading | proposed |
| F-049 | informational | A03 | frontend, ci, backend | Frontend and test-tooling hygiene: dev-only npm advisories, unused axios, python-jose in test requirements, error detail rendering | confirmed by reading | proposed |

### F-001 OIDC group match is a substring/dict-key test when the IdP claim is not a list

* **id**: F-001
* **category**: A07 (Authentication Failures)
* **component**: backend
* **severity (final)**: high. High: full privilege escalation to global admin, no precondition beyond an IdP account whose claim has a string shape; likelihood depends on the IdP mapping (not verifiable here), hence not critical.
* **status**: confirmed by experiment E-A07-003
* **location**: backend/app/auth.py:39-48
* **description**: `settings.oidc_admin_group in groups` is evaluated on whatever the IdP sends. For a string claim `in` is a substring test, for a dict it tests keys, for null/int/bool/float it raises TypeError. No type check, no normalisation.
* **impact / likelihood**: A user whose single-valued groups claim merely contains a group name (prefix/suffix/space- or comma-separated list/JSON-encoded list/dict key even with a false value) becomes global admin, manager or user without membership. Non-iterable claim types give a 500.
* **reproduction**: E-A07-003 (36 parameter sets, 17 fail, 19 were masked by rig defects in run 1; rerun-1: 17 fail).
* **evidence**: 13 string/dict shapes grant a role (admin for the admin-name variants) and 4 non-iterable types give HTTP 500; exact lists and rejected shapes behave correctly. Mutant `group-substring` documents the unpatched behaviour.
* **recommendation**: Require `isinstance(groups, list)` with only `str` elements, exact case-sensitive comparison, 403 for every other type (or split a string on one documented separator); add negative regression tests (the experiment is the test).
* **planned disposition**: fix in 004
* **source**: backend B-01; triage-group2

### F-002 Rate-limit key is client-controlled (X-Forwarded-For trusted from every peer)

* **id**: F-002
* **category**: A07 (Authentication Failures)
* **component**: backend, images, compose
* **severity (final)**: high. High: removes the only brute-force protection of a full-access local account; the effective exposure on Kubernetes depends on Traefik XFF handling (not verifiable) but the backend itself trusts every peer.
* **status**: confirmed by experiment E-A07-001
* **location**: backend/Dockerfile:25-26, backend/app/limiter.py:4, backend/app/routers/auth.py (superadmin-login 5/minute), nginx.conf:23,40
* **description**: uvicorn runs with `--proxy-headers --forwarded-allow-ips='*'`, so the left-most (client supplied) X-Forwarded-For entry becomes `request.client.host`; slowapi keys on it. nginx and Traefik append to a client supplied header. Counters are in memory per process (N replicas give N x limit).
* **impact / likelihood**: Unthrottled online brute force of the only local password (break-glass superadmin login) and of the cost-bearing OCR endpoints (20/min and 4/min limits never apply).
* **reproduction**: E-A07-001 `limit_not_bypassable_by_forwarded_for`; verified by a direct uvicorn 0.42 check that `get_trusted_client_host('1.2.3.4, 10.0.0.1')` returns `1.2.3.4`.
* **evidence**: 40 requests with 40 distinct forged X-Forwarded-For values: 40 evaluated 401 responses, no 429 (limit is 5/minute). Control without the header is limited.
* **recommendation**: Restrict `--forwarded-allow-ips` to the proxy / ingress CIDR, make the proxy overwrite XFF (`$remote_addr`), add an IP-independent per-account back-off for superadmin-login (DB counter), shared limiter store if more than one replica is ever supported.
* **planned disposition**: fix in 004
* **source**: backend B-02, deployment DEP-02, tooling TL-1

### F-003 Event-loop stall: synchronous DB checkout in async OIDC callback wedges the whole process at about 30 parallel sign-ins

* **id**: F-003
* **category**: A06 (Insecure Design)
* **component**: backend
* **severity (final)**: high. High: authenticated (IdP account) but unprivileged actor causes full, self-sustaining denial of service with a few dozen requests; no data loss.
* **status**: confirmed by experiment (ad-hoc measurement in rerun-1; regression E-A06-001 full profile)
* **location**: backend/app/routers/auth.py:149 -> backend/app/auth.py:56, backend/app/database.py (pool 10 + 20)
* **description**: `callback` is `async def` but does synchronous SQLAlchemy calls on the event loop. When more than pool size + overflow (30) callbacks run in parallel, the loop thread blocks in `QueuePool` checkout while the 30 holders need the loop to finish and release their connections.
* **impact / likelihood**: Any account that can sign in at the IdP (one valid code per request) can freeze the API including `/api/health/live` until the process is restarted. Health probes then also fail, so the pod is eventually restarted by liveness, but the outage is trivially repeatable.
* **reproduction**: Ad-hoc N-parallel-callbacks client against one rig identity (not a pytest case); full-profile E-A06-001 (100 sign-ins) must be re-run after the fix.
* **evidence**: N=30: all 307 in 3.3 s; N=32: 31 of 32 time out (60 s limit); N=40: 36/40; N=60: 57/60; N=100: 97/100. During the stall `/api/health/live` hangs (>120 s), `pg_stat_activity` shows exactly 30 connections `idle in transaction`, SIGUSR1 thread dump shows the loop thread in `QueuePool._do_get -> queue.get`. No recovery within 6 minutes; no 500 or pool timeout logged meanwhile.
* **recommendation**: Make `login`/`callback` (and other DB-using async handlers) plain `def` or run the DB part with `run_in_threadpool`; set a small `pool_timeout` so overload gives fast 503; concurrency limit on `/auth/callback`.
* **planned disposition**: fix in 004
* **source**: rerun-1 B-32; triage-group2 root cause 3

### F-004 Client supplied `image_path` is trusted: cross-tenant file reference, foreign OCR, delete and serve-check bypass

* **id**: F-004
* **category**: A01 (Broken Access Control)
* **component**: backend
* **severity (final)**: medium. Medium: breaks tenant isolation of images and allows destroying another tenant's evidence, but requires knowing an unguessable name.
* **status**: confirmed by experiment E-A01-002 / E-UP-001
* **location**: backend/app/routers/readings.py:62-94,120-124; backend/app/routers/uploads.py:37-55; backend/app/routers/ocr.py:382-390; backend/app/schemas/readings.py:134-135
* **description**: Reading create/patch stores `image_path`/`serial_image_path` verbatim (no ownership, no shape check). `serve_upload` decides on the first matching reading (`.first()`, no ORDER BY). `hint-rescan` OCRs any stored file by name after checking access only on the meter. `delete_reading` unlinks by `Path(...).name` even if another reading references the file.
* **impact / likelihood**: A tenant who learns another tenant's upload file name (UUID; visible to current and former members in reading listings) can read the image through its own reading, have it OCRed (value returned), adopt it, and destroy it by deleting its own reading. Needs the unguessable file name, so not trivially exploitable.
* **reproduction**: E-A01-002 (`cross_tenant_access_by_id_denied` x3 params, `image_filename_guessing_and_reference_attack`, `hint_rescan_does_not_process_foreign_images`), E-UP-001 `reading_cannot_adopt_foreign_upload`. Mutant `access-check-removed`.
* **evidence**: 6 + 1 tests fail in rerun-1; direct access by ID, listings and the streaming bulk scan did not leak foreign objects (pass).
* **recommendation**: Record uploader/property per scan (table or sidecar), validate `image_path` against `^uploads/<uuid>.<ext>$`, require unattached and owned by caller, serve only if the user may access all referencing readings, never unlink a file still referenced, bind hint-rescan to files of the meter's property.
* **planned disposition**: fixed (spec 006)
* **fix**: fixed in feature 006 (new table `upload_files` plus `app/upload_registry.py`; `image_path` must match `uploads/<uuid>.<ext>` and be a registered upload of the caller or the meter's property; file only deleted when no other reading references it; legacy readings keep working). Verified by E-A01-002 (`cross_tenant_access_by_id_denied` x5, `image_filename_guessing_and_reference_attack`, `hint_rescan_does_not_process_foreign_images`, `unattached_scan_upload_not_readable_by_other_user`) and E-UP-001 `reading_cannot_adopt_foreign_upload`, all passing in rerun-2. Side effect: the experiment of F-036 (unattached upload readable) passes too; F-036 stays `proposed` until the operator closes it.
* **source**: backend B-07; triage-group1; triage-group3

### F-005 Property role `manager` can manage membership and promote itself to property admin

* **id**: F-005
* **category**: A01 (Broken Access Control)
* **component**: backend
* **severity (final)**: medium. Medium: needs an existing manager membership (granted by an admin) and stays inside one property.
* **status**: confirmed by experiment E-A01-003
* **location**: backend/app/routers/properties.py:65-78,105-151; backend/app/permissions.py:23-27
* **description**: `add_property_user`, `remove_property_user` and `update_property` only require level `write` (admin + manager). `PropertyUserAdd.role` may be `admin`, the existing-member branch overwrites roles.
* **impact / likelihood**: Vertical escalation inside a property: manager becomes admin, can lock out the real admin and gains delete rights on meters and readings.
* **reproduction**: E-A01-003 `property_manager_cannot_grant_or_escalate_roles`.
* **evidence**: Self-promotion via POST returned success; role matrix for the other property levels passes.
* **recommendation**: Membership management and property PATCH require property-admin (`delete` level) or global admin; forbid removing or demoting the last property admin and changing one's own role.
* **planned disposition**: fixed (spec 006)
* **fix**: fixed in feature 006 (property PATCH and membership add/change/remove need property admin or global admin; last property admin cannot be removed or demoted; no self role change; manager keeps meter/reading write). Verified by E-A01-003 `property_manager_cannot_grant_or_escalate_roles` and `role_matrix_property_levels` (expectation updated: manager gets 403 on property PATCH and on membership changes; unit tests `backend/tests/test_role_escalation.py`), passing in rerun-2.
* **source**: backend B-05; triage-group1

### F-006 Global admin can demote the superadmin; break-glass role is never restored

* **id**: F-006
* **category**: A01 (Broken Access Control)
* **component**: backend
* **severity (final)**: medium. Medium: requires an admin account; impact is loss of the break-glass path, not data exposure.
* **status**: confirmed by experiment E-A01-003
* **location**: backend/app/routers/admin.py:184-201 (set_user_role); backend/app/routers/auth.py:726-738 (superadmin_login)
* **description**: `set_user_role` only blocks granting superadmin, not changing an existing superadmin as target. `superadmin_login` logs in the row without re-asserting the role. Roles set here are overwritten by the IdP group at the next OIDC login.
* **impact / likelihood**: A compromised admin neutralises the emergency account until database surgery; also admin-to-admin demotions and self changes are possible.
* **reproduction**: E-A01-003 `admin_cannot_touch_superadmin` (also a rig hazard: it left the seed superadmin demoted until the autouse restore fixture was added).
* **evidence**: Admin got HTTP 200 when demoting the superadmin; granting superadmin and deleting the superadmin by an admin are refused (pass).
* **recommendation**: Reject any change or deletion whose target is superadmin unless the caller is superadmin, restore the role on superadmin login, forbid demoting the last superadmin, decide and document whether OIDC user roles come from the IdP only.
* **planned disposition**: fixed (spec 006)
* **fix**: fixed in feature 006 (only a superadmin may change or delete the superadmin; last superadmin protected; role re-asserted on `superadmin-login`). Verified by E-A01-003 `admin_cannot_touch_superadmin`, passing in rerun-2.
* **source**: backend B-06; triage-group1; triage-group2 root cause 2

### F-007 No token revocation, logout is a no-op, `exp` not required, stale privileges after IdP removal

* **id**: F-007
* **category**: A07 (Authentication Failures)
* **component**: backend, frontend
* **severity (final)**: medium. Medium: household-scale, 60 minute window, no remote trigger; combines with F-008.
* **status**: confirmed by experiment E-A06-003 / E-A04-001
* **location**: backend/app/auth.py:19-32,44-62,90-111; backend/app/routers/auth.py:752-756
* **description**: The JWT carries only sub/role/exp (no iat/jti/iss/aud), there is no deny list or session table, `POST /auth/logout` does nothing, `claims.validate()` does not require `exp` and accepts `exp=Infinity`. A user removed from all IdP groups gets 403 at next login but the row keeps `is_active=True`, role and property links and the old token stays valid up to 60 minutes. No API sets `is_active=false`.
* **impact / likelihood**: A leaked token (see F-008) cannot be revoked; off-boarding is delayed up to the token lifetime; with a leaked signing key tokens without expiry can be forged.
* **reproduction**: E-A06-003 `privilege_not_kept_after_idp_removal`; E-A04-001 `token_without_expiry_rejected`, `malformed_exp_rejected[inf]`.
* **evidence**: Second sign-in after group removal is refused but old token and DB role remain; token without `exp` and with infinite `exp` are accepted (HTTP 200). Role claim in the token is ignored (pass).
* **recommendation**: Require `exp` (`claims_options`), reject non-finite and absurd lifetimes, add `iat`/`jti` plus per-user `token_valid_after`, make logout bump it, deactivate or demote on group rejection, add an admin endpoint for `is_active`, consider shorter lifetime with refresh rotation.
* **planned disposition**: fixed (feature 007, 0.6.0)
* **fix**: fixed in feature 007 (release 0.6.0): users.token_version counter; every JWT carries `tv` and `iat` and `get_current_user` rejects a token whose `tv` differs from the database (missing, stale, future and wrong-typed `tv` all give 401); `POST /auth/logout` bumps the counter; role change by an admin, role change at sign-in and removal of all permitted IdP groups bump it too. Verified by E-A07-006 (`logout_revokes_token`, `logout_does_not_touch_other_users`), E-A07-007 (`admin_role_change_revokes_sessions`, `role_change_at_signin_revokes_older_token`, `idp_group_removal_revokes_sessions`), E-A06-003 (`privilege_not_kept_after_idp_removal`, now passing), E-A04-001 (`token_version_claim_enforced` x5, `token_without_expiry_rejected`, `malformed_exp_rejected`). Not done: `is_active` admin endpoint and refresh rotation (the lifetime stays 60 minutes).
* **source**: backend B-04, frontend FE-02, triage-group1/2

### F-008 Session JWT is delivered in the URL query string (`/auth/token?token=...`)

* **id**: F-008
* **category**: A07 (Authentication Failures)
* **component**: backend, frontend
* **severity (final)**: medium. Medium: exposure requires log or history access, window limited to 60 minutes, same-origin Referrer-Policy limits leakage.
* **status**: confirmed by reading
* **location**: backend/app/routers/auth.py:705-708 (RedirectResponse); frontend/src/views/AuthCallback.vue:11-13
* **description**: After the OIDC callback the 60 minute bearer token is placed in the query of a GET redirect to the SPA host. The SPA consumes it and replaces the URL, but the request has already been logged.
* **impact / likelihood**: Token appears in Traefik/nginx/static-server access logs, browser history and any URL recording; anyone with log read access can replay it until expiry (no revocation, F-007).
* **reproduction**: Reading only. E-A09-001 covers the sibling exposure (callback `code`/`state` in the access log); a rig grep for `token=eyJ` after a login would confirm it.
* **evidence**: Code path is explicit; E-A09-001 fails because the access log carries the callback URL.
* **recommendation**: Deliver the token in the URL fragment or via an HttpOnly Secure SameSite=Lax cookie / one-time code exchanged by POST; until then strip query strings from ingress and static-server access logs.
* **planned disposition**: fixed (feature 007, 0.6.0)
* **fix**: fixed in feature 007 (release 0.6.0): the callback redirects to `<app>/auth/token#code=<one-time code>`; the code (60 s, only its SHA-256 stored in `login_codes`) is exchanged by `POST /auth/exchange` for the JWT; the SPA reads the fragment, which is never sent to a server. Verified by E-A07-005 (`redirect_carries_code_not_jwt`: no `eyJ`/`token=` in the Location header; `code_is_single_use`, `expired_and_unknown_codes_refused`, `parallel_replay_exactly_one_winner`: 15 parallel exchanges, exactly one 200) and E-A09-001 (no JWT or login code in the backend log).
* **source**: backend B-03, frontend FE-01

### F-009 Weak JWT secret and weak or half-configured superadmin credentials are accepted at startup

* **id**: F-009
* **category**: A02 (Security Misconfiguration)
* **component**: backend, compose
* **severity (final)**: medium. Medium: strong secrets are likely used in the real deployment (not verifiable), but the guard is the last line of defence.
* **status**: confirmed by experiment E-A02-001
* **location**: backend/app/config.py:93-100,215-224; docker-compose.yaml:57-59; README default login
* **description**: Validation rejects only the literal sentinel `change-me-in-production`. Any JWT key (empty, `x`, `secret`, 15 chars, the Compose value) and any superadmin password (`admin123`, `password`, `x`) passes; `SUPERADMIN_PASSWORD` without `SUPERADMIN_USER` starts. The shipped Compose file and README use known values.
* **impact / likelihood**: Offline brute force of a short HS256 key from one captured token, or a trivially guessable full-access login on any deployment that copies the Compose file.
* **reproduction**: E-A02-001 `weak_jwt_secret_refuses_start` (11 params), `weak_superadmin_password_refuses_start` (7 params), `superadmin_user_without_password_or_vice_versa`.
* **evidence**: 19 of 24 A02 failures; the sentinel checks, default DB URL and gateway-key checks pass.
* **recommendation**: Require JWT key >= 32 bytes and not on a denylist, superadmin password minimum length plus denylist, both or neither of user/password, refuse the Compose literals; remove real-looking defaults from Compose or require variables with `${VAR:?}`.
* **planned disposition**: fixed (spec 006)
* **fix**: fixed in feature 006 (startup validation: JWT secret >= 32 characters and not on a deny-list, superadmin password >= 12 characters and not on a deny-list, user and password both or neither; Compose and README no longer ship working weak values). Verified by E-A02-001 (`weak_jwt_secret_refuses_start` x11, `weak_superadmin_password_refuses_start` x7, `superadmin_user_without_password_or_vice_versa`, control and sentinel checks), passing in rerun-2. Production secrets must satisfy the rules before release (spec 006 edge case).
* **source**: backend B-12, deployment DEP-01, triage-group1

### F-010 No security event logging; callback code and state appear in the access log

* **id**: F-010
* **category**: A09 (Logging and Alerting Failures)
* **component**: backend
* **severity (final)**: medium. Medium: no direct compromise, but removes detection and forensics for the High findings.
* **status**: confirmed by experiment E-A09-001
* **location**: backend/app/auth.py, backend/app/routers/auth.py, admin.py, properties.py, meters.py, readings.py, uploads.py (no `logger`)
* **description**: OIDC success/failure and reason, superadmin login success/failure, 401/403 decisions, role changes, user deletion, membership changes and deletions are not logged. uvicorn's access line contains `/api/auth/callback?code=...&state=...`.
* **impact / likelihood**: Brute force (F-002) and role abuse (F-005, F-006) leave no trace; incidents are not attributable.
* **reproduction**: E-A09-001 `no_secrets_in_logs_and_security_events_present`.
* **evidence**: Test fails: no application-level event for failed superadmin login or rejected sign-in; code/state visible in the access log. Application code does not log tokens, passwords or keys (positive).
* **recommendation**: Structured security-event logger (event, actor id, target id, source IP, outcome; never tokens/codes/passwords), log every denied auth decision, redact the callback query in access logs.
* **planned disposition**: fixed (spec 006)
* **fix**: fixed in feature 006 (structured security events via `app/security_log.py`: event, outcome, actor, target, ip; callback query string removed from the access log). Verified by E-A09-001 `no_secrets_in_logs_and_security_events_present`, passing in rerun-2.
* **source**: backend B-11; triage-group2

### F-011 Image decompression and 4x upscale memory amplification; no pixel cap

* **id**: F-011
* **category**: A10 (Mishandling of Exceptional Conditions)
* **component**: backend, ocr-service
* **severity (final)**: medium. Medium: authenticated DoS of an internal service; chart has memory limits that turn it into an OOM-kill and restart.
* **status**: confirmed by experiment E-UP-001 (acceptance); resource effect not measured
* **location**: backend/app/services/llm_client.py:108-112; ocr-service/app/pipeline.py:121-123; backend/app/routers/ocr.py:265-266,286-288
* **description**: Only magic bytes are checked. PNG 20000x20000 (389 KB), 9500x9500, GIF 65535x65535 (35 bytes) and BMP 40000x40000 are accepted and stored; `Image.MAX_IMAGE_PIXELS` is never set. With `already_cropped=true` (client field) the OCR service denoises and upscales the whole image 16x in pixels. The OCR service has no body limit.
* **impact / likelihood**: One authenticated user (no property needed) can OOM or CPU-starve the backend or OCR pod.
* **reproduction**: E-UP-001 `decompression_bomb_and_huge_dimensions` (single and bulk path).
* **evidence**: All four bombs returned 200 and were stored; backend stayed responsive only because the stand-in gateway ignores the image.
* **recommendation**: Header-only size check before writing (reject > 40 MP or side > 12000), set `Image.MAX_IMAGE_PIXELS` and treat the warning as error, cap the upscaled size, memory limits in the chart, size limit in the OCR service.
* **planned disposition**: fixed (spec 006)
* **fix**: fixed in feature 006 (header-only pixel/side check before anything is stored, default 40 MP / 12000 px, `Image.MAX_IMAGE_PIXELS` set; OCR service enforces the same limit and caps the upscale). Verified by E-UP-001 `decompression_bomb_and_huge_dimensions` (single and bulk path), passing in rerun-2; the memory effect on a pod was not measured.
* **source**: backend B-10; triage-group3

### F-012 Bulk scan reads all uploaded files into memory before any size check; no body limit on the Kubernetes path

* **id**: F-012
* **category**: A10 (Mishandling of Exceptional Conditions)
* **component**: backend, chart
* **severity (final)**: medium. Medium: authenticated; behind nginx the 15 MB cap makes it low, in the chart path it is open.
* **status**: confirmed by reading
* **location**: backend/app/routers/ocr.py:486-487,519; chart/templates/ingressroute.yaml
* **description**: `await f.read()` for up to 50 files with no bound; the size check happens later in the generator. nginx caps the body at 15 MB, the Traefik route in the chart has no buffering/body-size middleware.
* **impact / likelihood**: Memory exhaustion / OOM kill of the backend pod by any authenticated user (4 requests/minute, spoofable limit F-002).
* **reproduction**: Reading only; E-UP-001 `size_limit_enforced` (single scan, mutant `size-check-removed`) passes and confirms the single-file path is bounded.
* **evidence**: Single scan is bounded with `read(MAX+1)`; bulk path is not.
* **recommendation**: Traefik `buffering` middleware with `maxRequestBodyBytes`, `Content-Length` check, bounded per-file read with early reject, process one file at a time.
* **planned disposition**: fixed (feature 008, 0.6.0)
* **fix**: fixed in feature 008 (release 0.6.0): bulk scan reads in chunks against a cumulative budget (`MAX_BULK_TOTAL_BYTES`, default 50 MiB, 413) and file count (`MAX_BULK_FILES`, 400), a route-level ASGI limit refuses a declared or streamed body over budget plus 1 MiB with 413 before the form is parsed, and the chart adds a Traefik buffering middleware. Verified by E-A10-003 `bulk_scan_limits` (over budget 413 without stored file or OCR/gateway call, too many files 400, declared Content-Length 413 from the headers alone, chunked body 413, control request streams) and E-UP-001 `size_limit_enforced`. The rig exercise found that the streamed (chunked) case answered 400 "error parsing the body" instead of 413; fixed in `backend/app/body_limit.py` with the regression test `backend/tests/test_bulk_scan_limits.py::test_chunked_body_over_route_limit_is_413`. The Traefik middleware is verified by reading/`helm template` only.
* **source**: backend B-08

### F-013 DB connection pinned for the whole streamed LLM run in bulk scan

* **id**: F-013
* **category**: A10 (Mishandling of Exceptional Conditions)
* **component**: backend
* **severity (final)**: medium. Medium (provisional): authenticated, requires many slow runs; re-rate after the experiment.
* **status**: suspected
* **location**: backend/app/routers/ocr.py:436-444,492-663; backend/app/database.py:353
* **description**: `generate()` uses the request-scoped session for up to 50 images x several LLM calls with a 300 s read timeout; the transaction keeps one pooled connection checked out for minutes (exact FastAPI dependency lifecycle not verified).
* **impact / likelihood**: About 30 parallel bulk scans exhaust the pool and the API answers `QueuePool` errors; amplifies F-003.
* **reproduction**: Not yet run: needs a gateway stand-in with 120 s latency and 35 parallel bulk scans (planned in the experiment priorities).
* **evidence**: -
* **recommendation**: Do DB work up front, close or commit before streaming, open short sessions inside the generator, cap concurrent bulk jobs per user and globally.
* **planned disposition**: fixed (feature 008, 0.6.0)
* **fix**: fixed in feature 008 (release 0.6.0): the request session is released before streaming and every step of the generator opens its own short session. Verified by the new experiment E-A10-004 (`bulk_scan_holds_no_db_transaction_while_gateway_is_slow`: with the gateway answering after 9 s no `idle in transaction` session of the application role exists) and by `backend/tests/test_bulk_scan_limits.py::test_no_db_session_open_while_llm_runs`. Status upgraded from suspected to confirmed fixed.
* **source**: backend B-09

### F-014 Enum drift between SQLAlchemy models and the initial migration breaks meter creation on a migration-built database

* **id**: F-014
* **category**: A02 (Security Misconfiguration)
* **component**: backend, migrations
* **severity (final)**: medium. Medium for fresh installs (core function unavailable), none for an already working database.
* **status**: confirmed by experiment E-A05-001
* **location**: backend/app/models.py (Enum(MeterUnit), Enum(IntegrationType)); backend/alembic/versions/0000_initial_schema.py:67,72
* **description**: Models persist enum names (`m3`, `kwh`, `liter`; `smart_meter`, `shelly`), the migration creates labels `kWh, m3, L, MWh, GJ, other` and `manual, homeassistant, modbus, api`.
* **impact / likelihood**: On a database built by `alembic upgrade head`, `POST /meters` with unit `kWh`/`L` or integration type `smart_meter` returns 500 (`invalid input value for enum`). Functional/robustness issue, not exploitable; production may have been created by `create_all`.
* **reproduction**: E-A05-001 `enum_members_do_not_crash`.
* **evidence**: 500 with `invalid input value for enum meterunit: "kwh"` / `integrationtype: "smart_meter"` on the rig database (migration built).
* **recommendation**: Check how production was created, then add a migration aligning the enum types with the models (or change the models), add a test on PostgreSQL built by Alembic.
* **planned disposition**: fixed in feature 009 (0.6.0)
* **fix**: fixed in feature 009 (migration 0008, release 0.6.0). Verified on the migration-built rig database by E-A02-003 (all 9 meter type x unit pairs including kWh and L are created and take a reading; all 4 integration types including `smart_meter` are stored and read back) and E-A05-001 `enum_members_do_not_crash` (now passing).
* **source**: rerun-1 new candidate; backend.md Alembic note

### F-015 Docker Compose ships weak defaults and publishes the backend and Ollama on all interfaces

* **id**: F-015
* **category**: A02 (Security Misconfiguration)
* **component**: compose
* **severity (final)**: medium. Medium: development stack by intent; high if copied to a shared host.
* **status**: confirmed by reading
* **location**: docker-compose.yaml:6-8,18,21,52,57-59,63,88
* **description**: Backend `8000` (bypasses the proxy) and unauthenticated Ollama `11434` are published on 0.0.0.0; `DEBUG=true`, known JWT secret, `admin/admin123`, DB password literal, `ollama:latest`; no `read_only`, `cap_drop`, `no-new-privileges`, resource limits, no healthchecks.
* **impact / likelihood**: Anyone on the LAN can use the GPU/model API and reach the backend directly; anyone who follows the README can obtain a superadmin token or forge JWTs.
* **reproduction**: Reading; tooling TL-6 and trivy misconfig; consequences of the secrets are experiment-confirmed in F-009.
* **evidence**: Documented as development stack, README documents `admin123`.
* **recommendation**: Bind to `127.0.0.1:`, drop `ports` for ollama/backend, require variables via `.env` (`${VAR:?}`), `DEBUG` false, pin image tags, add hardening options and healthchecks.
* **planned disposition**: fixed in feature 009 (0.6.0)
* **fix**: fixed in feature 009 (release 0.6.0): Compose binds to loopback, no published Ollama/backend ports, required variables via `${VAR:?}`, hardening options and healthchecks. Verified by reading and `docker compose config` only; no rig experiment exists for Compose.
* **source**: deployment DEP-01, tooling TL-6/section 9

### F-016 NetworkPolicy off by default; when on, ingress source defaults to every pod in the cluster; no egress policy

* **id**: F-016
* **category**: A02 (Security Misconfiguration)
* **component**: chart
* **severity (final)**: medium. Medium: depends on the GitOps values (not verifiable); defence in depth for several lower findings.
* **status**: confirmed by reading
* **location**: chart/values.yaml (networkPolicy.enabled: false), chart/templates/networkpolicy.yaml:35-42,58-65, chart/templates/statefulset-postgresql.yaml
* **description**: Default disabled; with `ingressFrom` left at `{}`/`{}` the policy admits every pod of every namespace; no Egress policies for backend, OCR (processes untrusted images) or PostgreSQL; the unauthenticated OCR service (F-030) relies only on this policy.
* **impact / likelihood**: Lateral movement and exfiltration from a compromised pod are unrestricted.
* **reproduction**: Reading and `helm template`; live enforcement not verifiable from the repository.
* **evidence**: Postgres policy is correct (backend only); chart pods are hardened (non-root, read-only root fs, drop ALL).
* **recommendation**: Make `ingressFrom` required, add egress rules (DNS, postgres, gateway, OIDC), enable by default when `ocr.enabled`; verify enforcement on the cluster.
* **planned disposition**: fixed in feature 009 (0.6.0)
* **fix**: fixed in feature 009 (chart, release 0.6.0): NetworkPolicy with safe defaults and egress rules. Verified by reading and `helm template` only; live enforcement is not verifiable from the repository. Reduces the exposure of F-030 but does not add authentication to the OCR service.
* **source**: deployment DEP-06, DEP-10

### F-017 OIDC login CSRF: state is not bound to the initiating browser

* **id**: F-017
* **category**: A06 (Insecure Design)
* **component**: backend
* **severity (final)**: low. Low: small impact for this app, requires social engineering.
* **status**: confirmed by experiment E-A06-004
* **location**: backend/app/routers/auth.py:629-650,653-708
* **description**: State and PKCE verifier are stored server side keyed by `state` only; no cookie or nonce ties the callback to the browser that called `/login`. Identity comes from userinfo, no `nonce` check.
* **impact / likelihood**: An attacker can hand a victim a callback link and the victim's browser receives the attacker's session (data mixing).
* **reproduction**: E-A06-004 `state_bound_to_initiating_browser` (the test was a false PASS in run 1 and fails after the rig fix).
* **evidence**: Browser B completes browser A's login. State replay, parallel reuse, expiry and forged state are rejected (pass).
* **recommendation**: Short-lived HttpOnly cookie with random value stored (hashed) with the state row and required at callback; atomic consume with `DELETE ... RETURNING`.
* **planned disposition**: fixed
* **fix**: fixed in feature 010 (`/auth/login` sets a short-lived HttpOnly SameSite=Lax cookie `oidc_binder`; only its SHA-256 is stored with the state; callback consumes the state once and rejects a missing or foreign cookie with 400; an `nonce` derived from the state is sent and checked when an id_token is processed). Verified by `backend/tests/test_oidc_login_hardening.py` (`test_callback_without_binder_rejected`, `test_callback_with_foreign_binder_rejected`, `test_id_token_nonce_mismatch_rejected`).
* **source**: backend B-13

### F-018 Missing or empty `sub` claim matches the superadmin row (`oidc_sub IS NULL`)

* **id**: F-018
* **category**: A07 (Authentication Failures)
* **component**: backend
* **severity (final)**: low. Low: requires a misbehaving IdP.
* **status**: confirmed by reading
* **location**: backend/app/auth.py:36,56
* **description**: `sub` is never validated; `User.oidc_sub == None` becomes `IS NULL` and matches the break-glass row, whose role and e-mail are then rewritten.
* **impact / likelihood**: A spec-violating or malicious IdP overwrites the superadmin account and logs in as it.
* **reproduction**: Reading only (needs a fake userinfo without `sub`).
* **evidence**: -
* **recommendation**: Require non-empty string `sub` (<= 255) else 502; never let OIDC login touch rows with NULL `oidc_sub`.
* **planned disposition**: fixed
* **fix**: fixed in feature 010 (missing, empty, non-string or over-long `sub` gives 502 before any lookup; OIDC login never reads `oidc_sub IS NULL` rows). Verified by `test_bad_sub_rejected_and_superadmin_untouched`, `test_callback_without_sub_is_502_not_500`.
* **source**: backend B-14

### F-019 Username/e-mail collisions give 500 on login or silent account confusion

* **id**: F-019
* **category**: A06 (Insecure Design)
* **component**: backend
* **severity (final)**: low. Low: availability of single accounts only.
* **status**: confirmed by experiment (side effect in run 1; dedicated experiment pending)
* **location**: backend/app/auth.py:56-87; backend/app/routers/auth.py:726-737
* **description**: `username` and `email` are UNIQUE; the retry path re-reads by `sub` and re-raises. Existing-user path overwrites e-mail without collision handling. `superadmin_login` selects by username, so an OIDC user named like `SUPERADMIN_USER` collides in both directions; first superadmin login has no race handling.
* **impact / likelihood**: Login denial for legitimate users, targeted DoS if usernames are user-editable at the IdP, confusing identity at the break-glass account.
* **reproduction**: Run 1 backend log: 1207 `users_username_key` violations from identical `preferred_username` of different identities (masked tests until the rig used unique names).
* **evidence**: HTTP 500 for every second identity with the same preferred username.
* **recommendation**: Derive a unique local username on collision, do not key on e-mail, mark the superadmin by a dedicated column, catch IntegrityError on all paths; add a dedicated B-15 experiment.
* **planned disposition**: fixed
* **fix**: fixed in feature 010 (colliding username gets a deterministic suffix from the `sub` hash, never merges with an existing or the superadmin account; taken e-mail on first login is 409, on later login the old e-mail is kept; empty e-mail gets a placeholder; IntegrityError handled on all paths). Verified by `test_same_username_different_identity_gets_unique_name`, `test_username_like_superadmin_never_merges`, `test_email_collision_new_identity_is_409_no_merge`, `test_email_change_to_taken_address_keeps_old_email`. Not done: dedicated superadmin column (the break-glass row is still found by username).
* **source**: backend B-15; triage-group2 root cause 1

### F-020 `compare_digest` on non-ASCII strings raises TypeError (500 on superadmin login)

* **id**: F-020
* **category**: A10 (Mishandling of Exceptional Conditions)
* **component**: backend
* **severity (final)**: low. Low: robustness only.
* **status**: confirmed by experiment E-A07-004
* **location**: backend/app/routers/auth.py:721-722 (168 in the checked-out tree)
* **description**: `secrets.compare_digest(str, str)` raises on non-ASCII input; fields have no length bound. The unhandled error also drops the keep-alive connection.
* **impact / likelihood**: 500 and stack trace in the log instead of 401; not a bypass.
* **reproduction**: E-A07-004 `nonascii_and_malformed_credentials_no_5xx`.
* **evidence**: 500 for the non-ASCII body; remaining bodies (100 kB password, null, wrong types) not yet evaluated after the connection drop.
* **recommendation**: Compare `.encode()` bytes or HMAC digests, bound the field lengths.
* **planned disposition**: proposed
* **source**: backend B-16

### F-021 Client-chosen `source` lets the retention job downsample and delete readings and image links

* **id**: F-021
* **category**: A08 (Software or Data Integrity Failures)
* **component**: backend
* **severity (final)**: low. Low: needs write access, but data loss.
* **status**: confirmed by reading
* **location**: backend/app/schemas/readings.py:133; backend/app/routers/readings.py:342; backend/app/jobs/retention.py:107-135
* **description**: Any manager/admin may create readings with `source` `auto` or `archived`; the nightly job averages them per bucket, deletes the originals and drops `image_path`, `note`, `image_hash`.
* **impact / likelihood**: Silent loss or alteration of billing-relevant readings and evidence images, trivially triggered by mistake.
* **reproduction**: Reading only.
* **evidence**: -
* **recommendation**: Force `source=manual` for API-created readings, advisory lock for the job, keep evidence fields when merging.
* **planned disposition**: proposed
* **source**: backend B-17

### F-022 Duplicate-image check is a check-then-insert race without a unique constraint

* **id**: F-022
* **category**: A06 (Insecure Design)
* **component**: backend
* **severity (final)**: low. Low: integrity of manual data, no security boundary.
* **status**: confirmed by experiment E-A06-002
* **location**: backend/app/routers/readings.py:324-351; backend/app/models.py (Reading.image_hash index only)
* **description**: Concurrent POSTs with the same image both pass the duplicate lookup and insert.
* **impact / likelihood**: Double entry of the same photo, defeats the business control.
* **reproduction**: E-A06-002 `parallel_same_image_hash` (2 params). The `parallel_identical_reading` variant (20 identical rows) reflects the absence of any uniqueness rule for readings without image: design decision, not counted separately.
* **evidence**: 3 to 6 responses 201 for one hash per round; fails fast after 5 rounds.
* **recommendation**: Partial unique index on `image_hash` where not null (per meter), map IntegrityError to 409.
* **planned disposition**: proposed
* **source**: backend B-18

### F-023 Weak validation of numeric, string and structured input gives 500s and can poison data

* **id**: F-023
* **category**: A05 (Injection)
* **component**: backend
* **severity (final)**: low. Low: availability/robustness of single requests, authenticated.
* **status**: confirmed by experiment E-A05-001 / E-A02-002 / E-A01-006
* **location**: backend/app/schemas/*.py, routers/meters.py:228-229, routers/properties.py:74-75, routers/readings.py:41,51, routers/auth.py:106, routers/ocr.py:386-388
* **description**: Unbounded `Decimal` (overflow, NaN/Infinity), `null` on NOT NULL columns, `integration_config: Any` (dict -> psycopg cannot adapt), strings without `max_length`, NUL bytes (property name, callback `state`, hint-rescan path), huge OFFSET, lone UTF-16 surrogate in a body (FastAPI 422 serialisation fails), no global exception handler.
* **impact / likelihood**: HTTP 500 instead of 4xx for many inputs; a stored NaN could break float arithmetic in the dashboard for the whole meter. No injection or data exposure found.
* **reproduction**: E-A05-001 `json_body_fields_all_endpoints`, `query_and_path_parameters`, `multipart_form_fields`; E-A02-002 `authenticated_error_bodies_contain_no_internals`; E-A01-006 `stored_integration_config_never_triggers_requests`.
* **evidence**: 5 of 10 probes in E-A02-002 give 500 (1e100000, 400-digit value, 100 000-char names, NUL in name); NUL in callback state and 40-digit offset give 500; no stack trace reaches the client.
* **recommendation**: `Field(max_length)`, `condecimal(allow_inf_nan=False, max_digits=12, decimal_places=3)`, reject explicit null for required fields, type `integration_config`, `le=` bounds on offset/limit, reject NUL globally, catch `DataError`/`DBAPIError` -> 4xx/503, generic 500 handler.
* **planned disposition**: proposed
* **source**: backend B-19; triage-group1; rerun-1

### F-024 `delete_user` probably fails with 500 for users with property links

* **id**: F-024
* **category**: A10 (Mishandling of Exceptional Conditions)
* **component**: backend
* **severity (final)**: low. Low: admin-only function.
* **status**: suspected
* **location**: backend/app/routers/admin.py:204-216; backend/app/models.py (User.property_users without cascade/passive_deletes)
* **description**: ORM tries to null `property_users.user_id` (NOT NULL) instead of relying on the DB-level `ON DELETE CASCADE`.
* **impact / likelihood**: Off-boarding by deletion fails for assigned users.
* **reproduction**: Not yet run (needs PostgreSQL, a user with an assignment).
* **evidence**: -
* **recommendation**: `passive_deletes=True` or `cascade='all, delete-orphan'`, add a PostgreSQL test.
* **planned disposition**: proposed
* **source**: backend B-20

### F-025 Startup depends on an external fetch and unguarded oil-price DB writes

* **id**: F-025
* **category**: A10 (Mishandling of Exceptional Conditions)
* **component**: backend
* **severity (final)**: low. Low: operator-configured source.
* **status**: confirmed by reading
* **location**: backend/app/main.py:417-428; backend/app/services/oil_price.py:37,42-58
* **description**: `await _run_oil_price()` runs inside `lifespan` (up to 15 s); commit errors are not caught, so two replicas or a hostile `custom` source (`1e30`, `nan`) crash start-up.
* **impact / likelihood**: Crash loop / delayed readiness.
* **reproduction**: Reading only.
* **evidence**: -
* **recommendation**: try/except + rollback, upsert with ON CONFLICT, range check, run as background task.
* **planned disposition**: fixed
* **fix**: fixed in feature 010 (start-up runs the oil-price job as a background task, `_run_oil_price` never raises, DB errors are rolled back and logged, non-finite or implausible prices are ignored). Verified by `test_run_oil_price_never_raises`, `test_startup_survives_oil_price_failure`, `test_db_error_in_oil_price_is_guarded`, `test_absurd_price_not_stored`. Not done: ON CONFLICT upsert (a lost race is caught and logged instead).
* **source**: backend B-21

### F-026 Unauthenticated `/auth/login` writes to the database per call; IdP outage gives 500 and no negative cache

* **id**: F-026
* **category**: A06 (Insecure Design)
* **component**: backend
* **severity (final)**: low. Low.
* **status**: confirmed by experiment E-A10-002
* **location**: backend/app/routers/auth.py:53-55,124,629-637
* **description**: Each anonymous `GET /api/auth/login` deletes expired states and inserts a row; discovery errors (`HTTPStatusError`, token exchange `ReadTimeout`) are unmapped (500) and not cached.
* **impact / likelihood**: Cheap anonymous DB write amplification and 10 s outbound requests per call when the IdP is down.
* **reproduction**: E-A10-002 `idp_down_at_login_with_cold_discovery_cache`, `idp_unavailable_at_token_exchange[idp_slow]`.
* **evidence**: Login with IdP down and cold cache returns 500; slow IdP (17 s) at token exchange returns 500.
* **recommendation**: Rate limit `/login` and `/callback`, cap pending states, map `httpx.HTTPError` to 502/503/504, 30-60 s negative cache.
* **planned disposition**: fixed
* **fix**: fixed in feature 010 (`/auth/login` and `/auth/callback` rate limited by `OIDC_RATE_LIMIT`, pending states capped by `OIDC_MAX_PENDING_STATES` with purge of expired rows, httpx errors mapped to 502/503/504, IdP calls bounded by `OIDC_HTTP_TIMEOUT_S` with connect <= 3 s, 30 s negative cache for discovery, stale discovery served when refresh fails). Verified by `test_login_rate_limited`, `test_pending_states_capped`, `test_discovery_failure_negative_cached`, `test_callback_idp_errors_mapped`.
* **source**: backend B-22; triage-group2

### F-027 Oil-price API key can be written to the log

* **id**: F-027
* **category**: A09 (Logging and Alerting Failures)
* **component**: backend
* **severity (final)**: low. Low.
* **status**: confirmed by reading
* **location**: backend/app/services/oil_price.py:35-37,92-96
* **description**: The key is part of the URL query; `HTTPStatusError` text and the generic `except` log `str(exc)`.
* **impact / likelihood**: Key disclosure to log readers (feature mostly unimplemented).
* **reproduction**: Reading only.
* **evidence**: -
* **recommendation**: Pass the key as header/params and log only exception class and status.
* **planned disposition**: fixed
* **fix**: fixed in feature 010 (API key sent as request parameter, failed fetches log only exception class and HTTP status, log filter masks `apikey`/`key`/`token` query values). Verified by `test_error_text_never_contains_key`, `test_tankerkoenig_key_sent_as_param_not_in_log`, `test_logging_filter_redacts_apikey`.
* **source**: backend B-23

### F-028 Any authenticated user can consume LLM/OCR resources; failed scans leave orphan files

* **id**: F-028
* **category**: A10 (Mishandling of Exceptional Conditions)
* **component**: backend
* **severity (final)**: low. Low.
* **status**: confirmed by experiment E-A10-002 / E-A08-001
* **location**: backend/app/routers/ocr.py:149-194,258-354,329,336-337
* **description**: `scan` and `bulk-scan` need only a valid token (no property needed); limits are per IP only (F-002), no per-user quota, one shared thread pool occupied up to 300 s per call. The upload is written before OCR/engine checks and never unlinked on error.
* **impact / likelihood**: GPU/gateway cost and upload-volume fill (10 MB per scan until the nightly cleanup + 24 h).
* **reproduction**: E-A08-001 `failed_scan_leaves_no_orphan_file` (gateway_down, gateway_unauthorized); E-A10-002 `gateway_ocr_failure_modes_fail_closed` (4 params), `optional_subsystems_unconfigured` (2 params).
* **evidence**: 503/502 returned but the PNG remains in the upload directory in all 8 cases.
* **recommendation**: try/finally unlink (also serial crops), check engine availability before writing, per-user quota and global concurrency limit, require property assignment, disk quota.
* **planned disposition**: partly fixed (feature 008, 0.6.0); quota part proposed
* **fix**: orphan-file part fixed in feature 008 (release 0.6.0): files and registry rows of failed scans are removed. Verified by E-A08-001 `failed_scan_leaves_no_orphan_file` x2 and E-A10-002 `gateway_ocr_failure_modes_fail_closed` x6, `optional_subsystems_unconfigured` x2 (all passing; rerun-2: 8 failing). Still open: per-user quota and global concurrency limit for authenticated users (design decision).
* **source**: backend B-24; triage-group2/3

### F-029 LLM and OCR-service output is not validated or bounded; malformed OCR JSON gives 500; EXIF date is free text

* **id**: F-029
* **category**: A08 (Software or Data Integrity Failures)
* **component**: backend, ocr-service
* **severity (final)**: low. Low: output is a proposal confirmed by the user, upstream is operator controlled.
* **status**: confirmed by experiment E-A08-001 / E-UP-001
* **location**: backend/app/services/ocr_pipeline.py:83-87,191-250,416-423,644-658; backend/app/routers/ocr.py:172,190,571-584
* **description**: Reading and serial are cleaned but not length-bounded (5 M digits pass); the OCR-service response is used with any type or size, non-JSON or deeply nested JSON raises non-httpx exceptions (500); the serial==reading guard exists only on the LLM path; `_extract_exif_datetime` returns arbitrary text; `raw_texts` passed through.
* **impact / likelihood**: Oversized or mistyped values reach the client and the SSE stream (XSS only if a frontend renders them as HTML, which it does not: E-A05-002); 500s on a misbehaving upstream. Nothing is stored by the OCR endpoints.
* **reproduction**: E-A08-001 `gateway_hostile_response_validated`, `ocr_hostile_response_validated` (huge, nested, types, reading_equals_serial, garbage), `hostile_output_never_stored`, `bulk_scan_hostile_stream_wellformed`; E-UP-001 `exif_date_abuse_and_oversize_exif`. Mutant `gateway-output-trusted`.
* **evidence**: 10 + 1 tests fail; gateway down/unauthorized/slow, serial-to-foreign-meter and ok controls pass.
* **recommendation**: Shared `_sanitize_result` (value regex, serial `^[A-Za-z0-9]{1,64}$`, equality guard), pydantic model for the OCR response with size cap, map ValueError/RecursionError to 503, validate EXIF date with a strict pattern.
* **planned disposition**: fixed (feature 008, 0.6.0)
* **fix**: fixed in feature 008 (release 0.6.0): shared sanitizers (reading 3 to 12 digits, serial `[A-Za-z0-9./]{1,64}`, equality guard on both paths), OCR-service response validated, garbage answers map to 502, EXIF date validated. Verified by E-A08-001 (`gateway_hostile_response_validated`, `ocr_hostile_response_validated` x5, `hostile_output_never_stored`, `bulk_scan_hostile_stream_wellformed`, `model_serial_cannot_reach_foreign_meters`), E-A10-002 `gateway_ocr_failure_modes_fail_closed` (garbage), E-UP-001 `exif_date_abuse_and_oversize_exif`; all passing (rerun-2: 11 failing).
* **source**: backend B-25/B-28; triage-group3

### F-030 OCR service has no authentication and relies on network isolation

* **id**: F-030
* **category**: A07 (Authentication Failures)
* **component**: ocr-service, chart
* **severity (final)**: low. Low: reachable only inside the cluster/Compose network.
* **status**: confirmed by reading
* **location**: ocr-service/app/main.py:61-85; docker-compose.yaml:38-41; chart/templates/deployment-ocr.yaml
* **description**: Anyone who can reach port 8100 can run EasyOCR/denoise on arbitrary images (F-011); the service binds 0.0.0.0 by design; the Compose port is not published, the chart NetworkPolicy is off by default (F-016).
* **impact / likelihood**: Free GPU/CPU use and DoS from inside the cluster network.
* **reproduction**: Reading; bandit B104 triaged as by design.
* **evidence**: -
* **recommendation**: Shared-secret header check (backend already sends `OCR_API_KEY`), size limit, enable NetworkPolicy by default.
* **planned disposition**: proposed (mitigated by the F-016 fix)
* **fix**: not fixed. Feature 009 (F-016) adds NetworkPolicy defaults for the chart, which narrows who can reach the OCR service (reading only); the service still has no authentication of its own.
* **source**: backend B-25; deployment DEP-10

### F-031 API documentation and verbose health endpoint are public

* **id**: F-031
* **category**: A02 (Security Misconfiguration)
* **component**: backend
* **severity (final)**: low. Low.
* **status**: confirmed by experiment E-A02-002
* **location**: backend/app/main.py:62-69,132-,434-441,503-554
* **description**: `/api/docs` and `/api/openapi.json` are served in production regardless of `debug`; anonymous `/api/health` returns version, model name and DB/OCR/LLM/scheduler state and makes up to three outbound calls (the gateway call sends the API key); DB session closes only on success.
* **impact / likelihood**: Attack-surface disclosure and request amplification.
* **reproduction**: E-A02-002 `api_documentation_not_public` (2 params), `health_endpoint_minimal_to_anonymous`.
* **evidence**: Anonymous 200 on docs and openapi.json; health detail returned.
* **recommendation**: Disable docs unless `debug`, anonymous health = status only, detail on a cluster-internal path, cache health results, close the DB in `finally`.
* **planned disposition**: fixed
* **fix**: fixed in feature 010 (`/api/docs`, `/api/redoc`, `/api/openapi.json` only with `ENABLE_API_DOCS=true`; anonymous `/api/health` returns `{status}` and makes no outbound calls; detail on `/api/health/details` for admins with a 30 s cache; DB session closed in `finally`; probes and healthchecks unchanged in path, chart probes got `timeoutSeconds: 3`). Verified by `backend/tests/test_health_docs_misc.py`. Not done: separate cluster-internal path (admin auth is used instead).
* **source**: backend B-26; triage-group1

### F-032 Scan stores undecoded, truncated or broken images

* **id**: F-032
* **category**: A08 (Software or Data Integrity Failures)
* **component**: backend
* **severity (final)**: low. Low.
* **status**: confirmed by experiment E-UP-001
* **location**: backend/app/routers/ocr.py:286-288,329
* **description**: Only `filetype.guess_mime` (header) is checked; truncated JPEG/PNG, signature-only and zeroed bodies are stored and later served as `image/*`.
* **impact / likelihood**: Garbage persistence and disk fill, downstream decode errors are swallowed.
* **reproduction**: E-UP-001 `truncated_and_broken_images`.
* **evidence**: All five broken inputs accepted with 200.
* **recommendation**: `Image.open().verify()` plus bounded `load()` before writing, reject undecodable data with 400.
* **planned disposition**: proposed
* **source**: triage-group3

### F-033 EXIF GPS data is retained in stored and served images

* **id**: F-033
* **category**: A06 (Insecure Design)
* **component**: backend
* **severity (final)**: low. Low (privacy).
* **status**: confirmed by experiment E-UP-001
* **location**: backend/app/routers/ocr.py:329
* **description**: The upload is written verbatim; the stored copy keeps the GPS IFD and is served to every property member (and to any authenticated user while unattached).
* **impact / likelihood**: Privacy: location of the user's home leaks to other members.
* **reproduction**: E-UP-001 `exif_gps_not_retained`.
* **evidence**: GPS IFD present in the stored file.
* **recommendation**: Strip metadata on store (re-encode or remove IFD 0x8825) after extracting the EXIF date; keep hashing the original bytes.
* **planned disposition**: proposed
* **source**: triage-group3

### F-034 JWT parsing robustness: non-string `alg` header or `sub` gives 500

* **id**: F-034
* **category**: A04 (Cryptographic Failures)
* **component**: backend
* **severity (final)**: low. Low.
* **status**: confirmed by experiment E-A04-001
* **location**: backend/app/auth.py:29-32,96-107
* **description**: Only `JoseError` and `ValueError` are caught; header `alg` as a list and `sub` as int/bool/list/dict raise other exception types.
* **impact / likelihood**: 500 instead of 401; needs a validly signed token, so only with a leaked key.
* **reproduction**: E-A04-001 `unusual_headers_rejected`, `missing_or_malformed_sub_rejected` (4 params).
* **evidence**: 5 failures; alg=none, wrong key, HS/RS confusion, tampering, role claim, bearer-only, expired, not-yet-valid all pass (46 passing A04 tests).
* **recommendation**: Catch `Exception` around decode+validate -> 401; require `isinstance(sub, str)`; `claims_options` essential `exp` and `sub`.
* **planned disposition**: proposed
* **source**: triage-group1

### F-035 Database outage mid-request: unhandled 500, acknowledgement lost after commit

* **id**: F-035
* **category**: A10 (Mishandling of Exceptional Conditions)
* **component**: backend
* **severity (final)**: low. Low.
* **status**: confirmed by experiment E-A10-001 (intermittent)
* **location**: backend/app/routers/readings.py:100 (`db.refresh()` after `commit()`); backend/app/database.py
* **description**: With PostgreSQL stopped every request ends in unhandled `OperationalError`; a reading can be stored while the 500 is returned (client retry duplicates it); uvicorn closes the connection after unhandled 500s.
* **impact / likelihood**: Duplicate or uncertain writes during DB failover; no 503 mapping.
* **reproduction**: E-A10-001 `postgres_stopped_midrequest` (failed in 1 of 2 runs).
* **evidence**: Bounded latency and recovery after restart hold; the failing part is the response mapping and the acknowledgement window.
* **recommendation**: Global handler for `OperationalError`/`DBAPIError` -> 503, short `pool_timeout`, return data built before the commit.
* **planned disposition**: proposed
* **source**: rerun-1; triage-group2

### F-036 Unattached scan uploads are readable by every authenticated user

* **id**: F-036
* **category**: A01 (Broken Access Control)
* **component**: backend
* **severity (final)**: low. Low.
* **status**: confirmed by experiment E-A01-002
* **location**: backend/app/routers/uploads.py:40-55
* **description**: A file with no referencing reading is served to any authenticated user who knows the UUID name; accepted in a code comment.
* **impact / likelihood**: Weak owner binding; protection rests on the unguessable name.
* **reproduction**: E-A01-002 `unattached_scan_upload_not_readable_by_other_user`.
* **evidence**: Other user receives 200.
* **recommendation**: Upload ownership table (see F-004); serve unattached files only to uploader and global admins, or record as accepted risk.
* **planned disposition**: proposed
* **source**: triage-group1

### F-037 `replaced_by_id` may reference another tenant's meter; `integration_config` stored in plaintext

* **id**: F-037
* **category**: A01 (Broken Access Control)
* **component**: backend
* **severity (final)**: low. Low.
* **status**: confirmed by experiment E-A01-005
* **location**: backend/app/schemas/meters.py:27,55; backend/app/routers/meters.py:77-78,228-229; backend/app/models.py (Meter.integration_config)
* **description**: Only an FK guards `replaced_by_id`; replaced meters become un-editable (409), so a mistaken or hostile PATCH is irreversible. `integration_config` (url, token, entity) is plaintext text although the API redacts it on output and no consumer exists in `backend/app`.
* **impact / likelihood**: Cross-property reference, irreversible state, dormant secret store in DB and backups.
* **reproduction**: E-A01-005 `meter_patch_cannot_reference_foreign_property_meter`.
* **evidence**: Manager stored a foreign-property meter id (200).
* **recommendation**: Validate same property and not self; encrypt or drop `integration_config` until used.
* **planned disposition**: proposed
* **source**: backend B-31; triage-group1

### F-038 Container build hygiene: floating tags, no hash-pinned dependencies, no .dockerignore, shell-form CMD, EasyOCR models without checksum

* **id**: F-038
* **category**: A03 (Software Supply Chain Failures)
* **component**: images, compose, backend, ocr-service
* **severity (final)**: low. Low.
* **status**: confirmed by reading
* **location**: backend/Dockerfile:1,13-14,25-26; ocr-service/Dockerfile:1,24-26; frontend/Dockerfile:1,6,9; docker-compose.yaml:3,18,82
* **description**: Base images by tag (`python:3.12-slim`, `node:22-alpine`, `static-web-server:2`, `ollama:latest`, `nginx:alpine`), `pip install` without hashes or lock for transitive dependencies, `COPY . .` bakes tests and any local `.env` into layers, `CMD` in shell form leaves `sh` as PID 1 (SIGTERM not forwarded), model downloads at build time without checksum, `apt-get` unversioned (hadolint DL3008/DL3025).
* **impact / likelihood**: Non-reproducible builds, drift, wider window for a compromised upstream tag.
* **reproduction**: Reading, hadolint, trivy config.
* **evidence**: Runtime requirements are pinned with `==`; non-root users; multi-stage frontend.
* **recommendation**: Digest pins with Renovate, `pip-compile --generate-hashes`, `.dockerignore`, `exec uvicorn`, checksum for models.
* **planned disposition**: proposed
* **source**: backend B-27; deployment DEP-03/DEP-04; tooling hadolint

### F-039 GitHub Actions pinned to mutable tags; `test.yaml` has no `permissions:` block; no Dependabot/Renovate

* **id**: F-039
* **category**: A03 (Software Supply Chain Failures)
* **component**: ci
* **severity (final)**: low. Low.
* **status**: confirmed by reading
* **location**: .github/workflows/test.yaml (no top-level permissions), all `uses:` lines, .github/workflows/build.yaml:116
* **description**: No commit-SHA pinning (including third-party security actions that receive the token), default token scope for the test workflow, `secrets.GITHUB_TOKEN` expanded inside a `run:` script, unpinned `pip` and Helm versions.
* **impact / likelihood**: A hijacked action tag runs with the job token; the packages-write build job is separate and scoped.
* **reproduction**: Reading, tooling TL-7.
* **evidence**: Triggers are push/pull_request only, no `pull_request_target` / `workflow_run`, no untrusted expression in `run:`.
* **recommendation**: Pin by SHA via Renovate, `permissions: contents: read` at the top of `test.yaml`, pass the token via `env:`, add dependency update automation.
* **planned disposition**: proposed
* **source**: deployment DEP-08; tooling TL-7

### F-040 Built images are mutable and unsigned; no SBOM/provenance; CI does not scan images or chart misconfiguration

* **id**: F-040
* **category**: A08 (Software or Data Integrity Failures)
* **component**: ci, images, chart
* **severity (final)**: low. Low.
* **status**: confirmed by reading
* **location**: chart/values.yaml, chart/templates/_helpers.tpl:40-56, .github/workflows/build.yaml:43-47, test.yaml:272-286
* **description**: Mutable `main` / `sha-` / branch tags with `IfNotPresent`, no cosign, no provenance; Trivy only in `fs` mode (`ignore-unfixed`), no image scan, no misconfig scanners, no `pip-audit`/`npm audit`/hadolint/helm lint+kubeconform in CI.
* **impact / likelihood**: What runs cannot be verified against what CI built; known-vulnerable layers not caught.
* **reproduction**: Reading, tooling sections 8 and 10.
* **evidence**: -
* **recommendation**: Digest pinning in the GitOps repo, cosign + provenance, `trivy image` before push, enable `vuln,secret,misconfig`, add the missing audit steps.
* **planned disposition**: proposed
* **source**: deployment DEP-07/DEP-09; tooling TL-9

### F-041 CSP lacks `object-src`/`base-uri`/`form-action`; header sets differ between Compose proxy and chart; static server sets none

* **id**: F-041
* **category**: A02 (Security Misconfiguration)
* **component**: frontend, compose, chart
* **severity (final)**: low. Low.
* **status**: confirmed by reading
* **location**: nginx.conf:5-9; chart/templates/middleware-security-headers.yaml:2,19-23; frontend static-web-server defaults
* **description**: Missing directives do not fall back to `default-src`; `style-src 'unsafe-inline'` (probably needed by Vue/ApexCharts); Compose proxy lacks HSTS/Permissions-Policy/COOP/CORP, chart comment about parity is wrong; `X-Frame-Options SAMEORIGIN` vs `frame-ancestors 'none'`; deprecated `X-XSS-Protection`; direct access to the frontend container gets no headers.
* **impact / likelihood**: Defence in depth only; no script-injection path found (E-A05-002).
* **reproduction**: Reading; header check `E-A02-002 security_headers_set_by_app_or_documented_ingress` passes.
* **evidence**: -
* **recommendation**: Add `object-src 'none'; base-uri 'self'; form-action 'self'`, one source of truth for headers, drop `X-XSS-Protection`.
* **planned disposition**: proposed
* **source**: frontend FE-03

### F-042 JWT kept in `sessionStorage`

* **id**: F-042
* **category**: A04 (Cryptographic Failures)
* **component**: frontend
* **severity (final)**: low. Low (candidate for accepted risk).
* **status**: confirmed by reading
* **location**: frontend/src/stores/auth.ts:22,26,52
* **description**: Token readable by any script on the origin; logout only deletes it client-side (see F-007).
* **impact / likelihood**: An XSS or malicious extension exfiltrates a usable token; no XSS sink found.
* **reproduction**: Reading; E-A05-002 `frontend_has_no_raw_html_sinks` passes.
* **evidence**: -
* **recommendation**: Accepted trade-off for an SPA with strict CSP: document it, or move to an HttpOnly cookie together with F-008.
* **planned disposition**: proposed
* **source**: frontend FE-02

### F-043 Operational scripts contain hard-coded default credentials, internal addresses and disabled SSH host-key checking

* **id**: F-043
* **category**: A07 (Authentication Failures)
* **component**: scripts
* **severity (final)**: low. Low.
* **status**: confirmed by reading
* **location**: scripts/test_ocr.sh:5, scripts/test_ocr2.sh:5, scripts/benchmark_ollama.py:155,701,783,791
* **description**: The default login of the README is embedded, `StrictHostKeyChecking=no` is used for SSH to a GPU host as root, internal addresses are defaults.
* **impact / likelihood**: MITM on the LAN, credential reuse.
* **reproduction**: Reading; gitleaks does not flag them.
* **evidence**: Other scripts use env vars, list-arg `subprocess`, bound SQL parameters.
* **recommendation**: Read credentials from env, `StrictHostKeyChecking=accept-new`, mark dev tooling clearly.
* **planned disposition**: proposed
* **source**: deployment DEP-11

### F-044 Dependency advisories without a reachable path: authlib 1.6.9, pydantic-settings 2.13.1, Mako, pip

* **id**: F-044
* **category**: A03 (Software Supply Chain Failures)
* **component**: backend, ocr-service, images
* **severity (final)**: low. Low: advisory reported as Medium by the tools, downgraded because the vulnerable code is not reachable.
* **status**: confirmed by reading
* **location**: backend/requirements.txt:8,11; ocr-service/requirements.txt
* **description**: pip-audit: authlib CVE-2026-41425 / -41479 / -44681 (fixed in 1.6.12), pydantic-settings CVE-2026-58203 (fixed 2.14.2), Mako 1.4.1 and pip 25.0.1 in the image. Reachability read: authlib is used only for `authlib.jose` JWT handling (no OAuth client or server grants, no `cache=`); `secrets_dir` is set without `secrets_nested_subdir`; Mako only renders Alembic templates.
* **impact / likelihood**: No exploitable path found; hygiene.
* **reproduction**: pip-audit, trivy fs and image scan; `grep` of `backend/app` for `OAuth(`, `cache=`, `secrets_nested`.
* **evidence**: -
* **recommendation**: Bump authlib >= 1.6.12 and pydantic-settings >= 2.14.2 via Renovate; rebuild images with `--pull`.
* **planned disposition**: proposed
* **source**: tooling TL-2/TL-3/TL-9

### F-050 Frontend image runs as root outside the chart; images have no HEALTHCHECK; migrations run at container start with the application DB role

* **id**: F-050
* **category**: A02 (Security Misconfiguration)
* **component**: images, compose, migrations
* **severity (final)**: low. Low.
* **status**: confirmed by reading
* **location**: frontend/Dockerfile:9-17; all Dockerfiles; backend/Dockerfile:25; backend/alembic/versions/0004_remove_ldap.py
* **description**: static-web-server has no `USER` (trivy DS-0002; chart overrides to 65534); no HEALTHCHECK (Compose cannot detect hangs); `alembic upgrade head` in the start command needs permanent DDL rights, concurrent replicas would race (chart pins 1), migration 0004 drops data irreversibly; implicit UID 1000 versus hard-coded `runAsUser`.
* **impact / likelihood**: Compose/podman run as root; app compromise has schema-altering privileges.
* **reproduction**: Reading, trivy config, hadolint.
* **evidence**: -
* **recommendation**: `USER 65534`, HEALTHCHECK or Compose healthchecks, separate migration Job/initContainer with a privileged role, explicit `useradd -u 1000`.
* **planned disposition**: proposed
* **source**: deployment DEP-04/DEP-05; tooling TL-5 (postgres container does drop ALL: false positive)

### F-045 404-before-403 ordering leaks existence of properties, meters and readings

* **id**: F-045
* **category**: A01 (Broken Access Control)
* **component**: backend
* **severity (final)**: informational. Informational.
* **status**: confirmed by reading
* **location**: backend/app/permissions.py:280-284,311-323; backend/app/routers/ocr.py:212-221,303-308; backend/app/routers/dashboard.py:161-169
* **description**: Existence is checked before access; UUIDs make property/meter probing impractical, but `rescan/{reading_id}` enumerates sequential integer ids by the 404/403 difference.
* **impact / likelihood**: Low-value oracle on reading ids.
* **reproduction**: Reading only.
* **evidence**: -
* **recommendation**: Answer 404 for both when the caller lacks access.
* **planned disposition**: proposed (documented, not changed)
* **fix**: not changed in feature 010 (informational, documented only): existence is checked before access; UUIDs make probing impractical and the integer reading id oracle (`rescan/{reading_id}`) stays a candidate for accepted risk or a 404-for-both change in the router owner's feature.
* **source**: backend B-29

### F-046 `X-Request-ID` is reflected unvalidated and unbounded; client-derived strings reach log lines

* **id**: F-046
* **category**: A05 (Injection)
* **component**: backend
* **severity (final)**: informational. Informational.
* **status**: confirmed by experiment E-A02-002
* **location**: backend/app/main.py:92-96,462-467; backend/app/routers/ocr.py:94,653,657; backend/app/services/llm_client.py:186
* **description**: 8000-character request id echoed; filenames and upstream model strings logged raw. CRLF injection is not possible (A05-001 header test passes; parser rejects raw CR/LF).
* **impact / likelihood**: Log noise / forging only.
* **reproduction**: E-A02-002 `request_id_header_not_reflected_unbounded`.
* **evidence**: 8000 characters returned.
* **recommendation**: Accept `^[A-Za-z0-9._-]{1,64}$` else generate a UUID; `repr()` client-derived log arguments.
* **planned disposition**: fixed
* **fix**: fixed in feature 010 (`X-Request-ID` must match `^[A-Za-z0-9._-]{1,64}$`, else a UUID is generated). Verified by `test_request_id_validated`. Client-derived filenames/model strings in `routers/ocr.py` and `services/llm_client.py` are outside this feature's files and still logged raw.
* **source**: backend B-30

### F-047 Documentation does not match the implementation (ports, redirect URI, routing mode, default login)

* **id**: F-047
* **category**: A02 (Security Misconfiguration)
* **component**: compose, scripts
* **severity (final)**: informational. Informational.
* **status**: confirmed by reading
* **location**: README.md:30,103; docker-compose.yaml:88; backend/app/config.py:36-39; frontend/src/router/index.ts:5
* **description**: README says port 8080 / redirect `/auth/callback` / hash routing; reality is 8081, `/api/auth/callback`, history mode; chart comment on header parity is inverted.
* **impact / likelihood**: A mis-registered redirect URI fails closed; wildcard registrations made to 'fix' it would be dangerous.
* **reproduction**: Reading.
* **evidence**: -
* **recommendation**: Correct the README and document the secret requirements.
* **planned disposition**: proposed
* **source**: deployment DEP-12

### F-048 Chart: secrets injected as env via `envFrom`; HSTS preload and includeSubdomains set unconditionally

* **id**: F-048
* **category**: A02 (Security Misconfiguration)
* **component**: chart
* **severity (final)**: informational. Informational.
* **status**: confirmed by reading
* **location**: chart/templates/deployment-backend.yaml:42-44; chart/templates/middleware-security-headers.yaml:12-15
* **description**: All Secret keys appear as environment variables (visible via `describe` and /proc; file mode exists), the DB password must be kept consistent in two Secrets, HSTS preload on a shared parent domain is hard to retract, `ingress.tls: false` still serves the `websecure` entrypoint with the default certificate.
* **impact / likelihood**: None direct.
* **reproduction**: Reading.
* **evidence**: No Secret objects or credentials in templates or values (positive).
* **recommendation**: Prefer the secret volume mode, make HSTS preload opt-in.
* **planned disposition**: proposed
* **source**: deployment DEP-13

### F-049 Frontend and test-tooling hygiene: dev-only npm advisories, unused axios, python-jose in test requirements, error detail rendering

* **id**: F-049
* **category**: A03 (Software Supply Chain Failures)
* **component**: frontend, ci, backend
* **severity (final)**: informational. Informational.
* **status**: confirmed by reading
* **location**: frontend/package.json:13-38; backend/requirements-test.txt; frontend/src/components/BulkImport.vue:799; frontend/src/views/Login.vue:63,71
* **description**: `npm audit`: 10 advisories, all dev-only (`--omit=dev`: 0); `axios` listed but not imported in `frontend/src`; `vitest ^1` against `vite ^8`; python-jose / ecdsa advisories only in test requirements (not in runtime image); 5 `detect-object-injection` eslint-security warnings (array indexing); BulkImport shows raw server `detail`, login does not distinguish 429 from 401.
* **impact / likelihood**: None shipped.
* **reproduction**: npm audit, pip-audit, eslint-security, `grep axios`.
* **evidence**: Production dependency audit is clean.
* **recommendation**: Add `npm audit --omit=dev --audit-level=high` to CI, remove axios, reuse the OcrCapture detail pattern, map 429 to a lockout message.
* **planned disposition**: proposed
* **source**: tooling TL-4/TL-8; frontend FE-04/FE-06/FE-07

## 4. Experiments

Latest run: `scripts/security-rig.sh --quick` (rerun-3, release 0.6.0 = features 006 to 009): races 20 rounds, 30 parallel sign-ins, limiter disabled rig-wide (only E-A07-001 restarts with the real limiter, E-A10-003 restarts with small bulk limits), backend started with `--no-proxy-headers` and `TRUSTED_PROXIES=10.255.255.0/24` like the production image. 277 tests, **257 passed, 19 failed (plus E-A10-001, intermittent: failed in one run, passed in the final one), 1 skipped** (A02 needs `SEC_RIG_APP_DEBUG=false`), about 8 min pytest. Every failure is a real application weakness of a low finding that is still open (`review/rerun-3.md`); the rig and experiment helpers were adapted to feature 007 (token minting reads `users.token_version`, sign-in exchanges the one-time code).

| ID | Goal | Result (rerun-2) | Failing tests (fail / total items) | Findings |
|---|---|---|---|---|
| E-A01-001 | Every endpoint requires authentication (OpenAPI enumeration) | pass (`POST /auth/exchange` added to the public allow-list: it needs no bearer token, the one-time code is the credential) | 0 / 1 | - |
| E-A01-002 | Cross-tenant access by ID, listings, streaming bulk scan, filename reference, unattached upload, hint-rescan | pass (was FAIL, 6 failed) | 0 / 10 | F-004 fixed, F-036 |
| E-A01-003 | Role matrix for admin and property levels, manager escalation, admin vs superadmin | pass (was FAIL, 2 failed) | 0 / 22 (property-level matrix now expects 403 for manager on property PATCH and membership changes) | F-005, F-006 fixed |
| E-A01-004 | Upload path traversal, symlink, stored image path cannot reach other files | pass | 0 / 3 | - |
| E-A01-005 | Extra fields, foreign `replaced_by_id`, privileged fields on own account | FAIL | 1 failed (foreign meter reference) | F-037 |
| E-A01-006 | Outbound URLs never request controlled, stored integration config | FAIL (now 2 items: `dict` and `json_string` form of `integration_config`) | 1 failed: `dict` gives 500 (schema `Optional[Any]` vs `Text` column: psycopg2 cannot adapt a dict); `json_string` passes, so the SSRF half (never fetched, never echoed) now runs and holds; the enum cause (F-014) is gone | F-023 |
| E-A02-001 | Startup refuses unsafe configuration | pass (was FAIL, 19 failed) | 0 / 24 | F-009 fixed |
| E-A02-002 | Public docs, headers, banners, health detail, error bodies, CORS, request id | FAIL | 5 failed (docs x2, health detail, authenticated error bodies, request id reflection); the remaining items pass | F-031, F-023, F-046 |
| E-A04-001 | JWT forgery and malformed tokens | FAIL (tokens built with `tv`/`iat` like the app issues them; new `token_version_claim_enforced` x5 passes) | 1 failed (header `zip: DEF` accepted, token still verifies); 57 pass | F-034, F-007 (fixed) |
| E-A05-001 | Injection in JSON, query, path, multipart, headers, enums; SQL payloads inert | FAIL | 3 failed (json fields, query/path, multipart); `enum_members_do_not_crash` now passes (F-014 fixed); header injection and SQL-inert pass | F-023 |
| E-A05-002 | Stored XSS round trip, error bodies, SSE echo, frontend sinks | pass (markup in `image_path` is now refused with 4xx, markup in `note` etc. is stored inert) | 0 / 4 | - |
| E-A06-001 | Parallel first sign-in yields one user | pass (30 sign-ins); fails at 32+ (ad-hoc), see F-003 | 0 / 1 | F-003 |
| E-A06-002 | Races: same image hash, identical readings | FAIL | 3 failed (same_hash x2: 2 to 4 responses 201 for one hash; identical reading: 20 rows); fail-fast after 5 failing rounds | F-022 |
| E-A06-003 | Role change vs sign-in, privilege after IdP removal | pass (was FAIL: privilege kept after IdP removal; role change vs sign-in 20 rounds) | 0 / 2 | F-007 fixed |
| E-A06-004 | State replay, parallel reuse, expiry/forgery, browser binding | FAIL | 1 failed (state not bound to browser); replay, parallel reuse (20 rounds) and forged/expired state pass | F-017 |
| E-A07-001 | Brute-force limiter, X-Forwarded-For bypass, trusted-proxy rightmost rule | pass (3 tests incl. new `trusted_proxy_rightmost_untrusted_entry_is_the_key`) | 0 / 3 | F-002 (fixed in 004) |
| E-A07-002 | PKCE parameters, verifier mismatch | pass | 0 / 2 | - |
| E-A07-003 | Group claim shapes (36 parameter sets) | pass | 0 / 36 | F-001 (fixed in 004) |
| E-A07-004 | Non-ASCII and malformed superadmin credentials | pass | 0 / 1 | F-020 |
| E-A07-005 | Login code exchange: JWT never in the redirect, code in the fragment, single use, expired/unknown/malformed codes, 15 parallel redeems | pass (new) | 0 / 4 | F-008 fixed |
| E-A07-006 | Logout revokes the token and all other tokens of the user, harmless with invalid token, other users unaffected | pass (new) | 0 / 2 | F-007 fixed |
| E-A07-007 | Admin role change, role change at sign-in and IdP group removal revoke older tokens; unchanged role does not | pass (new) | 0 / 3 | F-007 fixed |
| E-A08-001 | Hostile gateway/OCR responses, failure mapping, orphan files, stream well-formedness | pass (was FAIL, 10 failed) | 0 / 23 | F-029, F-028 fixed |
| E-A09-001 | No secrets in logs, security events present | pass (also covers the one-time login code and the exchanged JWT as log needles) | 0 / 1 | F-010 fixed |
| E-A02-003 | Every meter type x unit (9) and every integration type (4) creatable on the migration-built database | pass (new) | 0 / 13 | F-014 fixed |
| E-A10-001 | PostgreSQL stopped mid-request | FAIL in some runs (intermittent: failed in the first rerun-3 run, passed in the final one) | 0 or 1 | F-035 |
| E-A10-002 | IdP and gateway/OCR failure modes, slow backends, unconfigured subsystems | FAIL | 2 failed (IdP slow, cold-cache login); orphan x4, OCR garbage, unconfigured x2 and OCR failure modes now pass | F-026; F-028, F-029 fixed |
| E-A10-003 | Bulk scan limits with small `MAX_BULK_TOTAL_BYTES`/`MAX_BULK_FILES`: 413 over budget, 400 over count, 413 for declared and chunked oversize bodies, nothing stored, no OCR/LLM call, control streams | pass (new; the chunked case found a 400 instead of 413, fixed in `body_limit.py`) | 0 / 1 | F-012 fixed |
| E-A10-004 | No `idle in transaction` database session while a bulk scan waits 9 s for the gateway | pass (new) | 0 / 1 | F-013 fixed |
| E-UP-001 | Uploads: magic bytes, polyglots, size, bombs, truncated, EXIF, filenames, duplicates, adoption | FAIL | 2 failed (truncated/broken images stored, EXIF GPS kept); EXIF date abuse passes (was failing), 13 pass | F-032, F-033; F-029, F-011, F-004 fixed |

Per file (pass / fail), rerun-3: A01 39/2, A02 34/5 (+1 skip), A04 57/1, A05 7/3, A06 6/4, A07 42/0 plus new session file (E-A07-005..007, E-A10-003/004, E-A02-003) 24/0, A08 23/0, A09 1/0, A10 11/2 (E-A10-001 intermittent, counted passing), uploads 13/2. Sum 257 passed, 19 failed, 1 skipped (277). Rerun-2: A01 38/2, A02 34/5 (+1 skip), A04 52/1, A05 6/4, A06 5/5, A07 42/0, A08 13/10, A09 1/0, A10 3/10, uploads 12/3 (247 tests). Rerun-1: A01 30/10, A02 15/24, A04 46/7, A05 6/4, A06 5/5, A07 22/19, A08 13/10, A09 0/1, A10 4/9, uploads 10/5 (246 tests).
Slowest tests (rerun-2 `--durations`): A05-001 json fields 82 s, A05-001 query 55 s, A06-001 40 s, A08 ocr_slow 30 s; all others below 20 s.
Race experiments: quick profile 20 rounds with 30 parallel sign-ins / 20 parallel writes; the specification's 100-repetition profile is `scripts/security-rig.sh` without `--quick`. Failure rates observed: A06-002 same hash produced 2 to 4 successful creations for one hash (fail-fast after 5 failing rounds, F-022 unfixed); A06-001, A06-003 role change and A06-004 replay/parallel reuse had 0 failing rounds out of 20.

### Mutant checks (SC-006)

Five patches plus one unpatched real bug are defined in `security/mutants/README.md`. Expected detection (derived by reading, patches dry-run against a copy of `backend/` and byte-compiled):

| Mutant | Must fail | Actual result |
|---|---|---|
| access-check-removed | E-A01-002 (cross-tenant, filename, hint-rescan), E-A01-003 property levels, E-A01-005 meter patch, E-UP-001 adopt | not yet executed against the rig |
| no-unique-retry | E-A06-001 parallel first sign-in | not yet executed against the rig |
| alg-none-accepted | E-A04-001 alg none, tampered token | not yet executed against the rig |
| size-check-removed | E-UP-001 size limit | not yet executed against the rig |
| gateway-output-trusted | E-A08-001 hostile gateway modes, never stored | not yet executed against the rig |
| group-substring (real bug F-001) | E-A07-003 | detected: 17 failures on the unpatched backend |

Because mutant runs need the rig and backend sources are being changed by the high-severity fixes, the five patched runs are scheduled after those fixes merge and their actual results replace the third column then (task T025).

## 5. Tooling results

Run 2026-10-09 from throw-away environments outside the repository (`review/tooling.md`). Real findings are mapped to finding IDs, the rest is triaged as false positive or informational.

| Tool | Scope | Result | Triage |
|---|---|---|---|
| bandit 1.9 | backend/app, ocr-service/app | 0 issues (3007 LOC); 8 `# nosec` hits re-evaluated with `--ignore-nosec` | False positives or informational: B105 sentinel compared to reject it (config.py:98), B608 x2 static f-string fragments with bound values (retention.py:109,125), B110 best-effort shutdown/EXIF (informational), B106 OAuth `token_type` literal, B104 bind 0.0.0.0 by design. No finding. |
| pip-audit | backend/requirements.txt, ocr-service/requirements.txt, requirements-test.txt | authlib 1.6.9 (3 CVEs), pydantic-settings 2.13.1 (1 CVE); test-only python-jose and ecdsa | Not reachable (authlib only used for `authlib.jose`, no nested secrets dir, test requirements not in image): F-044, F-049. |
| Trivy fs (vuln, secret, misconfig) | repository without node_modules | Vulnerabilities as pip-audit; secrets none; misconfig: frontend root user HIGH (DS-0002), HEALTHCHECK LOW x3, chart KSV items | Frontend root: chart overrides to 65534 (F-050); KSV-0011/0020/0021/0110/0125 informational for a Helm chart; postgres container already drops ALL (false positive TL-5). |
| Trivy image | backend, frontend, ocr-service built with podman | backend 6 CRITICAL / 107 HIGH OS packages, ocr 8 / 128 (stale locally cached base image, fixes exist in current point releases), frontend 0, Python layers: only the items above and Mako/pip | Rebuild with `--pull` and rescan (F-040, F-044); runtime check: backend runs as uid 1000. |
| hadolint | three Dockerfiles | DL3008, DL3025 (shell-form CMD), DL3066; frontend clean | F-038. |
| gitleaks | git history (81 commits), working tree | History: no leaks. Working tree: 4 `generic-api-key` hits in `security/rig/.state/rig.env` | False positive: gitignored rig file with throw-away values, never committed. Keep the ignore rule. |
| eslint-plugin-security | frontend | 0 errors, 5 `detect-object-injection` warnings (BulkImport.vue:446,468,573,601, Dashboard.vue:186) | Informational (array indexing with internal keys); F-049. |
| npm audit | frontend | 10 advisories (3 critical, 5 high, 2 moderate), all dev-only; `--omit=dev`: 0 | Not shipped; F-049. |
| helm lint, helm template, kubeconform | chart | lint 0 failed; 10 resources rendered; strict validation 8 valid, 0 invalid, 2 CRDs skipped | Pass. Manual hardening review recorded in matrix (chart row); F-016, F-048. |
| docker-compose review | docker-compose.yaml | No privileged, host network or docker.sock; ports 11434 and 8000 on all interfaces; literal dev credentials | F-015, F-009. |
| workflow review | build.yaml, test.yaml | No `pull_request_target`; tag-pinned actions; `test.yaml` without `permissions` | F-039, F-040. |

Tooling severities were re-rated in this report: the Medium rating of TL-2/TL-3/TL-4 (advisories) became low/informational because the vulnerable code is not reachable (F-044, F-049); TL-1 (X-Forwarded-For) is F-002 (high).

## 6. Not verifiable from the repository

* **Live cluster:** whether the NetworkPolicy is enabled and enforced (CNI, ipset), `networkPolicy.ingressFrom` in the GitOps values, pod security admission, RBAC, `automountServiceAccountToken` on the postgres pod, deployed image digests and tags (F-016, F-040).
* **GitOps values:** strength of `JWT_SECRET_KEY` and `SUPERADMIN_PASSWORD`, whether the superadmin login is enabled, `DEBUG`, how secrets are provisioned (F-009).
* **Ingress / Traefik:** whether Traefik overwrites X-Forwarded-For and its `forwardedHeaders.trustedIPs`, TLS versions, middleware attachment to all routers, access-log format and whether query strings are retained (F-002, F-008), response headers actually served, body-size limits (F-012), `/api/docs` reachability from outside.
* **Identity provider (Authentik):** shape of the `groups` claim (string versus list), redirect-URI strictness, PKCE enforcement, token and session lifetimes, whether the IdP session ends on app logout, whether usernames are user-editable (F-001, F-017, F-019).
* **AI gateway and OCR hosts:** authentication of the gateway, Ollama and Lemonade hosts, real latency and size behaviour (F-011, F-029, F-028).
* **GitHub settings:** default workflow token permissions, branch protection and required checks, secret-scanning push protection, Dependabot alerts, who can create `v*` tags, GHCR package visibility (F-039).
* **PostgreSQL:** TLS between backend and database, role privileges of the application account, `pg_hba`, backup and at-rest encryption, how the production schema was created (`create_all` vs Alembic, F-014).
* **Browser side:** history and extension exposure of the token (F-008), `/api/uploads/*` content-type and `nosniff` headers at the live ingress (the rig confirms them for the backend, E-UP-001 polyglot test passes).
* **Resource effects:** memory and CPU behaviour of F-011/F-012/F-013 on memory-limited pods (the experiment confirms acceptance of the input, not the OOM).

## 7. Decisions

Accepted risks and operator decisions are recorded here with reason and owner. At the time of writing nothing has been accepted; the table is the skeleton to be filled when the operator has gone through section 3.

| Finding | Decision (fix / track / accept) | Reason | Owner | Date | PR / issue |
|---|---|---|---|---|---|
| F-001 | fix in 004 | severity high | repository owner | - | fixed in v0.4.1 (PR #12) |
| F-002 | fix in 004 | severity high | repository owner | - | fixed in v0.4.1 (PR #12) |
| F-003 | fix in 004 | severity high | repository owner | - | fixed in v0.4.1 (PR #12) |
| F-004, F-005, F-006, F-009, F-010, F-011 | fixed (feature 006, spec `specs/006-security-medium-fixes/`) | small, well understood, protect tenant isolation, privileges, secrets and availability; verified by the experiments named in section 3 (rerun-2) | repository owner | 2026-10-17 | feature 006 |
| F-007, F-008, F-012, F-013, F-014, F-015, F-016 | fixed (features 007, 008, 009, release 0.6.0) | the last open medium findings; verified by E-A07-005 to E-A07-007, E-A10-003, E-A10-004, E-A02-003 (rig) and by reading/`helm template` for the Compose and chart parts (F-015, F-016, Traefik middleware of F-012) | repository owner | 2026-10-17 | features 007, 008, 009 |
| F-028 (orphan files), F-029 | fixed (feature 008, release 0.6.0) | verified by E-A08-001, E-A10-002, E-UP-001; the per-user quota part of F-028 stays proposed | repository owner | 2026-10-17 | feature 008 |
| F-017 to F-050 except F-028, F-029 (low / informational) | operator decides | candidates for accepted risk: F-042 (sessionStorage), F-045, F-046, F-047, F-048, F-049 | repository owner | - | - |

Review limits: the rig runs the backend on a loopback address against synthetic stand-ins (IdP, gateway, OCR); results for infrastructure items are by reading only. Experiment IDs are stable and can be re-run with `scripts/security-rig.sh` after each fix.
