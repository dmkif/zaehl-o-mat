# Rerun 4 (profile `--quick`, release 0.7.0 = features 010 auth/HTTP, 011 data/OCR, 012 hygiene)

Command: `scripts/security-rig.sh --quick` (RIG_RACE_ROUNDS=20, RIG_RACE_SIGNINS=30, limiter off rig-wide). Branch `release/0.7.0` (v0.6.1 + features 010 to 012). Logs of the working runs are kept outside the repository.

| | Rerun-3 (0.6.0) | First run on 0.7.0 (unadapted rig) | Rerun-4 (final) |
|---|---|---|---|
| Tests | 277 | 277 (9 failed, 4 setup errors) | 291 (20 new, the 7-way docs parametrisation merged into one test: 277 + 20 - 6) |
| Passed | 257 | 263 | **290** |
| Failed | 19 | 9 (+ 4 setup errors) | **0** |
| Skipped | 1 | 1 | 1 (A02 debug-off test needs `SEC_RIG_APP_DEBUG=false`) |
| TEST/RIG-BUG failures | 0 | 12 of 13 (see below; the 13th was F-034 `zip`, real) | **0** |
| Application defects found | - | 1 visible at once (`zip`), 2 more after the rig was adapted | 0 open |
| Runtime (pytest) | about 8 min | 5 min 55 s | 9 min 21 s |

## Rig and experiment adaptation (no assertion weakened)

1. **Rig environment** (`rig/up.sh`): `ENABLE_API_DOCS=true` (4 E-A05-001 setups and E-A01-001 need `/api/openapi.json`), `OIDC_RATE_LIMIT=100000/minute`, `OCR_API_KEY` on backend and `fake_ocr --api-key`. Secrets are now passed as `--opt=value` (a random key starting with `-` made the argparse of a stand-in fail the start-up, a latent rig flake).
2. **`idp_helpers.py`**: `start_flow` keeps the `oidc_binder` cookie per `state`; `callback()` sends the binder of that login (explicit `binder=` overrides). This is also what keeps E-A06-001 (30 parallel flows on one client) correct, because a shared cookie jar only holds the last binder. E-A06-004 `state_bound_to_initiating_browser` sends no cookie for browser B (`binder=None`) and now requires exactly 400. `fake_oidc` gained `omit` (claim absent) for the `sub` experiments.
3. **E-A02-002** `api_documentation_not_public` restarts the backend with `ENABLE_API_DOCS` unset (default) and asserts 404 for all seven paths, plus a control with `true`. `health_endpoint_minimal_to_anonymous` asserts exactly `{status}`; new `health_details_need_login...`. `request_id_header...` no longer sends a tab (the HTTP client refuses it before the wire; the 8-KB, space, markup, quote, 65-char, slash and empty cases remain) and asserts the replacement is a safe token and a valid id is propagated.
4. **E-A01-006** `json_string`: a non-object `integration_config` is now a strict 422 (the old shape the Text column accepted); the SSRF half runs on the `dict` form.
5. **E-A10-001 / E-A10-002**: anonymous `/api/health` no longer carries `db`/`ocr`; E-A10-001 asserts 503 + `{status: degraded}` (and nothing else), E-A10-002 reads `ocr` from `/api/health/details` as admin.
6. **E-A06-002 `parallel_identical_reading`** encoded "one stored row" for readings without image. Spec 011 decides the opposite on purpose (no uniqueness without image, image hash is the only key). The experiment now asserts the decision: 20 parallel posts, 20 x 201, 20 rows, no 5xx, none lost. The strict image-hash variants are unchanged and pass.
7. `coverage_list.py` sets `ENABLE_API_DOCS=true` before importing the app.

## New experiments

