# Feature Specification: Security Review Follow-up (low findings, auth and HTTP)

**Feature Branch**: `010-low-auth-http`

**Status**: Implemented

**Input**: fix the open low findings of `security/REPORT.md` in the auth/HTTP area: F-017, F-018, F-019, F-025, F-026, F-027, F-031, F-046 (F-045 documented only). Plus the readiness flapping seen in the 0.6.0 rollout.

## User Scenarios & Testing

### US1 - SSO login cannot be forced onto a victim (F-017, P1)
`/auth/login` binds the login to the browser by an HttpOnly SameSite=Lax cookie; the callback needs it. An OIDC `nonce` is sent and checked if an id_token is processed.
1. Callback from a browser without or with a foreign cookie -> 400, state consumed, no user created.
2. id_token with a wrong nonce -> 400.

### US2 - Bad IdP data cannot touch special accounts (F-018, F-019, P1)
1. userinfo without a usable `sub` -> 502; the superadmin row (NULL `oidc_sub`) is never read or changed.
2. Same `preferred_username` for another identity, or one equal to `SUPERADMIN_USER` -> unique derived username, no merge.
3. E-mail already used by another account -> 409 (first login) or old e-mail kept (later login); never 500, never merge.

### US3 - Login endpoints are cheap and IdP outages are clean (F-026, P2)
1. `/auth/login` and `/auth/callback` are rate limited; pending states are capped and purged.
2. IdP timeout/unreachable/5xx -> 504/502/503 with bounded timeouts; discovery failures cached negatively for 30 s.

### US4 - Less public surface (F-031, F-046, P2)
1. API docs are off unless `ENABLE_API_DOCS=true`.
2. Anonymous `/api/health` returns `{status}` only and calls nothing external; `/api/health/details` needs an admin and caches OCR/LLM results 30 s.
3. `X-Request-ID` must match `^[A-Za-z0-9._-]{1,64}$`, else a UUID replaces it.

### US5 - Secrets and start-up (F-027, F-025, P2)
1. The oil-price API key never appears in logs (parameter, class+status only, log filter).
2. Start-up does not fail or wait on the oil-price fetch or its DB writes; implausible prices are ignored.

### US6 - Readiness is stable (P1)
Pod readiness depends on DB and scheduler only; optional services (gateway, Ollama, OCR) cannot flip it.

## Requirements
- FR-001..FR-012 as scenarios above; new settings `ENABLE_API_DOCS` (false), `OIDC_RATE_LIMIT` (20/minute), `OIDC_MAX_PENDING_STATES` (1000), `OIDC_HTTP_TIMEOUT_S` (10), in Compose, chart values and README (parity).
- F-045 (informational): documented in REPORT, no code.

## Out of scope
Frontend footer (needs `/api/health/details`), logging of filenames in `routers/ocr.py`, dedicated superadmin column, ON CONFLICT upsert for oil prices.
