# Research: OIDC Group Diagnostics

Sources: `backend/app/auth.py` (`get_or_create_user_from_oidc`), `backend/app/routers/auth.py` (`callback`), `backend/app/config.py`, `chart/templates/deployment-backend.yaml`, project constitution (A09, III, V).

## R1 — Where to hook in

**Decision**: Inside `get_or_create_user_from_oidc`, right where the role is derived. The 403 for "no permitted group" is raised only there (the callback's other failures are 400/502), so the helper sees exactly the rejected case plus the accepted case.
**Rationale**: The delivered `groups` list and the configured names are already in scope; no extra plumbing. **Alternatives**: logging in the router (would need the userinfo passed around, duplicates logic); a FastAPI exception handler (loses the claim data).

## R2 — Module and log format

**Decision**: New pure module `app/services/oidc_audit.py` with `log_rejected(oidc_data)` and `log_accepted(oidc_data, role)`. Lines are `key=value` text for grep-ability (`event=… user=… reason=…`), group lists as JSON arrays. Logger name `app.oidc_audit`.
**Rationale**: Matches the services/routers split (constitution VI), testable with `caplog`. **Alternatives**: structured JSON logging for the whole app (out of scope).

## R3 — User label

**Decision**: `preferred_username` (the sign-in name), sanitised and cut to 64 chars; if absent, `sub:` + first 8 hex of SHA-256(sub); if even `sub` is absent, `unknown`. Never the e-mail, never the raw `sub`.
**Rationale**: Spec FR-007; the existing code uses `preferred_username or email.split("@")[0]` for the stored username — the e-mail local part is deliberately not used for logs. **Alternatives**: hash only (operator wants the user named).

## R4 — Sanitising and bounds

**Decision**: Each delivered value is converted with `str()`, cut to 64 chars, and the whole list is rendered with `json.dumps(..., ensure_ascii=True)`, which escapes control characters, quotes and line breaks, so a hostile name cannot start a new line. At most 20 names are listed, followed by `omitted=N`. Non-string entries are rendered as their type name.
**Rationale**: FR-009, A05/A09. **Alternatives**: custom escaping (error-prone); `repr` (not stable across types).

## R5 — Claim shape

**Decision**: "Usable" means a list (or tuple) of strings. Missing key, `None`, empty list, a plain string or any other type → reason `no_group_information` and the distinct diagnostic line (names the configured claim name, e.g. `groups`). A list with some non-strings keeps the strings and counts the others as omitted.
**Rationale**: The existing code does `oidc_data.get(claim, [])` and a plain string would be iterated as characters by `in`; treating it as unusable is the safe reading and does not change who is accepted today for valid claims. **Behavior note**: an identity provider that sends a single string equal to a configured group name would be accepted today (`"admin" in "admin"` is substring logic for strings!). Normalising a string to a one-element list would change that; the plan keeps matching exactly as today (FR-010) and only *logs* the shape; any change of matching is out of scope.

## R6 — Switch

**Decision**: `oidc_group_diagnostics: bool = False` in `Settings` (env `OIDC_GROUP_DIAGNOSTICS`). pydantic rejects invalid values at start (FR-011). Start-up: `lifespan` logs one WARNING when on (FR-013).
**Rationale**: Same mechanism as other settings, no new dependency.

## R7 — Chart and Compose

**Decision**: No chart template change; `backend.env` already renders arbitrary env vars. Add a commented example and explanation in `chart/values.yaml`, a commented variable in `docker-compose.yaml`, and a README table row (constitution V).
**Rationale**: Minimal; parity documented in the same change.

## R8 — Release and rollout

**Decision**: Merge, tag `v0.4.1` (CI builds images and chart), bump the HelmRelease chart version in k3s-gitops. The switch stays off in the cluster; the operator turns it on in the release patch when investigating (set `backend.env.OIDC_GROUP_DIAGNOSTICS: "true"`).
