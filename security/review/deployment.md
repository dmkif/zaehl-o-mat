# Manual review: deployment and operations (T030)

Scope: `backend/Dockerfile`, `frontend/Dockerfile`, `ocr-service/Dockerfile`, `chart/`, `docker-compose.yaml`, `nginx.conf`, `.github/workflows/{build,test}.yaml`, `.pre-commit-config.yaml`, `backend/alembic`, `backend/scripts/`, `scripts/`, README claims. Status: F-candidates from reading only.

## F-candidates

### DEP-01 Compose stack ships weak defaults and exposes backend and Ollama directly
- OWASP 2025: A02 Security Misconfiguration
- Component: `docker-compose.yaml`
- Severity guess: Medium (High if used beyond a local laptop)
- Location: lines 21 (`11434:11434`), 52 (`8000:8000`), 57 (`JWT_SECRET_KEY: local-dev-secret-change-in-prod`), 58-59 (`admin`/`admin123`), 63 (`DEBUG: "true"`), 6-8 (db password `zaehlwart`), 18 (`ollama/ollama:latest`)
- Description: (1) the backend startup guard (`backend/app/config.py:93-100`) rejects only the exact sentinel `change-me-in-production` and the localhost DB URL, so the Compose secret `local-dev-secret-change-in-prod` and `admin123` pass silently; there is no minimum strength/length check on `JWT_SECRET_KEY` or `SUPERADMIN_PASSWORD`. (2) Ports are published on all interfaces: Ollama (unauthenticated model API: free GPU use, model pull/delete) and the backend (bypasses the nginx proxy, its headers, `client_max_body_size`). (3) `DEBUG=true` enables DEBUG logging (may log request details) and adds `http://localhost:5173` to CORS. (4) README states the default login `admin / admin123`.
- Impact: anyone on the LAN can use Ollama; anyone who knows the README can mint a superadmin JWT, and forge any JWT if the Compose secret is kept (HS256 with a publicly documented key).
- How to confirm: `docker compose config`; start with defaults and `curl :11434/api/tags`; sign a JWT with the Compose key and call `/api/auth/me`.
- Recommendation: bind to `127.0.0.1:` (or drop `ports` for ollama/backend), use an `.env` file with required variables (`${JWT_SECRET_KEY:?}`), reject secrets < 32 chars and known defaults in `Settings`, set `DEBUG` false, pin the image tags.
- Confidence: high

### DEP-02 Rate limiter keyed on spoofable `X-Forwarded-For` (`--forwarded-allow-ips='*'`)
- OWASP 2025: A07 Authentication Failures / A02
- Component: `backend/Dockerfile:23-26`, `backend/app/limiter.py:4`, `nginx.conf:40`
- Severity guess: Medium (depends on ingress)
- Description: uvicorn trusts `X-Forwarded-For` from any peer. With `*` uvicorn takes the left-most XFF entry, which is client-controlled when the proxy appends (`nginx.conf` uses `$proxy_add_x_forwarded_for`, which appends to a client-supplied value). `slowapi.get_remote_address` then keys the 5/min superadmin limit and the 4-20/min OCR limits on that value. Direct access to published port 8000 (DEP-01) is also unproxied. Traefik by default overwrites XFF from untrusted sources, so the Kubernetes path is likely safe (not verifiable here). The limiter is in-memory per process (documented, replicas=1).
- Impact: brute force of `/api/auth/superadmin-login` and unbounded OCR (cost/GPU) by rotating a fake XFF.
- How to confirm: 20 requests to `superadmin-login` through the proxy with changing `X-Forwarded-For: 1.2.3.<n>`; expect no 429. Experiment cross-reference: A07 brute-force test.
- Recommendation: set `--forwarded-allow-ips` to the proxy/ingress CIDR, have nginx use `proxy_set_header X-Forwarded-For $remote_addr`, add per-username throttling in addition to per-IP.
- Confidence: medium-high

