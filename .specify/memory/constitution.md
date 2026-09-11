<!--
Sync Impact Report
Version change: 1.1.1 → 1.2.0
Rationale: MINOR — Principle VIII materially expanded, not just reworded.
/speckit-analyze on the OCR-optional-container feature found VIII's text
ambiguous about outbound service-to-service calls (it said "every...
service-to-service call MUST require authentication" with an exception
list containing only inbound endpoints — plan.md was already resolving
this via unstated reinterpretation for the backend→Ollama and
backend→OCR-service calls). This amendment makes the scope explicit:
Principle VIII governs inbound traffic; outbound calls to self-hosted/
operator-controlled optional subsystems SHOULD offer and recommend auth
(mechanism already exists: OLLAMA_API_KEY, and OCR_API_KEY being added by
the in-flight feature); outbound calls to third-party SaaS use whatever
that provider offers, since this project doesn't control it.
Modified principles:
  - VIII. Authenticated Communication by Default — added an explicit
    inbound/outbound scope split with concrete rules for both cases.
Added sections: none
Removed sections: none
Deferred / TODO placeholders: none
Templates requiring follow-up: none checked in this run — dependent
templates/commands read this file at runtime and were not modified here per
scope guard.
-->

# Zähl-O-Mat Constitution

## Core Principles

### I. Test-Backed Changes (NON-NEGOTIABLE)
Every backend behavior change MUST be covered by pytest tests under
`backend/tests/`; every frontend behavior change MUST be covered by the
vitest suite under `frontend/`. CI (`test.yaml`) MUST pass before merge.
Bug fixes MUST include a regression test that fails before the fix and
passes after.
Rationale: the project already runs a real, growing pytest + vitest suite
gating CI; readings, OCR scoring, and RBAC logic are exactly the kind of
code where silent regressions cost users real meter data.

### II. Graceful Degradation for Optional Subsystems
OCR (EasyOCR) and the Ollama vision LLM are optional. The app MUST start
and serve core functionality (properties, meters, manual readings, RBAC,
dashboard) with either or both disabled. `/api/health` MUST report `ocr`
and `llm` status independently and MUST NOT let their unavailability flip
overall `status` to degraded. New optional integrations (alternate OCR
engines, alternate oil-price sources) MUST follow this same pattern:
detect absence of configuration/dependency, skip cleanly, surface status
via health check, never crash the app.
Rationale: this is the established contract (see README "Ollama Vision
Model" and "Health Check" sections) and lets the app run on CPU-only or
GPU-less hosts.

### III. Secure-by-Default Secrets & Access
Secrets (JWT signing key, DB URL, OIDC client secret, API keys) MUST be
readable from environment variables or from `/run/secrets/<name>` files,
never hardcoded or committed. Role checks (`superadmin` / `admin` /
`manager` / `user`) MUST be enforced server-side on every mutating
endpoint — a hidden UI button is not access control. OIDC login MUST use
PKCE. CORS configuration MUST be derived from `APP_BASE_URL`, not
wildcarded in production paths. CI SHOULD run automated secret scanning
(e.g. gitleaks) over the repository so a committed secret is caught before
merge — this is not yet present in `test.yaml` and MUST be added as part
of operating under this principle (tracked jointly with Principle X).
Rationale: matches the existing file-secret pattern for Kubernetes
projected volumes and the recent "operational hardening (CORS, health
probes)" work; multi-tenant property access makes server-side authorization
the only trustworthy enforcement point. Secret scanning closes the one gap
in an otherwise-enforced no-hardcoded-secrets rule: a human mistake
committing a real key.

### IV. Schema Changes via Migrations
All database schema changes MUST go through an Alembic migration
(`backend/alembic/`) committed alongside the code that needs it. No
application code may assume a column/table exists without a corresponding
migration in the same change. `alembic upgrade head` MUST remain safe to
run against a database at any prior migrated state.
Rationale: alembic is already the single mechanism for schema evolution
here; ad hoc schema drift breaks both local dev (`alembic upgrade head`
in README) and Helm-deployed instances.

### V. Deployment Parity (Compose ⇄ Helm)
Any new required environment variable, secret, or service dependency MUST
be added to both `docker-compose.yaml` and the Helm chart (`chart/`,
`chart/values.yaml`), and documented in the README environment-variable
table in the same change. The two deployment paths MUST stay
functionally equivalent.
Rationale: the project ships both a Compose quickstart and a Helm chart
for Kubernetes; letting them diverge silently breaks one of the two
supported install paths.