| ID | What | On v0.6.1 | On 0.7.0 |
|---|---|---|---|
| E-A02-004 | `GET /api/properties` redirect keeps `https://` behind a trusted proxy (`X-Forwarded-Proto`), ignores it from an untrusted peer, ignores unknown schemes, last hop wins | pass (0.6.1 already carries the fix; regression guard) | pass |
| E-A07-008 (3) | login CSRF: cookie attributes; no/foreign/tampered cookie -> 400, no user, state consumed; wrong nonce -> 400 | fail | pass |
| E-A07-009 (10) | `sub` absent/null/empty/blank/int/list/object/bool/300 chars -> 502 and superadmin row unchanged; username collisions; e-mail collision -> 409 | fail (500 for collisions) | pass |
| E-A07-010 (2) | real `ocr-service` app (EasyOCR stubbed) needs Bearer key; backend with wrong key fails closed | the ocr-service half tests the repository app (pass by construction); backend half passes (key support existed) | pass |
| E-A10-005 | DB stopped: GET/POST/PATCH answer the generic 503 + Retry-After | fail | pass |
| E-A01-007 | delete user with property links -> 204 | fail (500) | pass |
| E-A01-008 | client-chosen `source` stored as `manual` | fail (`auto` stored) | pass |

Comparison run: the same experiments against `git archive v0.6.1 backend` (`SEC_BACKEND_DIR`): 26 failed, 7 passed of 33 selected; the 7 passes are the controls and guards named above.

## Application defects found by the rig (small, fixed in `backend/`, regression tests added)

1. **`GET /api/auth/callback?state=a%00b` gave 500** (E-A05-001 `query_and_path_parameters`, F-023 residual in the new callback): the NUL reached PostgreSQL. `_consume_pkce_state` now answers 400 for NUL or a state longer than 256 characters before any query. Test: `tests/test_oidc_login_hardening.py::test_malformed_state_rejected_before_any_db_query`.
2. **Regression in the new e-mail conflict check (feature 010, F-019)**: in 1 of 20 rounds of E-A06-001 one of 30 parallel first sign-ins of the same identity got 409. The request looked up the `sub`, a sibling request committed, then the e-mail check found "its" row and raised the conflict. `get_or_create_user_from_oidc` now re-reads by `sub` when the e-mail is taken and treats that as the sign-in. Test: `test_concurrent_first_login_of_same_identity_is_not_an_email_conflict` (red before, green after; the final run had 0 failing rounds).
3. **F-034 residual**: a correctly signed token with the header `zip: DEF` was accepted (E-A04-001, listed REAL in rerun-3). `decode_access_token` now refuses headers other than `alg`, `typ`, `kid`. Test: `tests/test_auth.py::test_decode_rejects_extension_headers`.

Backend suite after the fixes: 783 passed.

## Verification status of each finding in this release

Verified by rig experiment (also failing on v0.6.1 where noted in section 4 of the report): F-017 (E-A06-004, E-A07-008), F-018 and F-019 (E-A07-009), F-021 (E-A01-008), F-022 (E-A06-002), F-023 (E-A05-001, E-A02-002, E-A01-006), F-024 (E-A01-007), F-026 IdP part (E-A10-002), F-030 (E-A07-010), F-031 and F-046 (E-A02-002), F-032 and F-033 (E-UP-001), F-034 (E-A04-001), F-035 (E-A10-001, E-A10-005), F-036 (E-A01-002), F-037 (E-A01-005), F-020 (E-A07-004).
Backend unit tests only, not demonstrable by the rig: F-025, F-027, the rate limit and pending-state cap of F-026, the retention part of F-021.
By reading / lint only (config, docs, CI, scripts, frontend): F-039, F-040, F-041 (plus E-A02-002 headers), F-042, F-043, F-044, F-047, F-048, F-049; the Compose and chart wiring of F-030 was not re-rendered. F-045 documented, unchanged.

## Remaining real failures

None. Open findings that no experiment covers (not part of features 010 to 012): F-038, F-050, the quota part of F-028, digests/signing/SBOM (F-040), Mako/pip in the image (F-044).

## Observations

* The mutant patches in `security/mutants/` were not re-run (backend moved by features 010 to 012; they may need re-basing).
* The local `main` branch of the repository is far behind `v0.6.1`; use the tag for before/after comparisons.
