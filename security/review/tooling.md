# Automated tooling results (T026 + T027)

Run date: 2026-10-09, worktree `zaehl-o-mat-004-security-review` (branch 004). Tools ran from throw-away venv / containers
outside the repo. No secrets are reproduced here (gitleaks run with `--redact`). Raw outputs were kept outside the repo (scratchpad).

## Summary of real findings

| ID | Sev | Where | Finding |
|----|-----|-------|---------|
| TL-1 | Medium | `backend/Dockerfile:25` + `nginx.conf` (X-Forwarded-For) + `backend/app/limiter.py:4` | `--forwarded-allow-ips='*'` makes uvicorn trust the **leftmost** XFF entry. nginx (`$proxy_add_x_forwarded_for`) and Traefik append to a client-supplied XFF, so an attacker sets `X-Forwarded-For: <random>` per request and gets a fresh slowapi rate-limit bucket (login/OCR throttling bypass). Verified with uvicorn 0.42.0: `get_trusted_client_host("1.2.3.4, 10.0.0.1")` returns `1.2.3.4`. Fix: restrict to proxy CIDR/pod network, or make the edge proxy overwrite XFF (`proxy_set_header X-Forwarded-For $remote_addr`). |
| TL-2 | Medium | `backend/requirements.txt:11` `authlib==1.6.9` | 3 CVEs fixed in 1.6.12: CVE-2026-41425 (no CSRF protection when `OAuth(cache=...)` is used, starlette client), CVE-2026-41479 and CVE-2026-44681 (unauthenticated open redirect in authorization endpoint of Authlib *server* grants). Relevance depends on use: app uses Authlib as OIDC **client**; the open-redirect pair affects server-side grants (likely not reachable, informational) - the CSRF one applies only if the `cache=` parameter is used (check `routers/auth.py`). Upgrade to >=1.6.12 regardless (reachable by pip-audit and trivy). |
| TL-3 | Medium (low exposure) | `backend/requirements.txt:8`, `ocr-service/requirements.txt` `pydantic-settings==2.13.1` | CVE-2026-58203 / GHSA-4xgf-cpjx-pc3j: `NestedSecretsSettingsSource` follows symlinks out of `secrets_dir` when `secrets_nested_subdir=True`. App uses `/run/secrets` file secrets; check whether `secrets_nested_subdir` is set (grep suggests default off => not exploitable). Fix: 2.14.2. |
| TL-4 | Medium | `backend/requirements-test.txt` `python-jose 3.5.0`, `ecdsa 0.19.2` | CVE-2026-85394 (python-jose, no fixed version listed), PYSEC-2026-1325 (ecdsa). Test-only requirements file, not in the runtime image (verified: not in trivy image scan). Informational unless prod code imports jose; check `grep -r jose backend/app`. |
| TL-5 | Low | `chart/templates/statefulset-postgresql.yaml:22-35` | Pod securityContext has no `runAsNonRoot`/`runAsUser` at pod level (container-level `runAsNonRoot: true` present, so OK); no `capabilities.drop: [ALL]` visible on postgres container (backend/frontend/ocr do drop ALL). Trivy KSV-0020/0021. |
| TL-6 | Low | `docker-compose.yaml` (see section 9) | Dev compose: default creds `admin/admin123`, `zaehlwart/zaehlwart`, JWT secret literal, `DEBUG=true`, backend 8000 and ollama 11434 published on 0.0.0.0, no `read_only`/`cap_drop`/`no-new-privileges`, `ollama:latest`. Dev-only, but guarded in app by config validator for the default JWT/DB sentinel only (admin123 is not rejected). |
| TL-7 | Low | `.github/workflows/*.yaml` | Actions pinned to mutable tags (`@v4`, `@v5`, `@v6`, `aquasecurity/trivy-action@v0.36.0`, `gitleaks/gitleaks-action@v3`), no SHA pinning; `test.yaml` has no `permissions:` block (inherits repo default token scope); `build.yaml` jobs have job-level minimal permissions (good). |
| TL-8 | Info | `frontend` devDependencies | `npm audit`: 10 vulns (3 critical, 5 high, 2 moderate), **all dev-only** (vitest/@vitest/ui, tinypool, vite, esbuild, braces/micromatch/fast-glob, brace-expansion); `npm audit --omit=dev`: **0 vulnerabilities**. Not shipped (frontend image = static files only). Dev-server issues only matter if `vitest --ui`/`vite dev` is exposed. |
| TL-9 | Info | Container images | Backend/ocr images built from a stale locally cached `python:3.12-slim` (created 2026-03-16) so OS-package counts (backend: 6 CRIT/107 HIGH; ocr: 8 CRIT/128 HIGH, Debian 13.4) are inflated; most have fixes in current Debian point releases -> rebuild with `--pull` / base-image refresh and rescan. Frontend image (static-web-server, scratch-like): 0 vulns, no OS. Python layer of backend: only the authlib/pydantic-settings items above plus `pip 25.0.1` (MEDIUM, build tool, not used at runtime) and `Mako 1.4.1` (CVE-2026-102991, via alembic; only used for migration templates). |