### VI. Brownfield Respect
Changes MUST follow the existing architecture and conventions — the
FastAPI router/service split (`app/routers/` for HTTP concerns,
`app/services/` for pure logic), Pinia store patterns on the frontend, and
Alembic-only schema evolution (Principle IV). Existing tests MUST keep
passing (Principle I). Breaking changes to APIs or data formats (request/
response shapes, stored `Reading`/`Meter` field semantics) MUST be
explicitly justified in the PR description and MUST include a migration
path for existing deployments and data.
Rationale: this is a live, self-hosted application with real operator
deployments and real stored meter data — "just rewrite it" is not free for
the people running it; deliberate justification keeps that cost visible.

### VII. Security by OWASP Top 10 (2025) (NON-NEGOTIABLE — highest priority)
Every spec, plan, and task MUST be checked against the current OWASP Top
10. A plan touching any relevant category MUST contain a section mapping
that category to concrete controls:
- **A01 Broken Access Control**: deny by default; authorization checked
  server-side on every request, per object and per function (no IDOR/
  BOLA) — extend the existing `require_property_access`/RBAC pattern
  (Principle III) to every new endpoint; outbound requests to
  user-supplied or configured URLs (`OIL_PRICE_API_URL`, `OCR_URL`,
  `OLLAMA_URL`-style settings) MUST be validated against SSRF.
- **A02 Security Misconfiguration**: secure defaults; no debug output or
  default credentials (`admin`/`admin123`) in production; security
  headers set — extend the existing Traefik middleware
  (`chart/templates/middleware-security-headers.yaml`), do not duplicate
  it elsewhere.
- **A03 Software Supply Chain**: dependencies pinned
  (`backend/requirements.txt` exact `==`, `frontend/package-lock.json`
  committed); automated vulnerability scanning (Trivy filesystem scan in
  `test.yaml`) MUST stay green; no new dependency without justification.
- **A04 Cryptographic Failures**: TLS terminated for all external traffic;
  only vetted crypto libraries; passwords hashed with a modern algorithm
  (bcrypt, currently in use — a deliberate migration to argon2id is
  acceptable, an incidental one is not).
- **A05 Injection**: parameterized queries only (SQLAlchemy ORM — no raw
  SQL string building); context-aware output encoding in the Vue
  frontend; no shell commands built from user input.
- **A06 Insecure Design**: threat considerations MUST be documented for
  every feature touching auth, property/meter access, or external input
  (image uploads, OCR/LLM service calls).
- **A07 Authentication Failures**: JWT + OIDC/PKCE are the only sanctioned
  auth mechanisms — no custom auth crypto; rate limiting on login and
  other sensitive endpoints (`slowapi`, already in use) MUST cover new
  sensitive endpoints, not just existing ones; secure session/token
  handling.
- **A08 Software/Data Integrity**: verify integrity of container images
  and Helm chart artifacts; validate any LLM/OCR service JSON response
  before trusting it — never blindly store an LLM-suggested reading or
  serial number (see the existing reading-vs-serial guard in
  `ocr_pipeline.py` as the pattern to follow).
- **A09 Logging & Alerting**: security-relevant events (login failures,
  role changes, access-denied responses) MUST be logged and alertable;
  logs MUST NOT contain secrets, tokens, passwords, or raw JWTs.
- **A10 Exceptional Conditions**: fail closed; errors MUST NOT leak
  internal details (stack traces, file paths) to clients; every error
  path MUST be handled explicitly, not left to an unhandled exception.
Rationale: this is a self-hosted application handling personal utility-
consumption data behind admin/OIDC accounts with real-world financial
implications (oil price / consumption tracking) — security regressions are
higher-cost than in a purely internal tool, and "non-negotiable" reflects
that this principle outranks convenience or speed when they conflict (see
Governance).

### VIII. Authenticated Communication by Default
Every backend endpoint — and every endpoint any project-operated service
exposes — MUST require authentication AND authorization for **inbound**
requests. The ONLY exceptions, listed exhaustively here, are:
`GET /api/health/live`, `GET /api/health`, `GET /auth/login`,
`GET /auth/callback`, `POST /auth/superadmin-login` (the credential-
exchange endpoint itself), and static frontend assets served by the
frontend/proxy containers. Any new unauthenticated endpoint MUST NOT be
added without an amendment to this constitution adding it to this list.

This principle governs inbound traffic only — requests arriving at a
service this project operates. **Outbound** calls the backend makes to
other services follow a separate rule, since this project does not always
control the far end:
- **Self-hosted / operator-controlled optional subsystems** (Ollama, the
  OCR service, or any future optional subsystem under Principle II): an
  authentication mechanism (a bearer-token setting, matching the existing
  `OLLAMA_API_KEY` pattern) SHOULD be offered and its use SHOULD be
  recommended to operators in documentation — because this project
  controls both ends, it should make securing that link easy and point
  operators at it, even though the default MAY stay unauthenticated for
  zero-config local/trusted-network use.