### DEP-03 Floating base images and unpinned build inputs; no digest pinning anywhere
- OWASP 2025: A03 Software Supply Chain
- Severity guess: Medium
- Location: `backend/Dockerfile:1`, `ocr-service/Dockerfile:1` (`python:3.12-slim`), `frontend/Dockerfile:1` (`node:22-alpine`), `:9` (`static-web-server:2`), `docker-compose.yaml:3,18,82` (`postgres:18-alpine`, `ollama:latest`, `nginx:alpine`), `chart/values.yaml` postgresql `18-alpine`
- Description: only moving tags, no `@sha256:`. `pip install -r requirements.txt` has exact `==` pins for direct deps but no hashes and no lock for transitive deps (fastapi/starlette, httpx deps, pydantic-core, easyocr -> torch). `apt-get install` unversioned (normal). OCR image downloads EasyOCR models at build time from the internet with no checksum (`ocr-service/Dockerfile:24-26`), and pulls torch transitively.
- Impact: non-reproducible builds, silent base-image/dependency drift, wider window for a compromised upstream tag.
- How to confirm: `docker buildx imagetools inspect` for tag digests over time; `pip install --dry-run` shows transitive versions changing.
- Recommendation: pin by digest and let Renovate/Dependabot update them; use `pip-compile --generate-hashes`; verify model checksums.
- Confidence: high

### DEP-04 No image HEALTHCHECK, no `.dockerignore`, frontend image/compose container runs as root
- OWASP 2025: A02
- Severity guess: Low
- Location: all three Dockerfiles (no `HEALTHCHECK`), no `.dockerignore` in `backend/`, `frontend/`, `ocr-service/`; `frontend/Dockerfile:9-17` has no `USER`
- Description: Compose has no healthcheck for backend/frontend/ocr (only db) so `restart: unless-stopped` cannot detect hangs; k8s probes exist in the chart. `COPY . .` copies tests, `.env`, local `node_modules`, caches, scripts into the build context and (backend/ocr) into the image layer. The static-web-server image has no non-root user set; Compose runs it as root (chart overrides with uid 65534). Backend and OCR use a non-root `appuser` (good); the UID is implicit (first adduser = 1000) while the chart hardcodes `runAsUser: 1000`, which breaks silently if the base image changes.
- Impact: low; a local `.env` with real secrets would be baked into `backend` image layers if present on the build host.
- How to confirm: `docker build` then `docker run --rm img ls -la /app`; `docker inspect` for `Config.User`.
- Recommendation: add `.dockerignore` (`.env*`, `tests`, `node_modules`, `.git`, `__pycache__`), explicit `useradd -u 1000`, `USER 65534` or `--user` in Compose for the frontend, HEALTHCHECK or Compose `healthcheck`.
- Confidence: high

### DEP-05 Migrations run automatically at container start with the application DB account
- OWASP 2025: A02 / A08
- Location: `backend/Dockerfile:25` (`alembic upgrade head && uvicorn ...`)
- Severity guess: Low
- Description: every pod start executes DDL using the runtime `DATABASE_URL`, so the app account needs DDL rights permanently and a crash loop on a failing migration takes the API down. Migration `0004_remove_ldap.py` drops a table and two columns irreversibly for data (downgrade restores schema only). Multiple replicas would race (chart pins replicas to 1). Migrations use only the op API (no raw SQL, no string-built SQL found in `versions/`); `env.py` reads `DATABASE_URL` from env, no credentials in `alembic.ini` (check below).
- Impact: SQL injection or app compromise has schema-altering privileges; data loss on 0004 if the dropped column held anything.
- How to confirm: `psql \du` privileges of the app role; read `alembic.ini` `sqlalchemy.url`.
- Recommendation: separate migration Job/initContainer with a privileged role; app role DML only.
- Confidence: medium