No hard-coded real secrets found in tree or git history (see 6). No High/Critical SAST findings.

## 1. bandit 1.9.x (backend/app, ocr-service/app)

- Default run on `backend/app`: **0 issues** (3007 LOC), 8 potential issues suppressed with `# nosec`.
- `--ignore-nosec` triage of all suppressed hits (+ ocr-service):

| Hit | Location | Verdict |
|-----|----------|---------|
| B105 hardcoded password `'change-me-in-production'` (Low) | `backend/app/config.py:98` | False positive: sentinel compared against to **reject** the default; startup fails if left. |
| B608 SQL string building (Medium/Low conf) x2 | `backend/app/jobs/retention.py:109, :125` | False positive after review: f-string interpolates only `where` built from static clause strings; all values bound via `params`. Residual: fragile pattern, keep clauses static. |
| B110 try/except/pass x3 | `backend/app/main.py:141, :152, :166` | Informational: best-effort shutdown/startup paths; swallow errors silently (observability only). |
| B110 | `backend/app/services/ocr_pipeline.py:88`, (`llm_client.py:234` nosec) | Informational: EXIF read best-effort, documented in comment. |
| B106 hardcoded password funcarg `'bearer'` (Low) | `backend/app/routers/auth.py:185` | False positive: OAuth `token_type`. |
| B104 bind all interfaces (Medium) | `ocr-service/app/config.py:11` | False positive/by design: container must listen on 0.0.0.0 (comment in source); exposure is controlled by compose/NetworkPolicy. Note: ocr-service has no authentication (documented). |

CI already runs bandit (`test.yaml` `backend-sast`, `ocr-service-sast`) without `--ignore-nosec`.

## 2. pip-audit (backend/requirements.txt, ocr-service/requirements.txt, requirements-test.txt)

- backend: 5 advisories in 2 packages: `authlib 1.6.9` (PYSEC-2026-25 / CVE-2026-41425 -> 1.6.11; PYSEC-2026-188 / CVE-2026-44681 -> 1.6.12; PYSEC-2026-2119 / CVE-2026-41479 -> 1.6.10), `pydantic-settings 2.13.1` (CVE-2026-58203 -> 2.14.2). Triage: TL-2, TL-3.
- ocr-service: pydantic-settings only (TL-3).
- requirements-test.txt: python-jose 3.5.0 (CVE-2026-85394), ecdsa 0.19.2 (PYSEC-2026-1325). TL-4.

## 3. Frontend: eslint-plugin-security + npm audit

- `npm run lint:security` (eslint 10, eslint-plugin-security 4 already configured in `frontend/eslint.config.js`, CI step present): **0 errors, 5 warnings**, all `security/detect-object-injection`:
  `src/components/BulkImport.vue:446, :468, :573, :601`; `src/views/Dashboard.vue:186`. Triage: false-positive class (array/object indexing by loop index or internal key; no prototype-pollution sink, keys not user-controlled strings). Informational. Not individually re-verified for BulkImport keys derived from file/CSV content - worth a quick look by whoever does the frontend review (indexing an object by CSV header text would be a prototype pollution risk only on writes to `{}`).
- `npm audit`: see TL-8. No production dependency vulnerabilities.

## 4. trivy fs (vuln + secret + misconfig), repo (node_modules excluded)

- Vulns: the same authlib/pydantic-settings items (MEDIUM). Nothing HIGH/CRITICAL (trivy ignores npm devDependencies by default, so the dev-only npm items are absent; CI `exit-code 1` on HIGH/CRITICAL therefore stays green).
- Secrets: none.
- Misconfig: `frontend/Dockerfile` DS-0002 HIGH "image user is root" (final stage inherits static-web-server default root; **mitigated** in chart by `runAsUser: 65534`, `runAsNonRoot`, read-only root fs, drop ALL; but in compose/`podman run` it runs as root - real but low; add `USER 65534` or document). DS-0026 (no HEALTHCHECK) LOW x3: informational (k8s probes used). Chart: KSV-0011 CPU limits, KSV-0020/0021 UID/GID <=10000 (backend uses 1000, postgres), KSV-0110 default namespace, KSV-0125 untrusted registry (ghcr/docker.io): all informational/false positive for a Helm chart that installs into the release namespace.

## 5. hadolint (all three Dockerfiles)

- DL3008 (pin apt versions) `backend/Dockerfile:9`, `ocr-service/Dockerfile:9`: Informational (trade-off with security updates).
- DL3025 `backend/Dockerfile:25`: CMD in shell form - required for `alembic upgrade head && uvicorn`; side effect: PID 1 is `sh`, SIGTERM not forwarded to uvicorn (graceful shutdown/scheduler stop). Low; use `exec uvicorn` or entrypoint script.
- DL3066 non-numeric USER (`backend:20`, `ocr:21`): Informational (`USER appuser`; chart sets numeric 1000 anyway).
- `frontend/Dockerfile`: clean. Base images not digest-pinned (`python:3.12-slim`, `node:22-alpine`, `static-web-server:2`): Informational (Renovate-style pinning would help).

