# Feature Specification: Session Hardening (F-007, F-008)

**Feature Branch**: `007-session-hardening`

**Created**: 2026-10-17

**Status**: Draft

**Input**: Fix the two remaining token-lifecycle findings of the security review (`security/REPORT.md` F-007, F-008; `security/review/backend.md` B-03, B-04): the session JWT travels in a URL, and there is no way to revoke a session.

## Background

After the OIDC callback the backend redirects to `/auth/token?token=<jwt>`. The 60 minute bearer token is therefore written to ingress/static-server access logs and browser history, and it cannot be revoked: `POST /auth/logout` does nothing, a role change or removal from all IdP groups leaves old tokens valid until `exp`. The `exp` part of F-007 is already fixed (mandatory, finite); a shorter token lifetime or refresh rotation stays out of scope.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - No session token in a URL (F-008, P1)

After signing in at the IdP the browser lands on the SPA with only a short-lived, single-use code in the URL fragment; the SPA swaps it for the session token with a POST. The JWT never appears in a URL, access log or history entry.

**Independent Test**: Complete the callback with a faked IdP, read `Location`, exchange the code once (200 + token), once more (400).

**Acceptance Scenarios**:
1. **Given** a successful callback, **Then** `Location` is `<app_base_url>/auth/token#code=<opaque>` and contains no JWT and no `?`.
2. **Given** a fresh code, **When** `POST /auth/exchange {code}`, **Then** 200 with a bearer token of that user; the same code a second time gives 400.
3. **Given** a code older than 60 s, an unknown code or a malformed code, **Then** 400 with one generic message (no oracle) and a `login_exchange` failed security event without the code.
4. **Given** the database, **Then** only a hash of the code is stored.
5. **Given** the code of an inactive/deleted user, **Then** 400.

### User Story 2 - Logout and privilege changes revoke tokens (F-007, P1)

Logout invalidates every token issued to that user so far. The same happens when an admin changes the role, when a sign-in changes the role, and when an IdP sign-in is refused because the user left all permitted groups. Deleted and deactivated users are already refused on every request.

**Acceptance Scenarios**:
1. A token used after `POST /auth/logout` (with that token) gets 401.
2. `POST /auth/logout` without or with an invalid token stays 200 (idempotent, nothing revoked) so the SPA can always finish logging out.
3. After an admin changes a user's role, the user's older tokens give 401; a new sign-in works.
4. After an OIDC sign-in with no permitted group, an existing user's older tokens give 401 and the user row is kept (reactivation by IdP group membership stays automatic).
5. A token without the version claim (issued before this release) gives 401.
6. A new sign-in on a second device does not log out the first.

### Edge Cases
- Deploy of this release signs every user out once (old tokens carry no version claim); documented in the release notes.
- Logout is "log out everywhere" for that user (per-user counter, not per-session). Accepted: household scale.
- Expired code rows are deleted opportunistically; no background job.

## Requirements *(mandatory)*

- **FR-001** (F-008): The callback MUST NOT place the JWT in the redirect; it issues a random single-use code (>= 256 bit) stored only as SHA-256 hash, bound to the user, valid 60 s, delivered in the URL fragment.
- **FR-002** (F-008): `POST /auth/exchange` consumes the code atomically (delete-and-count), rate limited, and returns the bearer token; all failures are indistinguishable (400) and logged via `log_event` without the code.
- **FR-003** (F-007): `users.token_version` (integer, default 0) is embedded in every token (`tv`) together with `iat`; `get_current_user` rejects tokens whose `tv` is missing or differs from the stored value.
- **FR-004** (F-007): `POST /auth/logout` increments the caller's version when presented with a valid token; admin role change, sign-in role change and refused IdP sign-in (known user) increment it. Events: `logout`, `session_revoked`.
- **FR-005**: The SPA uses the exchange flow, sends its token on logout, and keeps working without a token in `sessionStorage` history/URL.
- **FR-006**: Schema change by reversible migration `0007`; no new settings.
- **FR-007**: Every behaviour has a test that fails before and passes after (principle I).

## Success Criteria
- **SC-001**: Rig experiments E-A06-003 (privilege after IdP removal) and E-A04-001 pass; a rig grep for `token=eyJ` after login finds nothing.
- **SC-002**: Full backend suite and frontend build/vitest/lint:security green.

## Assumptions
- Out of scope: shorter token lifetime, refresh rotation, HttpOnly cookie (REPORT F-042 stays accepted), admin endpoint to set `is_active` (the flag is already enforced on every request).
