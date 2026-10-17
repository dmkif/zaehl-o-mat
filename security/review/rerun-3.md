# Rerun 3 (profile `--quick`, release 0.6.0 = features 007 session hardening, 008 bulk-scan limits, 009 schema/deploy hardening)

Command: `scripts/security-rig.sh --quick` (RIG_RACE_ROUNDS=20, RIG_RACE_SIGNINS=30, limiter off rig-wide). Branch `release/0.6.0`. Logs of the working runs are kept outside the repository (run-060 to run-070).

| | Rerun-2 (after 006) | First run on 0.6.0 (unadapted rig) | Rerun-3 (final) |
|---|---|---|---|
| Tests | 247 | 247 | 277 (30 new) |
| Passed | 206 | 104 | **257** |
| Failed | 40 | 141 | **19** (+ E-A10-001 intermittent, passed in the final run) |
| Skipped | 1 | 2 | 1 (A02 needs `SEC_RIG_APP_DEBUG=false`) |
| TEST/RIG-BUG failures | 0 | 120+ (see below) | **0** |
| Runtime (pytest) | 7 min 44 s | 2 min 57 s | about 8 min |

The 141 failures of the first run were almost all a consequence of feature 007, not regressions of the application: the experiments minted JWTs without the new `tv` claim (the rig then correctly answered 401 everywhere) and read the session token from the callback redirect query (which 007 removed on purpose). The final counts come from the full run `run-069` (20 failed, 256 passed) plus a targeted rerun of E-A04-001 after fixing one flaky assertion (see test defects, item 4): 19 failed, 257 passed.

## Test and rig defects found and fixed

1. **Token minting (all roles)**: `conftest.mint_token` now reads `users.token_version` from the rig database and adds `tv` and `iat` exactly like `issue_user_token`; the E-A04-001 forgery helper `claims()` does the same, so a forged variant differs from a valid token in exactly one property (before, many "rejected" assertions would have passed for the wrong reason: a missing `tv`). A caller can still pass `tv=<n>`.
2. **OIDC sign-in helpers** (`idp_helpers.py`): the callback redirect carries `#code=<one-time code>`; `code_of()` reads the fragment, `signin()` exchanges the code with `POST /api/auth/exchange` and returns the JWT. `token_of` stays as the presence check ("a session was issued") and now means "a login code was issued". New `jwt_in_url()` asserts that no JWT is in the Location header. E-A06-001, E-A09-001 (the code and the exchanged JWT are now log needles) were adapted accordingly. Assertions were not weakened.
3. **E-A01-001**: `POST /api/auth/exchange` is public by design (the one-time code is the credential) and has its own experiments (E-A07-005); it is on the allow-list and not probed there. The rest of the strict "401 without credentials" rule is unchanged.
4. **E-A04-001 `tampered_token_rejected`** (flaky, found on the final run): "last character of the token changed" is a valid token in one of four runs, because the last base64url character of a 32 byte HMAC carries two padding bits. The assertion now changes the second to last character, which always alters the signature bytes.
5. **E-A01-006** is now parametrised: `integration_config` as `dict` (what the schema advertises) and as `json_string` (what the Text column accepts). The old single case stopped before the SSRF half; now the `json_string` case runs it to the end.

## New experiments

| ID | What | Result |
|---|---|---|
| E-A07-005 (4 tests) | redirect has `#code=` and no JWT or `token=` anywhere, JWT has sub/role/tv/iat/exp; code single use; expired, unknown, empty, oversized, wrong-typed codes refused with 4xx and no token, only the hash is stored, expired row consumed; 15 parallel exchanges give exactly one 200 | pass |
| E-A07-006 (2) | logout kills the token and a second token of the same user, a fresh sign-in works, logout with garbage or no token is a harmless 200, other users keep their session | pass |
| E-A07-007 (3) | admin role change, promotion at sign-in and IdP group removal each revoke older tokens; a re-login without role change does not | pass |
| E-A10-003 (1) | with small limits: files over budget 413, too many files 400, declared Content-Length over limit 413 from the headers alone, chunked body over limit 413, no stored file and no OCR/gateway request, control streams | pass after the app fix below |
| E-A10-004 (1) | no `idle in transaction` database session while a bulk scan waits 9 s for the gateway | pass |
| E-A02-003 (13) | all 9 meter type x unit pairs and all 4 integration types creatable and read back on the migration-built database | pass |
| E-A04-001 `token_version_claim_enforced` (5) | missing, stale, future, string and null `tv` rejected, exact `tv` accepted | pass |

