# Backend and OCR-service code review (T028)

Scope: `backend/app/**`, `backend/scripts`, `backend/Dockerfile`, `backend/alembic`, `ocr-service/**`, plus the
deployment glue that decides how the code is exposed (`nginx.conf`, `chart/templates/ingressroute.yaml`,
`docker-compose.yaml` secrets). Method: manual reading only, nothing was executed and no app code was changed.
Maps to OWASP Top 10 2025 (A01 to A10). "Confirmed by reading" = the code path is unambiguous; "suspected" = depends on
library or deployment behaviour that the suggested experiment should settle.

Severity is a first guess (impact x likelihood in the household-scale, internet-facing context). All findings are
F-candidates and still need an experiment (E-ID assigned later).

## Summary table

| ID | Sev | OWASP | Title |
|---|---|---|---|
| B-01 | high | A07/A01 | OIDC group match is a substring test when the IdP sends a string |
| B-02 | high | A07/A06 | `--forwarded-allow-ips='*'` lets the client choose the rate-limit key (superadmin brute force) |
| B-03 | medium | A04/A09 | Session JWT is delivered in the URL query string |
| B-04 | medium | A07/A04 | No token revocation; logout is a no-op; removed IdP users keep access |
| B-05 | medium | A01 | Property "manager" can manage membership and grant property-admin |
| B-06 | medium | A01/A07 | Global admin can demote the superadmin; break-glass account never regains its role |
| B-07 | medium | A01 | `image_path` is client-supplied and trusted: cross-tenant file reference, delete and serve-check bypass |
| B-08 | medium | A10 | Bulk-scan reads all uploads into memory; no body limit on the k8s path |
| B-09 | medium | A10 | DB connection held across the whole SSE/LLM run, pool exhaustion |
| B-10 | medium | A10 | Image decompression/upscale memory amplification (backend and OCR service) |
| B-11 | medium | A09 | No audit logging of any security event; secret in log possible |
| B-12 | medium | A02 | Weak secrets pass validation (JWT key length, superadmin password, shipped Compose defaults) |
| B-13 | low | A06 | OIDC login CSRF: state not bound to the initiating browser |
| B-14 | low | A07 | Missing/empty `sub` matches the superadmin row (`oidc_sub IS NULL`) |
| B-15 | low | A06/A10 | Username/e-mail uniqueness collisions give 500 on login (incl. superadmin name clash) |
| B-16 | low | A10 | `secrets.compare_digest` on non-ASCII str raises TypeError, 500 on superadmin-login |
| B-17 | low | A08 | Client-chosen `source` makes the retention job delete readings and image links |
| B-18 | low | A06 | Duplicate-image check is check-then-insert without a unique constraint |
| B-19 | low | A08/A10 | Weak input validation: NaN/huge Decimal, `null` on NOT NULL, `Any` integration_config, no length limits |
| B-20 | low | A10 | `delete_user` likely fails with 500 when the user has property assignments |
| B-21 | low | A10 | Startup (`lifespan`) can crash on oil-price DB errors/races; blocking outbound call at startup |
| B-22 | low | A06 | Unauthenticated `/auth/login` writes to the DB on every call, no rate limit |
| B-23 | low | A04/A09 | Oil-price URL with API key can end up in logs (HTTPStatusError text) |
| B-24 | low | A10 | Any authenticated user (even without a property) can trigger LLM/OCR work; limiter is per IP only |
| B-25 | low | A02 | OCR service has no authentication and passes through unvalidated payload |
| B-26 | low | A02 | `/api/docs`, `/api/openapi.json` and `/api/health` are public; health does outbound calls |
| B-27 | low | A03 | Supply-chain hygiene: unpinned base image, no hash pinning, no `.dockerignore`, migrations on every start |
| B-28 | low | A08 | Unvalidated LLM/OCR output is returned verbatim (serial, raw_texts), prompt injection by image text |
| B-29 | info | A01 | 404-vs-403 ordering leaks existence of properties, meters, readings |
| B-30 | info | A05/A09 | `X-Request-ID` is reflected unvalidated; log lines contain client-controlled filenames |
| B-31 | info | A01 | Cross-property `replaced_by_id`; `integration_config` stored in plaintext |

---

## Findings

### B-01 Substring group match when the IdP sends a string
- OWASP: A07 (also A01). Component: `backend/app/auth.py`. Severity: **high** (conditional on IdP shape; seed finding from feature 003).
- Location: `auth.py:39-48`
- Description: `groups = oidc_data.get(settings.oidc_groups_claim, [])`, then `settings.oidc_admin_group in groups`. If the
  claim is a `str`, `in` is a substring test. Defaults are the very short `admin`, `verwalter`, `user`. A claim such as
  `"superuser"`, `"administrators"`, `"xadminx"` or `"users"` therefore matches `admin`/`user`. If the claim is `dict`, `in`
  checks keys; if it is `null`/`int`, `in` raises TypeError (500). There is no type check, no normalisation, no case rule.
- Impact: privilege escalation to global `admin` (all properties, user management) for any IdP user whose single-valued
  groups claim merely contains the admin group name; or access for users not in any permitted group.
- Reproduce: fake OIDC with `userinfo.groups = "xadminx"` (string), `"administrators"`, `"superuser"`, `["admin"]` as control,
  `null`, `42`, `{"admin": 1}`. Complete `/api/auth/login` -> `/callback`, then `GET /api/auth/me` and read `role`.
  Expected after fix: 403 for all string/dict/null variants unless the string equals the group exactly (or is rejected outright).
- Recommendation: require `isinstance(groups, list)` with `str` elements (optionally accept a string only by splitting on a
  documented separator), compare exact and case-sensitively, 403 on wrong type; add negative tests.
- Confidence: confirmed by reading.

### B-02 Client-controlled rate-limit key (X-Forwarded-For trust)
- OWASP: A07 / A06. Component: `backend/Dockerfile`, `backend/app/limiter.py`, `backend/app/routers/auth.py`. Severity: **high** for the Compose/nginx path, medium for k8s (suspected).
- Location: `Dockerfile:25-26` (`--proxy-headers --forwarded-allow-ips='*'`), `limiter.py:4` (`get_remote_address`), `routers/auth.py:711-713` (`5/minute`), `nginx.conf:23,40` (`$proxy_add_x_forwarded_for`).
- Description: uvicorn is told to trust X-Forwarded-For from every peer. nginx appends to a client-supplied header
  (`$proxy_add_x_forwarded_for`), so the header is `<client-chosen>, <real-ip>`. With `*` as trusted set uvicorn takes the
  left-most value (to be confirmed experimentally), so the client picks `request.client.host`. slowapi keys on exactly that.
  Result: each request can carry a new fake IP, the "5/minute" limit on `superadmin-login` (and 4-30/min limits on OCR
  endpoints) never triggers. In k8s the Traefik entrypoint may overwrite XFF (default for untrusted sources), but the backend
  pod additionally trusts anyone in the cluster/network that can reach port 8000. The opposite failure mode (limiter
  keyed on the proxy IP when XFF is not parsed) would let any single client exhaust the shared bucket and lock out the legitimate superadmin.
  Also the counters are in-memory per process: with `replicaCount > 1` the effective limit is N x 5/min.