- **Third-party SaaS / internet-hosted services** outside this
  deployment's control (a hosted OIDC provider, a paid oil-price API):
  Principle VIII does not mandate an auth scheme this project doesn't own.
  TLS and whatever authentication the provider itself offers (e.g. an API
  key) MUST still be used when available.
Rationale: an explicit, closed allowlist is auditable in a way that
"endpoints should generally require auth" is not — a reviewer can check a
new inbound route against this exact list instead of reasoning about
intent. Outbound calls need a different rule because "MUST authenticate"
is only meaningful where this project can actually implement and enforce
it — its own self-hosted optional subsystems, not a third party's API.

### IX. Input Validation Is Server-Side Authoritative
The backend MUST validate every input (type, length, format, range,
allowlist where possible — e.g. image MIME verified via magic bytes per
the existing `filetype`-based check, never trusting client-supplied
`Content-Type`) regardless of any client-side checks. Client-side
validation (Vue forms) is never the security boundary. The frontend SHOULD
additionally validate the same rules for usability. Validation rules
SHOULD be defined once — as Pydantic models on the backend — rather than
duplicated ad hoc across routers.
Rationale: continues the project's existing pattern (see the magic-bytes
MIME check and 10 MB upload cap already in `ocr_pipeline.py`) as an
explicit, non-negotiable rule rather than an incidental one.

### X. Verifiable Security (CI Gates)
CI MUST run SAST, dependency scanning, and secret scanning; merges MUST be
blocked on high/critical findings from any of them. Dependency scanning
already exists (Trivy filesystem scan in `test.yaml`) and MUST stay green.
SAST (`bandit` for the Python backend, `eslint-plugin-security` for the
TypeScript frontend) and secret scanning (`gitleaks`, Principle III) are
wired into `test.yaml` as required jobs. Every feature touching
auth, access control, or input handling MUST include negative tests
(unauthenticated request, unauthorized request, malformed input) alongside
the pytest/vitest coverage already required by Principle I.
Rationale: "checked against OWASP Top 10" (Principle VII) is only as good
as what's actually verified in CI — this principle makes that verification
mandatory and machine-checked rather than reviewer-dependent.

## Additional Constraints

- Container images MUST pass the existing Trivy scan in CI; new
  HIGH/CRITICAL findings introduced by a change MUST be fixed or
  explicitly justified in the PR description, not suppressed silently.
- Uploaded meter images MUST stay under `UPLOAD_PATH` and MUST NOT be
  exposed outside the property/meter access-control boundary defined by
  the RBAC roles in Principle III.
- Default credentials (`admin` / `admin123`) are for local/dev use only;
  code and docs MUST continue to state they must be changed via env vars
  in any non-local deployment (see also Principle VII, A02).

## Development Workflow

- Changes land via pull request. Direct pushes to `main` are blocked by
  GitHub branch protection; this applies to every contributor, including
  repo admins.
- CI gates (`test.yaml`: backend/frontend tests, bandit, eslint-plugin-
  security, Trivy, gitleaks; `build.yaml`: image build) MUST be green
  before a PR can merge. Branch protection enforces this as a required-
  checks list, not just a convention.
- PRs that add or change an environment variable MUST update: README env
  table, `docker-compose.yaml`, and `chart/values.yaml` (Principle V).
- PRs that change DB models MUST include the corresponding Alembic
  migration (Principle IV) and, where behavior is user-visible, a test
  (Principle I).
- PRs that add a new endpoint touching auth, access control, or external
  input MUST include the OWASP mapping (Principle VII) and the negative
  tests required by Principle X.
- Any PR proposing a new unauthenticated endpoint MUST include the
  constitution amendment adding it to Principle VIII's exception list —
  it cannot land as a silent addition.

## Governance

This constitution supersedes ad hoc practice for this repository. Amendments
are made by editing `.specify/memory/constitution.md` directly (via
`/speckit-constitution` or an equivalent reviewed change) and MUST include
an updated Sync Impact Report header and version bump per semantic
versioning: MAJOR for incompatible principle removal/redefinition, MINOR
for a new principle or materially expanded guidance, PATCH for wording or
clarification only. `LAST_AMENDED_DATE` MUST be updated on every change;
`RATIFICATION_DATE` is fixed at first adoption and never changes.

When principles conflict, priority order is: **Security (Principle VII) >
Backward compatibility (Principle VI, Brownfield Respect) > Simplicity >
Performance.** This ordering exists to make trade-off calls decidable in
review rather than argued case-by-case.

PRs that conflict with a principle above MUST either be brought into
compliance or state the justified exception in the PR description —
silent violations are not acceptable. There is no separate runtime
guidance file at this time; this document is authoritative.

**Version**: 1.2.0 | **Ratified**: 2026-09-11 | **Last Amended**: 2026-09-11