## Real application defect found and fixed (small, with regression test)

E-A10-003 showed that a chunked bulk-scan body (no Content-Length) exceeding the route limit was refused with **400 "There was an error parsing the body"**, not the 413 required by `specs/008-bulk-scan-limits/spec.md` (acceptance 4). The refusal itself held (reading stopped, nothing stored), but the status was wrong because the `HTTPException` raised from the ASGI `receive` wrapper was turned into a 400 by the framework. Fix in `backend/app/body_limit.py`: past the limit the wrapper ends the stream like a disconnect and replaces whatever the application answers by the 413 response. Regression test: `backend/tests/test_bulk_scan_limits.py::test_chunked_body_over_route_limit_is_413`; the whole backend suite passes (633 tests).

No other regression of 007, 008 or 009 was found.

## Remaining failures: all REAL (open findings of the low class, none touched by 007 to 009)

| Experiment (tests) | Finding | Verdict |
|---|---|---|
| E-A01-005 meter PATCH with foreign `replaced_by_id` | F-037 | REAL |
| E-A01-006 `[dict]` meter create with a dict `integration_config` gives 500 (schema `Optional[Any]`, column `Text`) | F-023 | REAL (the enum cause F-014 is gone; the `[json_string]` case passes, so no SSRF) |
| E-A02-002 docs x2, health detail, authenticated error bodies (overflow, huge value, long name/serial, NUL give 500), X-Request-ID reflection | F-031, F-023, F-046 | REAL |
| E-A04-001 `unusual_headers_rejected` (`zip: DEF` accepted) | F-034 | REAL (residual) |
| E-A05-001 json fields, query/path, multipart (NUL, `1e999`, offset overflow, callback `state` NUL) | F-023 | REAL |
| E-A06-002 same image hash x2 (several 201 for one hash), identical reading (20 rows) | F-022 | REAL |
| E-A06-004 `state_bound_to_initiating_browser` (a different browser completes the login; now yields a login code) | F-017 | REAL |
| E-A10-002 IdP slow gives 500, cold-cache login with IdP down gives 500 | F-026 | REAL |
| E-A10-001 PostgreSQL stopped mid-request (partial write; intermittent, failed in the first rerun-3 run) | F-035 | REAL, intermittent |
| E-UP-001 truncated/broken images stored, EXIF GPS kept | F-032, F-033 | REAL |

## Newly passing since rerun-2 (verified by experiment)

* F-007: E-A06-003 `privilege_not_kept_after_idp_removal`, E-A07-006, E-A07-007, E-A04-001 `token_version_claim_enforced`.
* F-008: E-A07-005, E-A09-001 (no JWT or login code in logs).
* F-012: E-A10-003 (plus E-UP-001 `size_limit_enforced`). Traefik middleware by reading only.
* F-013: E-A10-004 (was suspected, now confirmed fixed).
* F-014: E-A02-003, E-A05-001 `enum_members_do_not_crash`.
* F-028 (orphan files): E-A08-001 `failed_scan_leaves_no_orphan_file`, E-A10-002 `gateway_ocr_failure_modes_fail_closed`, `optional_subsystems_unconfigured`. Quota part open.
* F-029: E-A08-001 (all 10 previously failing tests), E-A10-002 garbage, E-UP-001 `exif_date_abuse_and_oversize_exif`.
* Not demonstrable by the rig: F-015 (Compose) and F-016 (chart NetworkPolicy), verified by reading and `helm template` only; F-030 stays open (no authentication on the OCR service, only mitigated by F-016).

## Observations

* The mutant patches in `security/mutants/` were not re-run; the ones touching `ocr.py`, `auth.py` or `models.py` may need re-basing on 0.6.0.
* The experiments run against a rig where `DEBUG=true`; the A02 debug-off test is skipped as before.