- Impact: unthrottled online brute force of the break-glass superadmin password (the only password in the system) and
  of the cost-bearing OCR endpoints.
- Reproduce: 200 sequential `POST /api/auth/superadmin-login` with wrong password, header `X-Forwarded-For: 10.0.0.<i>`
  against the Compose stack (through nginx) and directly to the backend: count 401 vs 429. Control run without the header must give 429 from the 6th request.
  Second experiment: two source IPs behind the proxy without header manipulation, check whether they share a bucket.
- Recommendation: set `--forwarded-allow-ips` to the real proxy address/CIDR (or the Traefik pod CIDR), never `*`; make the
  proxy overwrite (`$remote_addr`) instead of append XFF; add an account-level lockout/back-off for `superadmin-login`
  (counter in DB, independent of IP); use a shared limiter store (Redis/DB) if more than one replica is supported; alert on repeated failures (see B-11).
- Confidence: limiter and flags confirmed by reading; uvicorn left-most selection suspected (verify).

### B-03 Session JWT travels in the URL query
- OWASP: A04 / A09. Component: `routers/auth.py`. Severity: **medium**.
- Location: `routers/auth.py:705-708` (`RedirectResponse(url=f"{frontend_url}/auth/token?token={access_token}")`).
- Description: the 60-minute bearer token is placed in a GET URL of the SPA host. It ends up in the access logs of the
  ingress (Traefik) and the static file server, in browser history, and in any error-reporting/Referer (same-origin Referrer-Policy limits the latter). The token is also not URL-encoded (JWT charset is safe).
- Impact: anyone with read access to ingress/frontend logs or browser history can replay valid tokens until expiry; there is no revocation (B-04).
- Reproduce: complete the OIDC flow against the rig, grep the frontend/ingress/uvicorn logs for `token=eyJ`; `curl -i` the callback and read `Location`.
- Recommendation: deliver the token in the URL fragment (`#token=`, never sent to servers) or, better, set an `HttpOnly; Secure; SameSite=Lax` cookie / one-time code exchanged via POST; scrub query strings from access logs as a stop-gap.
- Confidence: confirmed by reading.

### B-04 No token revocation, no-op logout, stale IdP state
- OWASP: A07 / A04. Component: `auth.py`, `routers/auth.py`. Severity: **medium**.
- Location: `auth.py:19-32`, `routers/auth.py:752-756`, `auth.py:56-62`.
- Description: JWT has only `sub`, `role`, `exp`; no `iat`, `jti`, `iss`, `aud`, no server-side deny list, no session table. `POST /auth/logout`
  does nothing and is unauthenticated. `claims.validate()` does not require `exp` to be present (tokens are only minted with it, so this only matters if the signing key leaks). A user who was removed from all IdP groups gets 403 at the next login, but
  the local row stays `is_active=True` with the old role and property assignments, and a token issued before stays valid up to 60 minutes. There is no endpoint to set `is_active=False` at all; `is_active` is checked on every request but cannot be changed through the API.
  On the positive side, the `role` claim in the token is ignored; the role is read from the DB on each request.
- Impact: stolen or leaked token (B-03) cannot be revoked; offboarding is delayed up to the token lifetime; a compromised key forges tokens without `exp`.
- Reproduce: log in, call logout, replay the same token (expect still 200); remove the user from the IdP group and replay; mint a token without `exp` with the known test key and check acceptance.
- Recommendation: add `iat`/`jti` and a per-user `token_valid_after` (or session table), enforce `exp` via `claims.validate()` with `exp: {"essential": True}`, shorten lifetime or add refresh with rotation, deactivate users on login rejection, add an admin endpoint for `is_active`.
- Confidence: confirmed by reading.

### B-05 Property "manager" can manage membership and escalate to property admin
- OWASP: A01. Component: `routers/properties.py`, `permissions.py`. Severity: **medium**.
- Location: `properties.py:105-131` (add/update), `134-151` (remove), `65-78` (PATCH property); level `write` -> roles admin+manager (`permissions.py:23-27`).
- Description: `add_property_user`, `remove_property_user` and `update_property` only require `level="write"`, which includes the
  property role `manager`. `PropertyUserAdd.role` may be `admin`. A manager can (a) add any existing user id as property admin, (b) change the role of an existing member (including the current admin) via the "existing" branch, (c) remove any member including admins, (d) rename the property/change address. The docstring in `permissions.py` says managers can "add readings/meters", not manage users. Target user existence is also not constrained to active users.
- Impact: lateral/vertical privilege escalation inside a property: manager -> admin, lock out the real admin, enable `delete` rights on meters/readings.
- Reproduce: user M with property role manager; `POST /api/properties/{p}/users {"user_id": M, "role":"admin"}` (self-promotion, expect 200 today), then `DELETE /api/properties/{p}/meters/{m}` (needs delete). Also `POST` with the admin's id and role `user`, then `DELETE /api/properties/{p}/users/{admin}`.
- Recommendation: membership management and property PATCH require `delete`-level (property admin) or global admin; forbid removing/demoting the last property admin; add matrix test.
- Confidence: confirmed by reading.

### B-06 Global admin can demote/lock out the superadmin; role never restored
- OWASP: A01 / A07. Component: `routers/admin.py`, `routers/auth.py`. Severity: **medium**.
- Location: `admin.py:184-201`, `auth.py:726-738`.
- Description: `set_user_role` only blocks *granting* superadmin by non-superadmins; it does not protect an existing superadmin as the *target*.
  An `admin` can `PATCH /api/admin/users/{superadmin_id}/role {"role":"user"}`. `superadmin_login` finds the row by username
  and issues a token without re-asserting `UserRole.superadmin`, so the break-glass account stays demoted until DB surgery. Same endpoint lets an admin change their own role or demote other admins; `delete_user` lets admins delete other admins and themselves (only superadmin rows are protected there, which shows the intent).
  Additionally the role set here is overwritten by the IdP group at the next OIDC login (`auth.py:58`), so admin-made role changes for OIDC users are silently ineffective.
