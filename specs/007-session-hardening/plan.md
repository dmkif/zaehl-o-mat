# Implementation Plan: Session Hardening (F-007, F-008)

**Branch**: `007-session-hardening` | **Spec**: [spec.md](spec.md)

## Summary
Backend: migration `0007` (`users.token_version`, table `login_codes`), `auth.py` (claims `tv`/`iat`, check in `get_current_user`, `revoke_sessions` helper), `routers/auth.py` (callback issues a code, `POST /auth/exchange`, authenticated logout), `routers/admin.py` (bump on role change). Frontend: `AuthCallback.vue` exchanges the fragment code, `AppHeader.vue` sends the token on logout, small helper `utils/authCode.ts` with vitest. Edits in `routers/auth.py` are localized (callback tail, `_sign_in`, logout) to stay mergeable with the OIDC diagnostics work on the same file.

## Constitution Check
I tests first PASS; IV reversible migration (0007) PASS; V Compose/Helm parity PASS (no new setting, no env change); VII OWASP A07/A04/A09 PASS; IX server-side validation PASS.

## Decisions
- **Code exchange over fragment or cookie.** A fragment alone would keep the JWT out of server logs but still in browser history and any script on the page before replace; a cookie contradicts the Bearer design and F-042. Code in the fragment + POST exchange: the only thing that ever sits in a URL is a worthless-after-use 60 s code, and the fragment is not sent to servers anyway (defence in depth against ingress logging).
- **Storage**: `login_codes(code_hash CHAR(64) PK, user_id FK users ON DELETE CASCADE, expires_at, created_at)`. Consume = `DELETE ... WHERE code_hash=:h` and check `rowcount == 1` and `expires_at > now` (atomic single use, also across replicas). Expired rows are purged at insert time like the PKCE states.
- **Revocation by per-user counter** `users.token_version`, claim `tv`, instead of a jti session table or deny list: one integer column, one comparison per request (the user row is loaded anyway), no growth. Cost: logout ends all sessions of the user. A jti/session table would allow single-device logout but needs cleanup and a lookup table for little gain at household scale. Tokens without `tv` are rejected, so deploying signs everyone out once.
- **Bump points**: logout, admin role change, OIDC sign-in whose role differs from the stored one, refused OIDC sign-in of a known user (instead of deactivating the row: deactivation would need an un-deactivate path when the user returns to the group). User delete needs nothing (row gone); `is_active=false` already blocks per request.
- **Logout** takes optional bearer: valid token -> bump + `logout` event; otherwise 200 without effect, so an expired SPA session can always finish.
- **Superadmin login** (`/superadmin-login`) keeps returning the token directly (POST body, never in a URL); its token also carries `tv`.
- `iat` added for forensics; not validated.
