# Research: Full Security Review

Sources: repository layout (8 routers, ~35 endpoints, `backend/tests/conftest.py` using SQLite in memory), `docker-compose.yaml`, chart templates, `.github/workflows/test.yaml` (bandit, Trivy, gitleaks, tests), project constitution (A01–A10), feature 003 research (string-claim finding).

## R1 — Why a real database for the experiments

**Decision**: The rig runs PostgreSQL 18 in a container; the experiments never use the SQLite fixtures of the unit suite.
**Rationale**: SQLite in-memory with `StaticPool` serialises access and has different locking and constraint behavior; first-sign-in races (`IntegrityError` retry path), duplicate-hash checks and transaction isolation only show on PostgreSQL.
**Alternatives**: testcontainers (not available without Docker); mocking races (proves nothing).

## R2 — OWASP Top 10 (2025) → concrete checks

| Category | Review (reading/tools) | Experiments |
|---|---|---|
| A01 Broken Access Control | every route's dependency chain (`get_current_user`, `require_property_access`, role checks), file serving `uploads`, SSE `bulk-scan` | per-endpoint role × property matrix, IDOR by guessing UUIDs/filenames, path traversal on uploads, mass assignment on PATCH |
| A02 Security Misconfiguration | defaults (`admin`/`admin123`, `change-me`), CORS, headers middleware, `debug`, container user/rootfs, chart securityContext, Compose ports | start with default secrets (must refuse), response headers, error pages, `/docs` exposure, CORS preflight |
| A03 Software Supply Chain | pinning in requirements/package-lock/Dockerfiles/workflows, base images, chart/OCI | `pip-audit`, Trivy fs + image, gitleaks, lockfile drift |
| A04 Cryptographic Failures | password hashing, JWT alg/exp/secret strength, PKCE, TLS at ingress, cookie/token storage in frontend | `alg=none`/HS-RS confusion, expired/forged/tampered tokens, weak secret rejection |
| A05 Injection | ORM-only SQL, raw SQL grep, shell/subprocess, template/HTML sinks in Vue (`v-html`), log forging | SQL/JSON/header/filename injection payloads in every field, stored XSS round trip, CRLF in values |
| A06 Insecure Design | threat model per feature (auth, property access, uploads, AI calls), business-logic limits | race and logic experiments: duplicate readings/hash, first-login, concurrent role changes, negative/huge values, replay of OIDC `state` |
| A07 Authentication Failures | OIDC/PKCE flow, superadmin login, rate limiting, token lifetime/logout | brute force against `superadmin-login` (limit holds under parallel requests), state/PKCE reuse, token after logout |
| A08 Software/Data Integrity | trust in LLM/OCR output, image/chart integrity, CI pinning | malformed/hostile gateway and OCR responses, serial-equals-reading guard, oversize/nested JSON |
| A09 Logging & Alerting | which events are logged, secrets in logs | run the suite with DEBUG and grep logs for tokens/codes/secrets/e-mail; assert security events exist |
| A10 Exceptional Conditions | error handling paths, leaking details | kill database/OIDC/gateway/OCR mid-request: clean error, no partial writes, no stack traces to client |

**Decision**: This table is the review checklist (`security/CHECKLIST.md`); each row becomes experiment IDs in the catalogue.

## R3 — Rig design

**Decision**: `scripts/security-rig.sh` starts (1) `podman run postgres:18-alpine` on a random loopback port with a throw-away volume and password, (2) the backend with `uvicorn` from the project venv against it (migrations via Alembic), (3) three small Python stand-ins: fake OIDC provider (discovery, authorize, token, userinfo with controllable claims including hostile shapes), fake AI gateway (controllable status/latency/body, hostile JSON), fake OCR service; (4) seeds synthetic data; runs `pytest -m security`; tears everything down. Fault injection = stopping/starting the postgres container or switching the stand-ins to error modes.
**Rationale**: No image build needed, reproducible, fast; matches how the app is actually run (uvicorn) and how it talks to its dependencies. **Alternatives**: full Compose stack (podman-compose unreliable here, image build slow); kind/k3s namespace (heavy, not needed for app-level review).

## R4 — Concurrency method

**Decision**: Races are driven by `asyncio` + `httpx.AsyncClient` with a barrier (all requests released at once), N parallel requests, repeated ≥ 100 times; the harness records outcomes (status codes, rows created) and reports a failure rate. Where useful, add a delay in a stand-in to widen the window.
**Rationale**: Gives a number (SC-004) instead of anecdotes.

## R5 — Weakened variants

**Decision**: `security/mutants/*.patch` applied to a temporary copy of the app (git worktree or temp dir) to reintroduce known flaws: remove the property-access check on one endpoint, drop the unique constraint/retry on user creation, accept `alg=none`, disable the upload size check, trust gateway output unvalidated. The rig can run with `--mutant <name>`; the matching experiment must fail.
**Rationale**: SC-006 — proves the experiments can detect problems.

## R6 — Static tooling available locally

**Decision**: bandit and pip-audit from the project venv; trivy, gitleaks and hadolint as containers via podman; `npm audit` and the frontend `eslint-plugin-security`; chart linting with `helm lint`/`helm template` plus kubeconform if available. Findings from tools are triaged (false positives documented), not copied blindly.

## R7 — Severity and triage

**Decision**: Severity = impact × likelihood with the project context (internet-facing ingress, personal data, household scale) → critical/high/medium/low/informational; critical/high fixed in this feature with regression test (fails before, passes after, verified by running the test on the pre-fix commit); the rest tracked in the report with a decision. Reproduction must exist for "confirmed".

## R8 — Seed finding

**Decision**: Feature 003 noted that `get_or_create_user_from_oidc` uses `in` on the claim value; for a string claim this is a substring test. It is the first experiment in A01/A07 (`provider sends "xadminx"` → must not become admin) and a pre-registered finding to confirm and fix.