### DEP-06 NetworkPolicy disabled by default and, when enabled, ingress source is "any pod in any namespace"; no egress policy
- OWASP 2025: A02
- Component: `chart/values.yaml` (`networkPolicy.enabled: false`), `chart/templates/networkpolicy.yaml:35-42,58-65`
- Severity guess: Medium
- Description: default off. When enabled, backend and frontend policies take `namespaceSelector` and `podSelector` from `networkPolicy.ingressFrom`, whose default is `{}`/`{}`, i.e. `from: [{namespaceSelector: {}, podSelector: {}}]` = every pod in the cluster. Intended to be set to the Traefik namespace, but the default is permissive and undocumented in the chart. No `Egress` policies at all: the backend (processes untrusted images, calls OIDC, gateway, oil-price URL) can reach any in-cluster service and the Internet; the PostgreSQL pod, OCR pod likewise. The postgres policy is correct (backend only). The postgres pod mounts a default ServiceAccount token (`statefulset-postgresql.yaml` sets no `serviceAccountName`/`automountServiceAccountToken: false`). (This cluster's CLAUDE.md notes NetworkPolicy enforcement was missing on some nodes until 2026-09; verify enforcement.)
- Impact: lateral movement and exfiltration from a compromised pod are unrestricted; the OCR service has no auth (README) and relies purely on this policy.
- How to confirm: `helm template --set networkPolicy.enabled=true | kubectl apply --dry-run=server`; from a pod in another namespace `curl http://<release>-ocr:8100/health` (expect timeout when correctly configured).
- Recommendation: default `ingressFrom` to required values, add egress rules (DNS, postgres, gateway/OIDC), `automountServiceAccountToken: false` on the postgres pod.
- Confidence: high for the template logic, medium for live behavior

### DEP-07 Chart images unpinned by digest, mutable tags with `IfNotPresent`; branch-name tags published
- OWASP 2025: A03 / A08
- Location: `chart/values.yaml` image blocks; `chart/templates/_helpers.tpl:40-56`; `build.yaml:43-47`
- Severity guess: Low
- Description: chart tag defaults to `appVersion` (`0.2.0`), semver tags are immutable only by convention; CI also pushes mutable `main` and `sha-` tags. `pullPolicy: IfNotPresent` with a re-pushed tag leaves nodes with different content. No image signing (cosign), no SBOM/provenance attestations, no Trivy scan of the built images (only `fs` scan).
- Impact: integrity of what runs in the cluster cannot be verified against what CI built.
- Recommendation: digest pinning in the GitOps repo, `cosign sign` + `provenance: true` in `build-push-action`, Trivy image scan before push.
- Confidence: high

### DEP-08 GitHub Actions pinned to mutable tags; test workflow has no `permissions:`
- OWASP 2025: A03 / A08
- Location: `.github/workflows/test.yaml` (no top-level `permissions`), all `uses:` lines (`actions/checkout@v4`, `docker/*@v3-v6`, `azure/setup-helm@v4`, `aquasecurity/trivy-action@v0.36.0`, `gitleaks/gitleaks-action@v3`); `build.yaml:116`
- Severity guess: Medium (supply chain), Low (permissions)
- Description: no commit-SHA pinning, including third-party security actions that run with the job token (gitleaks gets `GITHUB_TOKEN`). `test.yaml` declares no `permissions`, so the token gets the repository default (write-all if the repo default was never tightened; not verifiable). `build.yaml` correctly scopes `contents: read, packages: write`, but the `packages: write` build job also runs on `pull_request` (login step is skipped, push is false - good). `${{ secrets.GITHUB_TOKEN }}` is interpolated in a `run:` (`build.yaml:116`) rather than passed via env (low risk, token is masked). No `pull_request_target` or `workflow_run` triggers, no `${{ github.event.* }}` injection in `run:` steps (checked: only `GITHUB_REF_NAME`, `github.actor` via safe contexts), so no untrusted-input injection found. `pip install --upgrade pip`/unpinned `pytest httpx` in `test.yaml:217-218`; `azure/setup-helm` unpinned Helm version. No Dependabot/Renovate config in `.github/`. `helm dependency update` in a chart without dependencies is a no-op.
- Impact: a hijacked action tag can run code with repo secrets/packages-write token; supply chain to published images.
- How to confirm: `gh api repos/dmkif/zaehl-o-mat/actions/permissions/workflow`.
- Recommendation: pin `uses:` to SHAs (Renovate `helpers:pinGitHubActionDigests`), add `permissions: contents: read` at workflow top in `test.yaml`, enable branch protection already described in README.
- Confidence: high

### DEP-09 CI security scanning gaps (coverage vs. README claim)
- OWASP 2025: A03 / A02
- Location: `test.yaml:272-286` (Trivy fs), `.pre-commit-config.yaml`, README "Contributing"
- Severity guess: Low
- Description: Trivy runs only `scan-type: fs` with default scanners, `ignore-unfixed: true`, severity HIGH/CRITICAL: no image scan, and config/misconfig scan of Dockerfiles and the Helm chart is not enabled (`scanners: vuln,secret,misconfig` not set). No `helm lint`/`helm template | kubeconform` step, no `pip-audit`, no `npm audit`. README says "image build" is a required check, but `build.yaml` has no branch-protection linkage visible in the repo.
- Recommendation: add `scanners: vuln,secret,misconfig`, `trivy image` on built images, `helm template` + kubeconform.
- Confidence: high

### DEP-10 Unauthenticated OCR service; relies entirely on network isolation
- OWASP 2025: A02 / A07
- Location: `ocr-service/app/main.py:1-9`, `docker-compose.yaml:38-41`, `chart/templates/deployment-ocr.yaml`, README "OCR Service"
- Severity guess: Low-Medium (reachable only inside the cluster/compose network)
- Description: documented, deliberate (no auth). Compose does not publish the port (good). In the chart protection is the NetworkPolicy, which is off by default (DEP-06). Accepts any image up to the limit and runs EasyOCR/torch (CPU/GPU DoS). Input is magic-byte validated and stored under a random per-process tmp dir (good). No temp-file cleanup was checked.
- How to confirm: from another namespace post an image to `/scan`; check tmp growth.
- Recommendation: enable NetworkPolicy by default when `ocr.enabled`; optional shared-secret header check.
- Confidence: medium

### DEP-11 Operational scripts contain hard-coded credentials, internal IPs and host-key bypass
- OWASP 2025: A02 / A07
- Location: `scripts/test_ocr.sh:5`, `scripts/test_ocr2.sh:5` (`admin` / `admin123`, `localhost:8080`, `/home/dmkif/...` paths); `scripts/benchmark_ollama.py:155,701,783,791` (`StrictHostKeyChecking=no`, `root@172.16.50.98`, internal endpoint defaults); committed `scripts/benchmark_results.json` (benchmark output about the sample meters, no secrets seen)
- Severity guess: Low
- Description: the default credential matches the documented Compose one (DEP-01); gitleaks does not flag it. SSH to a GPU host as root with host-key checking off allows MITM on the LAN. `backend/scripts/seed_oil_prices.py` uses `sqlalchemy.text()` with bind parameters and a fixed data set (checked), reads `DATABASE_URL` from env, no injection. `scripts/ocr_gateway_eval.py` reads the gateway key from env and does not print it (good).
- Recommendation: read credentials from env, `StrictHostKeyChecking=accept-new`, move dev tooling out of the shipped repo root or mark clearly.
- Confidence: high

### DEP-12 Documentation does not match reality
- OWASP 2025: A02 (misleading operators), low
- Location: `README.md:30` ("http://localhost:8080") vs Compose proxy `8081:80` (`docker-compose.yaml:88`) and scripts using `:8080`; `README.md:103` says redirect URI `https://<domain>/auth/callback` with hash routing `/#/auth/token`, but actual: `OIDC_REDIRECT_URI` must be `.../api/auth/callback` (`config.py:36-39`, Compose value) and the router uses `createWebHistory()` with `/auth/token` (`frontend/src/router/index.ts:5`, `auth.py:151-154`); README "Default superadmin credentials" documents `admin123`; `chart/templates/middleware-security-headers.yaml:2` comment claims parity with the Compose proxy but the sets differ (FE-03). A mis-registered redirect URI at the IdP (following the README) fails closed (login breaks), but wildcard registrations to "fix" it would be dangerous.
- Recommendation: correct README, mention the secrets requirements.
- Confidence: high

### DEP-13 Chart secrets handling relies on out-of-band Secrets; HSTS preload default
- OWASP 2025: A02
- Location: `chart/values.yaml` (`backend.existingSecret`, `postgresql.auth.existingSecret: zaehl-o-mat-db-secret`), `chart/templates/deployment-backend.yaml:42-44`, `middleware-security-headers.yaml:12-15`
- Severity guess: Info
- Description: positive: no Secret objects or credentials in templates/values; the backend refuses to start with sentinel defaults. Notes: all Secret keys are injected as env via `envFrom` (visible in `kubectl describe`/`/proc`; the `secretVolume` file mode exists as alternative); the DB password must be kept consistent between two Secrets by the operator; HSTS `preload` + `includeSubdomains` is set unconditionally, which on a shared parent domain can lock out sibling hosts and is hard to retract; `ingress.tls: false` still serves the `websecure` entrypoint with Traefik's default certificate.
- Confidence: medium

## Checked, no finding

### A02 Security misconfiguration
| Check | Evidence |
|---|---|
| Containers non-root (chart) | backend `runAsNonRoot: true, runAsUser: 1000, fsGroup 1000`, frontend `65534`, ocr `1000`, postgres `999`, all with `seccompProfile: RuntimeDefault` (`deployment-*.yaml`, `statefulset-postgresql.yaml:80-95`) |
| Container hardening | `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, `capabilities.drop: [ALL]` on every container including postgres; writable paths via `emptyDir` (`/tmp`, `/var/run/postgresql`) and PVC (`/data/uploads`) |
| ServiceAccount | `automountServiceAccountToken: false` on the chart SA (`serviceAccount.yaml:10`); app never calls the API. (Exception: postgres pod, DEP-06.) |
| Resource limits | memory requests/limits set for backend (1Gi), frontend (64Mi), ocr (2Gi), postgres (1Gi); no CPU limits (deliberate/common) |
| Probes | backend startup/liveness/readiness, frontend, ocr and postgres probes exist; liveness deliberately independent of DB |
| Services | all `ClusterIP`; postgres headless; no NodePort/LoadBalancer |
| Ingress routing | `/api` -> backend, rest -> frontend, both through the security-headers middleware; entry point `websecure` only (no plain-HTTP route in chart) |
| Docker non-root | backend and OCR `USER appuser` after install; no secrets or `ARG` credentials in any Dockerfile layer; multi-stage frontend build (final image has only `dist`) |
| Default secrets guard | `config.py:93-100` refuses sentinel `JWT_SECRET_KEY` and default DB URL; empty `SUPERADMIN_PASSWORD` disables the local login (`auth.py:165-167`); constant-time compares |
| CORS | origins limited to `app_base_url` (+ localhost:5173 only in debug), `main.py:74-86`; note `allow_credentials=True` with `allow_methods/headers=*` is acceptable with a single origin |
| Compose internal services | db and ocr have no published ports; proxy is the only intended entry (but see DEP-01 for backend/ollama) |
| `/api/docs` | enabled in prod (`main.py:67-69`), not blocked at ingress: public API schema; Swagger UI loads assets from a CDN so it fails under the CSP. Info only; consider disabling `docs_url`/`openapi_url` when `debug` is false. |

### A03 Software supply chain
| Check | Evidence |
|---|---|
| Python direct deps pinned | `backend/requirements.txt`, `ocr-service/requirements.txt` all `==` (hashes/transitives missing: DEP-03) |
| Frontend lockfile + `npm ci` | `frontend/package-lock.json` present; used in Dockerfile and CI |
| Secret scanning | gitleaks in CI (`test.yaml:288-300`, full history `fetch-depth: 0`) and pre-commit; `git ls-files | grep -i env\|secret\|pem\|key` shows only `alembic/env.py` and `env.d.ts`; `.env` is git-ignored |
| SAST | bandit 1.9.4 pinned (CI + pre-commit), eslint-plugin-security |
| Vulnerability scan | Trivy fs HIGH/CRITICAL with `exit-code: 1` (scope limits: DEP-09) |
| Workflow triggers | only `push`/`pull_request`/tags; no `pull_request_target`, no `workflow_run`, no self-hosted runners, no secrets other than the implicit `GITHUB_TOKEN`; no untrusted `${{ }}` expression inside `run:` |
| Published chart | built from the tagged commit only; `packages: write` only on build/helm jobs |

### A08 Software and data integrity
| Check | Evidence |
|---|---|
| Image push gated | login and push skipped for `pull_request` (`build.yaml:31,53`); chart push only on `v*` tags and after `build` |
| Alembic migrations | five linear revisions (0000-0004), op-API only, no dynamic SQL, no data-fetching from URLs, `down_revision` chain consistent, `env.py` reads URL from env |
| Seed script | `backend/scripts/seed_oil_prices.py`: parameterised SQL, `ON CONFLICT DO NOTHING`, fixed constants |
| Untrusted LLM/OCR output | out of scope here (backend experiments, A08 rows in CHECKLIST) |
| Artifact integrity | not verified: no signatures/attestations (DEP-07) |

### A05 / A07 / A09 (deployment facets)
| Check | Evidence |
|---|---|
| A05 injection in ops code | `scripts/*.py` use `subprocess.run` with list args (no `shell=True`) (`benchmark_ollama.py:152-159`); seed script bind params; shell scripts use fixed commands |
| A07 default accounts | none in chart; only Compose/README (DEP-01) |
| A09 logging config | `httpx`/`httpcore` forced to WARNING so request URLs are not logged (`main.py:20-22`); `DEBUG` false by default in chart values (`backend.env.DEBUG: "false"`); nginx adds `X-Request-ID` for correlation; no access-log tuning though (FE-01 interaction) |
| Chart default-deny posture for secrets | no `Secret` kind rendered by the chart (`grep -rn "kind: Secret" chart/` empty) |

## Not verifiable from the repository
- Live cluster state: whether the NetworkPolicy is enabled and enforced (CNI/ipset), pod-security admission level, actual image digests/tags deployed, `kubectl get sa` token automount on postgres, RBAC.
- Real `values` used by the GitOps repo (`k3s-gitops`): secrets provisioning (OpenBao/ESO), `networkPolicy.ingressFrom`, `JWT_SECRET_KEY` strength, `SUPERADMIN_PASSWORD` set or empty, `DEBUG`.
- Ingress/Traefik behavior: XFF overwrite and `forwardedHeaders.trustedIPs`, TLS versions, whether the middleware is attached to all routers, access-log format (query string retention), whether the backend Service is reachable from outside via other routes (e.g. `/api/docs`).
- IdP (Authentik) configuration: redirect-URI strictness, token/session lifetimes, group claim mapping, PKCE enforcement, MFA.
- GitHub repository settings: default workflow token permissions, branch protection and required checks, secret-scanning/push-protection, Dependabot alerts, who can create `v*` tags, GHCR package visibility.
- PostgreSQL: TLS between backend and DB, role privileges, `pg_hba`, backup/retention and encryption at rest (Longhorn).
- Ollama/gateway/Lemonade hosts and their authentication; the unauthenticated Ollama on the LAN (172.16.x) referenced by the scripts.
- Whether `axios` is really unused and which of the transitive Python/npm packages currently carry advisories (requires `pip-audit`/`npm audit`; Trivy result from CI not available here).