## 6. gitleaks (docker.io/zricethezav/gitleaks:latest; `zricks/gitleaks` does not exist)

- Git history (fresh clone, 81 commits, all branches in clone): **no leaks found**.
- Working tree incl. untracked (`--no-git`): 4 `generic-api-key` hits, all in `security/rig/.state/rig.env` lines 5, 7, 11, 13 (values redacted). File is **gitignored** (`.gitignore:38 security/rig/.state/`), untracked, generated by the test rig with throw-away values: false positive / not committed. Keep the ignore rule; do not `git add -f`.
- Note: `.github` workflow uses `gitleaks-action@v3` (requires `GITLEAKS_LICENSE` for organisation repos; fine for a personal repo).

## 7. Helm: `helm lint` / `helm template` / kubeconform

- `helm lint chart`: 0 failed (only "icon is recommended").
- `helm template` renders 10 resources; kubeconform `-strict`: 8 valid, 0 invalid, 2 skipped (Traefik IngressRoute/Middleware CRDs without schema).
- Manual hardening review: backend/frontend/ocr pods: `runAsNonRoot`, seccomp RuntimeDefault, `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, `drop: [ALL]`: good. NetworkPolicy disabled by default (documented; TL note: default-open pod network, recommend enabling in prod values - the k3s-gitops overlay should be checked separately). Secrets only via `existingSecret`, no inline secret values. ServiceAccount token automount not disabled (`automountServiceAccountToken` not set): Low, none of the pods use the API.

## 8. Image build + trivy image (podman)

- Built `backend`, `frontend`, `ocr-service` successfully (ocr pre-downloads EasyOCR models at build). Runtime check: backend runs as `uid=1000(appuser)`.
- backend: Debian 13.4 packages 6 CRITICAL / 107 HIGH (openssl CVE-2026-31789, perl-base x3 etc.; fixes exist in `deb13u*`; base image is stale locally, see TL-9); Python: authlib, pydantic-settings, Mako, pip (see above). No secrets in image.
- frontend: no OS packages, 0 vulns, 0 secrets; runs as root unless overridden (DS-0002).
- ocr-service: Debian 13.4 8 CRITICAL / 128 HIGH (same stale base); Python layer 0 HIGH/CRITICAL (torch/easyocr included).
- Recommendation: add a Trivy image scan job (CI today only scans the filesystem) and rebuild with `--pull`.

## 9. docker-compose.yaml review

- No `privileged`, no host network, no docker.sock mount. Good.
- Ports on 0.0.0.0: `11434` (ollama, unauthenticated; exposes model API to the LAN), `8000` (backend, bypasses proxy), `8081` (proxy). Recommend `127.0.0.1:` binding for 11434 and 8000.
- Plain credentials in env: `POSTGRES_PASSWORD: zaehlwart`, `DATABASE_URL` with password, `JWT_SECRET_KEY: local-dev-secret-change-in-prod`, `SUPERADMIN_PASSWORD: admin123`, `DEBUG: "true"`. Dev defaults only, but `admin/admin123` and `DEBUG=true` are not rejected by the config validator (only `JWT_SECRET_KEY` sentinel and default DB URL are), so a copy-paste deployment would run with a known admin password.
- Hardening gaps (all services): no `read_only`, `cap_drop: [ALL]`, `security_opt: no-new-privileges`, resource limits; `ollama/ollama:latest`, `nginx:alpine` unpinned; frontend container runs as root; GPU devices passed to ocr/ollama (needed). `db` not published (good). Compose file is named `docker-compose.yaml` (task text said `.yml`).

## 10. GitHub workflows (`.github/workflows/build.yaml`, `test.yaml`)

- Triggers are `push`/`pull_request` (not `pull_request_target`): fork PRs get no secrets. Good.
- `build.yaml`: `permissions: contents: read, packages: write` at job level, login skipped on PRs, `GITHUB_TOKEN` passed via env/stdin (not echoed in args beyond `echo "${{ secrets.GITHUB_TOKEN }}" | helm registry login --password-stdin`: expression is expanded into the shell script text; low risk, prefer an `env:` var). Helm push only on tags.
- Unpinned actions by tag only (TL-7). `test.yaml`: no `permissions:` key (set `contents: read` at top level). `test.yaml` uploads `coverage.xml` artifact (no secrets). Test env secrets are literal CI dummies (`ci-test-secret-key-not-for-production`): false positive.
- CI includes bandit, eslint-security, trivy fs, gitleaks already; missing: pip-audit/npm audit, image scan, hadolint, kubeconform/helm lint, dependabot/renovate for actions.