- Impact: a (possibly compromised) admin neutralises the emergency account; surprising role semantics.
- Reproduce: as admin token, `PATCH` role on the superadmin id (expect 200 today), then superadmin-login and `GET /auth/me`.
- Recommendation: reject any change/deletion whose target is superadmin unless caller is superadmin; on superadmin-login force the role back to superadmin; decide and document whether OIDC users' roles come from the IdP only (then remove or restrict this endpoint) ; forbid self-demotion/last-admin removal.
- Confidence: confirmed by reading.

### B-07 Client-supplied `image_path` is trusted (cross-tenant reference, delete, serve-check bypass)
- OWASP: A01. Component: `routers/readings.py`, `routers/uploads.py`, `schemas/readings.py`. Severity: **medium** (needs the upload file name, which readers of a property can see in API responses; ex-members keep it).
- Location: `readings.py:317-322` (hash from `Path(body.image_path).name`), `readings.py:366-375` (unlink by `Path(...).name`), `uploads.py:443-459`, `schemas/readings.py:134-135`.
- Description: `POST .../readings` stores `image_path`/`serial_image_path` verbatim (any string up to 512 chars; no check that the file was produced by the caller's own scan, that it is unattached, or that it has the canonical `uploads/<uuid>.<ext>` shape). Consequences:
  1. Cross-tenant delete: a user with write on property B creates a reading pointing to `uploads/<A's file>`, then (as property admin of B) deletes it; `delete_reading` unlinks `upload_path/<name>` and destroys A's image (and the same happens on legitimate delete of a reading whose file is shared).
  2. Hash oracle: `create_reading` hashes any file in the upload dir named by the client and runs the duplicate lookup (scoped to caller, so only boolean-ish leakage).
  3. Serve check bypass/ambiguity: `serve_upload` takes `.first()` of readings matching the path without `ORDER BY`; access is decided on an arbitrary row. A reading with a non-canonical path (e.g. `x.jpg` without `uploads/`) is never matched, so the file is treated as "unattached" and any authenticated user who knows the name can read it. Unattached files are by design readable by every authenticated user holding the UUID filename.
- Impact: integrity/availability of other tenants' evidence images; weakened tenant isolation of image serving. Exploit requires the UUID filename (unguessable) but those are exposed by `GET readings` to all property members including later-removed ones.
- Reproduce: tenants A and B on the rig; A scans, saves reading, lists readings to learn path; B (property admin on B) `POST` reading with that path, `GET /api/uploads/<name>` (compare with 403 for B before), `DELETE` the B reading, then `GET` as A (expect 404 today). Variant: path `foo.jpg`/`../x/uploads/<name>`.
- Recommendation: server-side ownership of uploads (table `uploads` with owner/property, or bind the file to the scanning user + short-lived token returned by `/ocr/scan`); validate `image_path` shape; never unlink a file still referenced by another reading; make serve check "any referencing reading the user can access, deny otherwise" and require an attachment for non-admin access.
- Confidence: confirmed by reading (exploit chain needs the experiment).

### B-08 Unbounded in-memory reads in bulk-scan
- OWASP: A10. Component: `routers/ocr.py`. Severity: **medium** (k8s path), low behind nginx (15 MB cap).
- Location: `ocr.py:486-487` (`await f.read()` for each of up to 50 files before any size check; size is only checked later in the generator at `519`); `chart/templates/ingressroute.yaml` has no body-size/buffering middleware.
- Description: nginx limits the body to 15 MB, Traefik in the chart does not. Starlette spools multipart to temp files, then the endpoint reads every file fully into `file_payloads` (50 x unlimited). `scan` is bounded with `file.read(MAX+1)` and is fine.
- Impact: memory exhaustion/OOM-kill of the backend pod by any authenticated user (4 requests/min allowed, see B-02).
- Reproduce: authenticated `POST /api/ocr/bulk-scan` with e.g. 5 x 200 MB files, or 50 x 50 MB through the k3s-style path; watch RSS, status code. Also single `scan` with a 1 GB body to see multipart spooling to disk.
- Recommendation: Traefik `buffering` middleware with `maxRequestBodyBytes`, app-level `Content-Length` check, per-file bounded `read(MAX+1)` and early reject; process one file at a time.
- Confidence: confirmed by reading.

### B-09 DB connection pinned for the duration of the streamed LLM run
- OWASP: A10. Component: `routers/ocr.py`, `database.py`. Severity: **medium** (suspected for the exact FastAPI lifecycle).
- Location: `ocr.py:436-444,492-663`; pool `database.py:353` (size 10 + 20 overflow).
- Description: `generate()` uses the request-scoped `db` session across up to 50 images x (up to 5 LLM calls x gateway read timeout 300 s). The session's transaction (autobegin on the first query) keeps one pooled connection checked out for minutes. Depending on whether FastAPI closes the yield-dependency before or after streaming, the generator either holds a connection or uses a closed session. Four runs/min per (spoofable, B-02) IP are allowed.
- Impact: 30 parallel bulk-scans exhaust the pool, the whole API hangs on `QueuePool timeout` (500s) while the LLM is slow.
- Reproduce: start 35 bulk-scans against a gateway stand-in with 120 s latency; meanwhile request `/api/auth/me`; observe latency/500. Check `pg_stat_activity` for `idle in transaction`.
- Recommendation: do DB work up front, `db.close()`/commit before streaming and open short sessions inside the generator; cap concurrent bulk jobs per user/globally with a semaphore.
- Confidence: suspected.

### B-10 Image decompression and 4x upscale memory amplification
- OWASP: A10. Component: `backend/app/services/llm_client.py`, `ocr-service/app/pipeline.py`. Severity: **medium**.
- Location: `llm_client.py:108-112` (`Image.open` + `exif_transpose`, no pixel cap), `ocr-service/app/pipeline.py:121-123` (`cv2.resize(... dw*4, dh*4)`), `ocr.py:265-266` (`already_cropped` is a client-controlled form field and skips the strip crop); no `Image.MAX_IMAGE_PIXELS` is set anywhere.
- Description: a 10 MB PNG/WEBP/GIF can decode to tens of megapixels (Pillow's default only warns at ~89 MP and errors at ~179 MP). With `already_cropped=true` the OCR service denoises and upscales the *whole* image 4x in each dimension (16x pixels), e.g. 8000x8000 -> 32000x32000 uint8 (about 1 GB per channel plus copies). The backend decodes the full image into RGB before downscaling for the LLM. `fastNlMeansDenoising` is also CPU-heavy. The OCR service itself has no size limit (`await file.read()`, `main.py:63,78`).
- Impact: OOM-kill/CPU starvation of the OCR pod and backend by one authenticated user (even without any property).
- Reproduce: craft a highly compressible 9000x9000 PNG (< 10 MB), `POST /api/ocr/scan` with `engine=ocr`, `already_cropped=true`; watch OCR pod memory/restart. Same image with `engine=llm` for the backend.
- Recommendation: set `Image.MAX_IMAGE_PIXELS` to a sane bound (e.g. 40 MP) and treat `DecompressionBombWarning` as an error, check `img.size` before decoding/upscaling and cap the upscaled size, add memory limits in the chart, size-limit the OCR service.
- Confidence: confirmed by reading (resource effect needs measurement).

### B-11 No audit/security logging; secrets can reach logs
- OWASP: A09. Component: `routers/auth.py`, `auth.py`, `routers/admin.py`, `routers/properties.py`. Severity: **medium**.
- Location: grep shows no `logger` in `auth.py`, `routers/auth.py`, `admin.py`, `properties.py`, `meters.py`, `readings.py`, `uploads.py`.
- Description: none of these events is logged: OIDC login success/failure and reason (group rejection, token exchange failure, invalid/expired state), superadmin login success and failure, 401/403 decisions, role changes, user deletion, property-membership changes, reading/meter deletion. Brute force on `superadmin-login` (B-02) and role abuse (B-05/B-06) would leave no trace except uvicorn access lines. uvicorn's access log does contain the full request line for `/api/auth/callback?code=...&state=...` (single-use code, PKCE-bound: low risk) . Positive: `llm_client.py` avoids logging URLs/keys; httpx/httpcore loggers are pinned to WARNING; the `Settings` secret `gateway_api_key` is a `SecretStr`.
- Impact: attacks and misuse are undetectable and non-attributable.
- Reproduce: run the rig with `LOG_LEVEL=debug`, perform failed superadmin logins, role change, deletion, then inspect `BACKEND_LOG` for events (expect none today); grep the log for `code=`, `token=`, API keys.
- Recommendation: structured security-event logger (event, actor id, target id, source IP, outcome; never tokens/codes/passwords), log every denied auth decision; redact query strings of `/auth/callback` in access logs.
- Confidence: confirmed by reading.

### B-12 Weak secrets are accepted at startup
- OWASP: A02. Component: `config.py`, `docker-compose.yaml`. Severity: **medium**.
- Location: `config.py:215-224`, `docker-compose.yaml:57,59`.
- Description: validation only rejects two exact sentinel strings. `JWT_SECRET_KEY=a` (HS256) or the shipped `local-dev-secret-change-in-prod` is accepted; no minimum length/entropy. `SUPERADMIN_PASSWORD` has no minimum and the Compose file ships `admin123` for a login that grants full access (and the default `SUPERADMIN_USER` is empty: with an empty password the login is disabled, good). `OIDC_CLIENT_SECRET: ""` is accepted although OIDC login then fails at runtime only. `DATABASE_URL` check blocks only the exact default URL.
- Impact: forged tokens (offline HS256 brute force of a short key) or trivially guessable superadmin login on any deployment that copies the Compose file.
- Reproduce: start the backend with `JWT_SECRET_KEY=x`, `SUPERADMIN_PASSWORD=admin123`; expect start-up success today; forge a token with a wordlist key.
- Recommendation: enforce JWT key >= 32 bytes and reject known-weak values (including the Compose one), superadmin password minimum length/denylist, require `OIDC_*` when discovery URL is set; remove real-looking defaults from Compose or refuse to start with them.
- Confidence: confirmed by reading.

### B-13 OIDC login CSRF (state not bound to the browser)
- OWASP: A06 / A07. Component: `routers/auth.py`. Severity: **low** (medium if the app is publicly federated).
- Location: `auth.py:629-650` (state in DB only), `653-708`.
- Description: `state` + PKCE verifier are stored server-side keyed by `state` alone; no cookie/nonce ties the callback to the browser that started `/login`. An attacker can run `/login`, complete the IdP step with their own account, and give the victim the resulting `/api/auth/callback?code=..&state=..` link: the victim's browser receives the *attacker's* session token (login CSRF). `/login` is also unauthenticated and can be called by anyone to obtain valid `state` rows. No ID-token/`nonce` validation: identity is taken from `userinfo` obtained with the code (acceptable over TLS back-channel). State consumption is delete-then-commit without `DELETE ... RETURNING` (two parallel callbacks with the same state can both pass the lookup; the IdP code is single-use so the second exchange should fail).
- Impact: victim acts inside attacker's account (data mixing); small for this app.
- Reproduce: two clients; client 1 starts login and finishes IdP auth, client 2 requests the callback URL; check which identity client 2 receives. Replay the same callback twice, and twice in parallel.
- Recommendation: set a short-lived `HttpOnly` cookie with a random value at `/login`, store its hash with the state row and require it at callback; make state consumption atomic.
- Confidence: confirmed by reading.

### B-14 Missing/empty `sub` matches the superadmin row
- OWASP: A07. Component: `auth.py`. Severity: **low** (needs a misbehaving or malicious IdP).
- Location: `auth.py:36,56`.
- Description: `sub = oidc_data.get("sub")` is never validated. With `sub=None`, `User.oidc_sub == None` becomes `oidc_sub IS NULL` and matches the superadmin row (created without `oidc_sub`). The code then sets `user.role = role` (group-derived) and `user.email = email` on that account and the callback issues a token for the superadmin's user id with that rewritten role. `email` missing is `""` (also unique-constrained, B-15).
- Impact: userinfo without `sub` (spec violation) overwrites the break-glass account's role/e-mail and logs the caller in as that identity.
- Reproduce: fake OIDC userinfo `{"preferred_username":"x","groups":["user"]}` with no `sub` after a superadmin login created the row; check `/auth/me` and the DB row.
- Recommendation: require `sub` as non-empty `str` (<=255), reject otherwise (502); never let OIDC login touch rows with a NULL `oidc_sub`.
- Confidence: confirmed by reading.

### B-15 Uniqueness collisions produce 500 or account confusion
- OWASP: A06 / A10. Component: `auth.py`, `routers/auth.py`. Severity: **low**.
- Location: `auth.py:56-87`, `routers/auth.py:726-737`.
- Description: `username` and `email` are UNIQUE. (a) First login with a username/e-mail that already belongs to another row (same `preferred_username` for two IdP users, missing e-mail `""` for two users, IdP user named like `SUPERADMIN_USER`) raises IntegrityError; the retry path re-queries by `sub`, finds nothing and re-raises -> 500, the user can never log in. (b) The existing-user path overwrites `email` from the IdP without handling a collision -> 500 on every login. (c) `superadmin_login` selects by `username == SUPERADMIN_USER`: if an OIDC user got that name first, the superadmin password logs into that OIDC user's account (role admin, not superadmin) instead; if the superadmin row exists first, an IdP-controlled `preferred_username` equal to it blocks that person (targeted DoS, only if usernames are user-editable at the IdP). (d) `superadmin_login` first-time creation has no race handling (two parallel first logins -> 500 for one).
- Impact: login denial for legitimate users, confusing identity mapping at the break-glass account.
- Reproduce: fake OIDC users `a` and `b` both with `preferred_username="alice"` or empty e-mail; create/parallelise first logins (also the 100x barrier race from R4).
- Recommendation: derive a unique local username on collision, do not key identity on e-mail, mark the superadmin by a dedicated column/`oidc_sub IS NULL AND role=superadmin`, catch IntegrityError on all paths.
- Confidence: confirmed by reading.

### B-16 `compare_digest` on non-ASCII strings
- OWASP: A10. Component: `routers/auth.py`. Severity: **low**.
- Location: `auth.py:721-722`.
- Description: `secrets.compare_digest(str, str)` raises `TypeError` when either argument contains non-ASCII characters. `body.username`/`body.password` are attacker-controlled. Result: unhandled exception -> HTTP 500 (stack trace in logs) instead of 401; not a bypass. Also no length bounds on the pydantic model (very long inputs are compared as-is).
- Reproduce: `POST /api/auth/superadmin-login {"username":"é","password":"ü"}` -> 500 (expect 401).
- Recommendation: compare `.encode()` bytes (or HMAC digests), bound field lengths.
- Confidence: confirmed by reading (CPython behaviour is documented).

### B-17 Client-controlled `source` lets the retention job delete readings and image links
- OWASP: A08 / A06. Component: `schemas/readings.py`, `jobs/retention.py`. Severity: **low**.
- Location: `schemas/readings.py:133`, `readings.py:342`, `retention.py:107-135` (`_downsample` deletes all `auto`/`archived` rows per bucket and re-inserts only `meter_id,value,read_at,'archived'`).
- Description: any property manager/admin may create readings with `source: "auto"` or `"archived"`, which the nightly job treats as machine-generated data: it averages them per minute/15-min/hour bucket, deletes the originals and drops `image_path`, `note`, `image_hash`. Counter-type values averaged in a bucket are no longer the recorded values. The "manual readings are never touched" invariant is enforced only by trusting the client. After downsampling, the now-unreferenced images are deleted by `cleanup_orphan_uploads` 24 h later. With multiple replicas every pod runs the job (no advisory lock).
- Impact: silent loss/alteration of billing-relevant readings and evidence images; trivially exploitable by mistake by an API client.
- Reproduce: `POST` two readings with `source:"auto"` and an `image_path` within one minute, run `run_retention`, list readings.
- Recommendation: force `source=manual` for API-created readings (or restrict others to a service role); advisory lock for the job; keep the evidence fields when merging.
- Confidence: confirmed by reading.

### B-18 Duplicate-image check is a check-then-insert race
- OWASP: A06. Component: `routers/readings.py`. Severity: **low**.
- Location: `readings.py:324-351`; `models.py:Reading.image_hash` has a plain index, no unique constraint (migration `0003`).
- Description: two concurrent POSTs with the same `image_path` both pass the duplicate lookup and both insert. The duplicate rule is a business control against double entry (also used by the bulk review). The client-supplied `image_hash` field is accepted by the schema but ignored (good).
- Reproduce: barrier-released 20 parallel identical `POST`s, count rows (R4 method).
- Recommendation: partial unique index on `(image_hash)` where not null (or per meter), catch IntegrityError -> 409.
- Confidence: confirmed by reading.

### B-19 Weak validation of numeric and structured fields
- OWASP: A08 / A10. Component: `schemas/*`, `routers/meters.py`, `routers/properties.py`, `routers/readings.py`. Severity: **low**.
- Location: `schemas/readings.py:131`, `schemas/meters.py:44,53`, `properties.py:74-75`, `meters.py:228-229`, all `str` fields without `max_length`.
- Description: `value: Decimal` has no bounds (Numeric(12,3) overflow or `NaN`/`Infinity` tokens, negative values, read_at in the far future) -> DataError 500, or a stored NaN that later breaks float arithmetic in dashboard/forecast (`dashboard.py:43,228,307`) for the whole meter (persistent 500 on the dashboard summary = poisoned data). PATCH with `{"name": null}` on property/meter writes NULL into a NOT NULL column (500). `integration_config: Any` -> a dict/list is passed to a `Text` column (psycopg2 "can't adapt" 500) or stored as arbitrary JSON; plaintext at rest although the response redacts it. Over-long strings exceed `String(255/100/150)` -> 500. `ReadingCreate.source`, see B-17. `list_readings`/`consumption` return unbounded rows (consumption has no limit at all, `dashboard.py:176`).
- Reproduce: field-fuzz matrix per endpoint: `{"value":"NaN"}`, `1e30`, `-1`, `{"name":null}`, 10 000-char names, `integration_config` as object/array/number, then call `/api/dashboard/summary`.
- Recommendation: `Field(max_length=...)`, `condecimal(allow_inf_nan=False, max_digits=12, decimal_places=3, ge=0)`, reject explicit null for required fields (`exclude_none` or validators), type `integration_config` strictly.
- Confidence: confirmed by reading (NaN handling by pydantic/Postgres suspected).

### B-20 `delete_user` probably fails with 500 for users with property links
- OWASP: A10. Component: `routers/admin.py`, `models.py`. Severity: **low**.
- Location: `admin.py:204-216`, `models.py:User.property_users` (no `cascade`, no `passive_deletes`).
- Description: `db.delete(user)` with a loaded one-to-many without delete cascade makes SQLAlchemy first set `property_users.user_id = NULL` (NOT NULL) -> IntegrityError instead of the DB-level `ON DELETE CASCADE`. SQLite-based unit tests may not show it. If true, offboarding (B-04) by deletion does not work for any assigned user and surfaces as 500.
- Reproduce: assign a user to a property, `DELETE /api/admin/users/{id}` on the PostgreSQL rig.
- Recommendation: `passive_deletes=True`/`cascade="all, delete-orphan"` on the relationship; add a test on PostgreSQL.
- Confidence: suspected.

### B-21 Startup depends on an external fetch and unguarded DB writes
- OWASP: A10 / A06. Component: `main.py`, `services/oil_price.py`. Severity: **low**.
- Location: `main.py:417-428` (`await _run_oil_price()` inside `lifespan`), `oil_price.py:42-58` (read-then-insert; `date.today()` local time; commit not wrapped), `oil_price.py:37` .
- Description: the fetch errors are caught but `db.commit()` exceptions are not: two replicas starting together both see no row for today and both insert -> unique-constraint IntegrityError in `lifespan` -> pod crash loop; a hostile/buggy `custom` source returning `1e30`/`nan` (`float(price)` unchecked) -> DataError -> same crash at every start. Startup also blocks up to 15 s on an external site (readiness delay); scraper output is only a float parse, no plausibility range, no transport allow-list for `oil_price_api_url`.
- Reproduce: `OIL_PRICE_SOURCE=custom` pointing to a stand-in returning `{"price_per_100l": 1e30}`; start the backend, expect crash; or start two instances concurrently on an empty table.
- Recommendation: wrap in try/except + rollback, upsert with `ON CONFLICT`, validate range (e.g. 10..500), run fetch as a background task not in startup.
- Confidence: confirmed by reading.

### B-22 Unauthenticated `/auth/login` writes to the database on every call
- OWASP: A06. Component: `routers/auth.py`. Severity: **low**.
- Location: `auth.py:629-637` (`_cleanup_expired_pkce` DELETE + commit + INSERT + commit per call, no rate limit).
- Description: each anonymous `GET /api/auth/login` runs a DELETE over the table and inserts a row that lives 10 minutes; also triggers an OIDC discovery fetch whenever the cache is empty (cached 1 h, errors are not cached so a down IdP turns every call into a 10 s outbound request and a 500 `httpx` error). `callback` has the same exposure but needs a valid state.
- Reproduce: 5 000 anonymous `GET /api/auth/login` (no follow redirects), count rows in `oidc_pkce_states`, measure latency; repeat with the fake IdP offline.
- Recommendation: rate limit `/login` and `/callback`, hard cap on pending states, map IdP outage to 502/503 with a short negative cache.
- Confidence: confirmed by reading.

### B-23 Oil-price API key can be logged
- OWASP: A09 / A04. Component: `services/oil_price.py`. Severity: **low** (feature mostly unimplemented).
- Location: `oil_price.py:92-96`, `35-37` (`logger.error("... %s", exc)`).
- Description: the key is embedded in the URL query; `httpx.HTTPStatusError` text contains the full URL, and the generic `except Exception` logs `str(exc)`. The same pattern for `oil_price_api_url`.
- Reproduce: `OIL_PRICE_SOURCE=tankerkoenig` with a stand-in returning 401; grep the log for `apikey=`.
- Recommendation: pass the key as header/params and log only exception class + status.
- Confidence: confirmed by reading.

### B-24 Any authenticated user can consume LLM/OCR resources
- OWASP: A10 / A06. Component: `routers/ocr.py`, `limiter.py`. Severity: **low**.
- Location: `ocr.py:258-354`, `ocr.py:199-255`.
- Description: `scan` (no `meter_id`) and `bulk-scan` need only a valid token; a user without any property assignment (e.g. freshly created from the IdP group `user`) can drive the GPU/gateway and fill the upload volume (each scan writes a new file, up to 10 MB, 20/min/IP, no per-user quota; orphans live until the nightly job + 24 h grace). Limits are per IP (spoofable, B-02) not per user. Gateway path is "local only" (good), thread pool of `asyncio.to_thread` (default size) is shared with the rest of the app and is occupied for up to 300 s per call.
- Reproduce: token of an unassigned user, 20 scans/min for 10 minutes, `df` on the upload dir, latency of `/api/auth/me`.
- Recommendation: per-user quotas and a global concurrency semaphore for LLM calls, require property assignment, disk quota for uploads.
- Confidence: confirmed by reading.

### B-25 OCR service: no authentication, unvalidated passthrough
- OWASP: A02 / A08. Component: `ocr-service/app/main.py`, `services/ocr_pipeline.py`. Severity: **low** (NetworkPolicy exists in the chart; Compose publishes the port, check).
- Location: `ocr-service/app/main.py:61-85` (no auth, `await file.read()` without limit, binds `0.0.0.0`), `ocr_pipeline.py:653-658` (`raw_texts` returned unfiltered), `ocr_pipeline.py:644-650`.
- Description: anyone who can reach port 8100 can run EasyOCR/denoise on arbitrary images (B-10). The backend forwards `raw_texts`/`detected_value`/`detected_serial` from the service to the browser without type/size checks (a compromised or buggy OCR pod controls the response shape).
- Recommendation: shared-secret header check in the service (the backend already sends `OCR_API_KEY`), size limit, response schema validation (`pydantic`) in the backend.
- Confidence: confirmed by reading.

### B-26 Public API docs and verbose health endpoint
- OWASP: A02. Component: `main.py`. Severity: **low**.
- Location: `main.py:434-441` (docs/openapi enabled in production regardless of `debug`), `503-554`.
- Description: `/api/docs` and `/api/openapi.json` expose the full attack surface unauthenticated. `/api/health` is unauthenticated, reports version, LLM model name and the state of DB/OCR/LLM, and performs up to three outbound HTTP calls (3 s each, `gateway_healthy` sends the gateway API key) per request without rate limit (amplification). `db = next(get_db())` is closed only on success, so a DB error leaks one pooled connection per call. CORS is correctly restricted to `app_base_url` (+ localhost only in debug).
- Reproduce: `curl /api/openapi.json`; flood `/api/health` with the OCR stand-in delayed 3 s and watch worker threads/open connections.
- Recommendation: disable docs when not `debug`, split liveness/readiness (readiness cluster-internal only), cache health results for a few seconds, close the DB in `finally`.
- Confidence: confirmed by reading.

### B-27 Supply-chain and container hygiene
- OWASP: A03 / A02. Component: `backend/Dockerfile`, `ocr-service/Dockerfile`, `backend/requirements*.txt`. Severity: **low**.
- Location: `Dockerfile:1,13-14,16,25-26`, `ocr-service/Dockerfile`, `requirements-test.txt:5`.
- Description: base image `python:3.12-slim` by tag, not digest; `pip install` without `--require-hashes`/lock file (runtime pins are exact, which is good, but transitive dependencies float); no `.dockerignore`, so `COPY . .` bakes tests and any local `.env` (read by `Settings(env_file=".env")`) into the image; `requirements-test.txt` uses open `>=` ranges and includes unmaintained `python-jose` (the app itself uses authlib, so it is test-only, verify it is not installed in the runtime image); `alembic upgrade head` runs in the container's start command, so N replicas migrate concurrently (no migration lock in Alembic for PostgreSQL); shell-form `CMD` leaves `sh` as PID 1 so SIGTERM does not reach uvicorn (no graceful shutdown); EasyOCR models are downloaded at build time without checksum. Positive: non-root user, minimal runtime packages, exact pins for runtime requirements, `psycopg2-binary` only.
- Recommendation: pin by digest, add a `pip-compile --generate-hashes` lock, `.dockerignore`, `exec uvicorn`, migrate in an init Job.
- Confidence: confirmed by reading; `pip-audit`/Trivy results are for a separate task.

### B-28 LLM/OCR output is not constrained before it reaches the client
- OWASP: A08. Component: `services/ocr_pipeline.py`, `routers/ocr.py`. Severity: **low**.
- Location: `ocr_pipeline.py:426-470,480-485`, `ocr.py:354,571-584`.
- Description: values are regex-cleaned (`[^\d.,]`, >= 3 digits) but there is no range/monotonic check; `detected_serial` is only whitespace/hyphen-stripped and may be arbitrary text of any length (including markup) returned in JSON and in the SSE stream (frontend must not use `v-html`: separate frontend review). Text printed in the photo can steer the model (prompt injection) toward other digits or a serial of a different meter, but serial matching only considers meters the caller can access (`meters` list built from `accessible_property_ids`), and the result is a proposal the user confirms (nothing is stored by the OCR endpoints). Good: serial==reading guard, gateway errors mapped to fixed texts (no upstream message), no body/key logging, gateway timeout minimum 300 s enforced.
- Reproduce: gateway stand-in returning `"serial":"<img onerror=...>"`, 100 kB strings, extra keys, nested JSON, `reading` as list; check the response and the SSE frame.
- Recommendation: cap length/charset (`^[A-Za-z0-9]{1,40}$`) for serial, numeric bounds for reading, validate OCR-service response with a schema.
- Confidence: confirmed by reading.

### B-29 Existence oracles via 404-before-403
- OWASP: A01. Component: `permissions.py`, `routers/*`. Severity: **info**.
- Location: `permissions.py:280-284,311-323`, `ocr.py:212-221,303-308`, `dashboard.py:161-169`.
- Description: property and meter existence is checked before access, so a non-member distinguishes 404 from 403 for property/meter UUIDs; `rescan/{reading_id}` enumerates sequential integer reading ids by the same 404/403 difference (ids are `BigInteger` autoincrement). UUIDs make the first two impractical; the reading-id oracle is practical.
- Recommendation: answer 404 for both when the caller lacks access.
- Confidence: confirmed by reading.

### B-30 Header reflection and log forging
- OWASP: A05 / A09. Component: `main.py`, `routers/ocr.py`. Severity: **info**.
- Location: `main.py:462-467` (client `X-Request-ID` echoed verbatim, length unlimited, never logged), `ocr.py:657` and `653` (`original_filename` from the multipart part and exception text logged), `ocr.py:94`, `oil_price.py:36`, `llm_client.py:186` (gateway-supplied `model` string logged raw).
- Description: h11/uvicorn rejects raw CR/LF in header values, so response splitting is not possible; filenames and upstream strings in log format arguments can contain control characters or very long text (log line forging / noise).
- Recommendation: validate request-id format (`^[A-Za-z0-9-]{1,64}$`), `repr()`/escape client-derived log arguments.
- Confidence: confirmed by reading.

### B-31 Cross-property meter reference and plaintext integration config
- OWASP: A01 / A04. Component: `schemas/meters.py`, `models.py`. Severity: **info**.
- Location: `meters.py:228-229`, `schemas/meters.py:55`, `models.py:Meter.integration_config`.
- Description: `replaced_by_id` may point to any meter UUID, including another tenant's (FK only); `replaced_at` can be set without a successor; replaced meters are then un-editable (409) so a mistake or hostile PATCH is irreversible through the API. `integration_config` ("url, token, entity_id") is stored as plaintext text in PostgreSQL; the API redacts it on output (good) but backups and DB readers see tokens. No consumer of the config exists in `backend/app` today (grep), so the field is currently a dormant secret store.
- Recommendation: verify `replaced_by_id` belongs to the same property; encrypt or drop `integration_config` until used.
- Confidence: confirmed by reading.

---

## Areas checked with no finding (evidence)

### A01 Broken access control
Endpoint-by-endpoint check of the dependency chain (all routers mounted at `/api`):

| Endpoint | Check | Result |
|---|---|---|
| `GET/POST /properties/`, `DELETE /properties/{id}` | list filtered by `accessible_property_ids`; create/delete `require_admin` | ok |
| `GET /properties/{id}`, `/{id}/users` | `get_property_or_404` + `read` | ok (404/403 oracle, B-29) |
| `PATCH /properties/{id}`, users add/remove | `write` only | **B-05** |
| `/properties/{p}/meters/*` | every route calls `require_property_access` with p from the path and filters `Meter.property_id == p` (no cross-property meter via IDs); delete needs `delete` | ok |
| `/properties/{p}/meters/{m}/readings/*` | `get_meter_with_access` (meter must belong to p) and readings filtered by `meter_id` | ok; no mass assignment of `meter_id`/`id` (schema excludes them; `ReadingUpdate` only value/read_at/note; `PropertyUpdate`/`MeterUpdate` only whitelisted fields, `property_id` not patchable) |
| `GET /dashboard/*` | meter/property -> membership via `_accessible_property_ids` (403), summary restricted to own properties; oil price is global by design | ok |
| `/ocr/rescan/{reading_id}` | access derived from the reading's meter | ok |
| `/ocr/scan`, `/ocr/bulk-scan` | meter/property access `read`; bulk meter list and duplicate lookup scoped to accessible meters (no leak of foreign readings, verified at `ocr.py:544-560` and `readings.py:270-281`) | ok |
| `/ocr/hint-rescan` | meter access ok, image path not tied to caller | B-07 related, see below |
| `/admin/*` | `require_admin`; only superadmin may grant superadmin | ok except B-06 |
| `/uploads/{filename}` | auth required; traversal check (`resolve().relative_to`), `%2F` cannot reach the `{filename}` param, symlinks resolved before the check | ok for traversal; tenant logic B-07 |
| role derivation | `get_current_user` reads role from DB; token `role` claim ignored; `is_active` checked per request | ok |
| `hint-rescan` / `scan` path handling | `Path(image_path.lstrip("/")).name` + `resolve()` + `is_relative_to` prevent traversal | ok for traversal; any UUID-named upload in the directory is accessible by name (B-07) |

Note on `hint-rescan`: `image_path` is not bound to the caller or to a reading, so a user with read access to any meter can have an arbitrary upload file (by UUID name) OCR'd and receives the extracted value back; same UUID precondition as B-07.

### A02 Misconfiguration
CORS correct (`allow_origins=[app_base_url]`, localhost only if `debug`, credentials with a fixed origin); `debug` default false; nginx/Traefik set CSP, HSTS (Traefik), nosniff, frame-deny, `Referrer-Policy` (`nginx.conf:5-9`, `middleware-security-headers.yaml`); container runs as non-root; default DB URL and JWT sentinel rejected (weak variants not: B-12); no stack traces to clients (no custom handler, FastAPI returns `Internal Server Error`); `FileResponse` is served as attachment with an extension-derived media type from a fixed allowlist of image types (SVG/HTML not accepted by the magic-byte allowlist, no stored-XSS via uploads).

### A03 Supply chain
Runtime requirements pinned with `==` (fastapi 0.135.3, authlib 1.6.9, pillow 12.3.0, python-multipart 0.0.32, etc.); `python-jose` is not in runtime requirements. Remaining items in B-27; `pip-audit`/Trivy output is out of scope for this file.

### A04 Cryptography, JWT, PKCE
`JsonWebToken([settings.jwt_algorithm])` restricts accepted algorithms to HS256 (no `none`, no alg confusion), signature verified in `decode`, `exp` validated if present; user lookup by UUID parsed safely (invalid -> 401). PKCE: 40 random bytes verifier, S256 challenge, `state` is `token_urlsafe(16)` (128 bit), single-use delete before the token exchange, 10-minute TTL enforced twice, `redirect_uri` fixed from config (no open redirect: the post-login redirect target is built from `app_base_url`, not from request input). Superadmin password compared in constant time (`compare_digest`, modulo B-16). No password hashing code path is reachable (`hashed_password` is never written; `bcrypt` is a dependency without use: confirm and drop). Remaining: B-03, B-04, B-12, B-13.

### A05 Injection
All DB access is ORM or parameterised; the only raw SQL is in `retention.py` (f-string builds only from static fragments `sources`/`where`, values bound as parameters, bandit `B608` nosec justified, verified) and `scripts/seed_oil_prices.py` (bound parameters). No `subprocess`, `eval`, `exec`, `pickle`, `os.system`, `yaml.load`, `verify=False` in `backend/app` or `ocr-service/app` (grep). File names for storage are generated server-side (`uuid4` + extension from verified MIME), never taken from the client; reads use `.name`/`resolve()` guards. SSRF: all outbound URLs (OIDC discovery, token/userinfo from discovery document, gateway, Ollama, OCR service, oil price) come from operator configuration or from the IdP's discovery document; no request parameter influences a URL. Residual: discovery-document trust (endpoints used as returned, no host pinning), `follow_redirects=True` only on the fixed scraping URL. Jinja/HTML templates are absent in the backend. Header injection: B-30 info.

### A06 Insecure design / races
First-login race on `oidc_sub` has an `IntegrityError` retry (`auth.py:78-87`) backed by the UNIQUE constraint; `PropertyUser (property_id,user_id)` and `OilMarketPrice (price_date,source)` have unique constraints, so these races end in 500 at worst, not in duplicates (add-property-user and oil-price paths give 500/crash, see B-15/B-21); PKCE state is single-use. Missing: B-02, B-13, B-18, B-22.

### A07 Authentication
Superadmin login is disabled when `SUPERADMIN_PASSWORD` is empty, uses constant-time comparison of both fields, generic error message (no user enumeration), `5/minute` limit (effectiveness: B-02). Users who are in no permitted group are rejected before any row is created or modified. Inactive users are rejected on every request. Role downgrade happens at every login. Remaining: B-01, B-02, B-04, B-06, B-14.

### A08 Integrity
`image_hash` computed server-side from the stored file (client field ignored); gateway failures map to fixed user-safe messages and never to "nothing read"; bulk scan stops after the first model outage; serial-equals-reading guard; OCR/LLM never writes to the database by itself. Remaining: B-17, B-19, B-28; CI pinning is covered by the infrastructure review.

### A09 Logging
`llm_client` documents and implements "no bodies, headers or keys"; httpx logging suppressed; `Settings.gateway_api_key` is `SecretStr`; no tokens, passwords or e-mail addresses are logged by the application code (grep of `logger.*` calls: values logged are meter ids, detected values, file names/paths). Missing events and the access-log query-string exposure: B-11, B-03.

### A10 Exceptional conditions
Fail-closed behaviours: `get_current_user` rejects on any decode error; health returns 503 only on DB/scheduler failure, optional subsystems cannot crash it; `get_db` rolls back on exceptions; LLM/OCR errors are caught per file in bulk-scan without aborting the stream; file deletes after commit (DB first, file second, no phantom rows); outbound calls all have timeouts (`gateway` 300 s read by design, OCR 30 s, OIDC 10/15 s, health 3 s); upload size limits exist for `scan` (bounded read) and are re-checked per file in bulk-scan. Partial-write scenarios found: scan writes the image before OCR (orphans cleaned by retention), `callback` deletes the state before the token exchange (a failed exchange needs a new login, acceptable). Remaining: B-08, B-09, B-10, B-16, B-20, B-21.

### Alembic and scripts
Migrations `0000-0004` are plain DDL; no data-dependent SQL and no secrets. Observations only: `0000` creates enum types (`metertype`, `meterunit`, `integrationtype`) with values different from the Python enums (`gas/heat/other`, `m3` vs `m³`, `smart_meter/shelly` missing): creating meters with `integration_type` `smart_meter`/`shelly` or unit `m³` on a database built purely from migrations would fail with a DB error (500) - verify on the PostgreSQL rig (functional/robustness, not exploitable). `scripts/seed_oil_prices.py` uses bound parameters and `DATABASE_URL` from the environment.

## Suggested experiment priorities
1. B-01 (string/dict/null claim matrix), B-02 (XFF spoof + shared-bucket), B-07 (cross-tenant image chain), B-05/B-06 (role matrix).
2. B-08/B-09/B-10 resource exhaustion on a memory-limited container (use the rig with `ulimit`/cgroup).
3. B-13/B-14/B-15 OIDC hostile-claim set with the fake IdP; B-16/B-19 field fuzzing; B-20 delete-with-assignments; B-18/B-21 barrier races.
4. B-11 grep run with `LOG_LEVEL=debug` after the experiments above.
