# Implementation Plan: Security Review Follow-up (low findings, auth and HTTP)

**Branch**: `010-low-auth-http` | **Spec**: [spec.md](spec.md)

## Owns
`backend/app/{auth.py,routers/auth.py,main.py,services/oil_price.py}`, `config.py` (settings appended), `chart/templates/deployment-backend.yaml`, `chart/values.yaml`, `docker-compose.yaml`, README rows, tests `test_oidc_login_hardening.py` and `test_health_docs_misc.py`, `security/REPORT.md` status.

## Decisions
- No schema change (migration 0009 belongs to another feature): the binder hash is stored in `oidc_pkce_states.code_verifier` as `<verifier>.<sha256(binder)>` (fits the 128 column); the `nonce` is `HMAC(JWT_SECRET_KEY, state)`, so nothing is stored. The app only uses userinfo, so the nonce is checked only when an id_token is returned (back channel, signature check not required per OIDC Core 3.1.3.7).
- State is deleted before the binder comparison (single use even for attackers).
- `sub` validation after the group check so rejected sign-ins keep their audit/403 behaviour.
- Username collision: `base`, then `base-<sha256(sub)[:8..64]>`; e-mail collision is never resolved by merging.
- Health: `/api/health` (DB + scheduler) is the probe; details moved to `/api/health/details` (admin, 30 s cache). Probes get `timeoutSeconds: 3`.
- Oil price job runs as background task from the lifespan; `_run_oil_price` swallows and logs errors.

## Constitution Check
I tests first PASS; III secure-by-default (docs off) PASS; V Compose/Helm parity PASS; VII OWASP A02/A06/A07/A09/A10 PASS.
