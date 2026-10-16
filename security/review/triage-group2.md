# Triage group 2: test_a06_races (8), test_a07_authentication (38 incl. 36 params of E-A07-003), test_a09_logging (1), test_a10_exceptional (11)

Source: `security/rig/.last-logs/full-run-1.log` plus `security/rig/.last-logs/backend.log` (385k lines, the rig backend log of the same run), tests in `security/experiments/`, backend code read-only. Rig not started, no test or app code edited.

Result: 58 failing items = **28 REAL**, **30 TEST/RIG BUG**.
Important: most "TEST/RIG BUG" items hide a latent verdict. The rig defect masks a result that is very likely REAL once fixed (listed under "Expected after rig fix").

## Root causes (shared by many items)

1. **Fake IdP gives every identity the same `preferred_username` (`rig-oidc`)** (`security/rig/standins/fake_oidc.py:107-108`, default `p.get("preferred_username", "rig-oidc")`; `idp_helpers.set_claims` at `security/experiments/idp_helpers.py:79-83` only varies `sub` and `email`). The first successful first-sign-in creates user `rig-oidc`; every later new identity hits `users_username_key` at `backend/app/auth.py:66-79` -> `IntegrityError` -> retry path finds no row for that `sub` -> `raise` (`auth.py:85`) -> HTTP 500. Backend log: 1207 `UniqueViolation ... users_username_key` (first at line 22801, `Key (username)=(rig-oidc)`). This is also the real app weakness **B-15** (low), but the tests are not written to test B-15, so it is a rig bug for them. **Fix (rig):** in `set_claims` add `extra.setdefault("preferred_username", f"u-{sub}")` (sub is `sub-<uuid>`/`<prefix>-<16 hex>`, well under the 100-char limit). Keep one dedicated B-15 experiment that sends the same username twice on purpose.
2. **Superadmin stays demoted after `test_a01_access_control::test_E_A01_003_admin_cannot_touch_superadmin`** (`test_a01_access_control.py:582`: admin got 200 on demoting the superadmin = REAL B-06). The test's `finally` (`:585-587`) tries to restore through the superadmin client, but that client is now a plain `user`, so the restore PATCH gets 403 and is silently ignored. Every later `users_by_email(superadmin, ...)` (`idp_helpers.py:136-139`) fails with `admin/users -> 403`. Visible in A06-001 (`'AssertionError: admin/users -> 403'`) and 13 A07-003 params. **Fix (test):** restore with direct SQL in the `finally`, as `_age_pkce_state` in `test_a06_races.py:29-39` already does: `UPDATE users SET role='superadmin' WHERE id=:sid` (and `role='admin'` for the admin id). Better: a function-scoped autouse fixture in `conftest.py` that re-asserts the seed roles via SQL after each test (cheap, protects all files). The app-side cause stays B-06 (the break-glass login does not restore the role either, `routers/auth.py:172-186`).
3. **Backend wedged by the race flood** (`test_a06_races.py` A06-001 and A06-003 post 100 parallel callbacks per round). Backend log: 311 x `sqlalchemy.exc.TimeoutError: QueuePool limit of size 10 overflow 20 reached, connection timed out, timeout 30.00`, traceback `routers/auth.py:149` -> `auth.py:56` (`db.query(User).filter(User.oidc_sub == sub)`). While this happens even a single `POST /api/properties/` (A06-002 `make_meter`) times out after 90 s. Likely mechanism (confirm after the rig fix): `callback` is `async def` (`routers/auth.py:100`) but does synchronous SQLAlchemy calls on the event loop; the request session is closed by the sync-generator dependency `get_db` (`database.py:28-35`), whose teardown runs in the threadpool and therefore needs the event loop; the handlers of requests 31+ block the loop in `QueuePool.checkout` (pool 10+20, `database.py:12-14`, 30 s timeout) while the 30 connections of finished requests wait for a teardown that cannot be scheduled. Result: whole-process stall of up to 30 s per queue slot and 500s. Needs a valid IdP code per request, so it is reachable by any account at the IdP that is in a permitted group (B-24 level), not anonymously. **New candidate (proposed B-32, medium):** "async handlers with blocking DB I/O + sync dependency teardown can stall the whole API under ~30 parallel logins (closest: B-09, B-22)". Fix: make `callback`/`login` plain `def` (threadpool) or use `run_in_threadpool` for the DB part; add a connection-wait semaphore; set `pool_timeout` small (5 s) so failures are fast 503s.

## Table

Legend: `rig fix 1` = unique `preferred_username` in `set_claims`; `rig fix 2` = restore superadmin by SQL. Severity only for REAL.

### test_a06_races

| Test | Verdict | Reason (log evidence -> code) | Sev. | Candidate | Proposed fix / expected after rig fix |
|---|---|---|---|---|---|
| E_A06_001 parallel_first_signin_one_user | TEST/RIG BUG | 100/100 rounds failed: round 1 `admin/users -> 403` (root cause 2), the others `ReadTimeout`/500 (root causes 1 and 3). Never reached the "one user row" assertion. | - | (B-15 / new B-32 behind it) | rig fix 1 + 2. Expected after fix: possibly REAL B-32 (pool stall at 100 parallel callbacks); then lower `RIG_RACE_SIGNINS` to 25 for the "one row" check and keep a separate 100-wide load test. |
| E_A06_002 parallel_same_image_hash[distinct_files_same_bytes] | TEST/RIG BUG | `ReadTimeout` already at `make_meter` (`POST /api/properties/`, `idp_helpers.py:226`), before any race: backend still wedged by A06-001 (root cause 3). Not a verdict on B-18. | - | B-18 (unevaluated) | Fix 001 first; in `make_meter`/fixtures call `wait_healthy` (exists, `idp_helpers.py:208`) and `restart_backend` after a flood test. Expected: REAL B-18 (check-then-insert, `routers/readings.py:62-86`, no unique index) with codes.count(201) > 1, low-medium. |
| E_A06_002 parallel_same_image_hash[same_path] | TEST/RIG BUG | Same as above (ReadTimeout at `make_meter`). | - | B-18 | Same. |
| E_A06_002 parallel_identical_reading | TEST/RIG BUG | Same (ReadTimeout at `make_meter`). | - | (B-18 family; the test itself notes the app has no uniqueness rule, so duplicates are expected) | Same. Expected: fails with N identical rows; decide whether that is a finding or an accepted design (no unique rule, test comment says so). |
| E_A06_003 role_change_vs_signin | TEST/RIG BUG | 100/100 rounds: `ReadTimeout` or `initial sign-in failed: 500` (username collision, root cause 1/3). | - | - | rig fix 1 + 2 + isolation. Expected: probably pass; role flap is serialized by single-threaded handlers. |
| E_A06_003 privilege_not_kept_after_idp_removal | TEST/RIG BUG | `admin sign-in failed: 500` (username collision). Assertion on the stale-privilege behavior never ran. | - | B-04 | rig fix 1. Expected after fix: REAL B-04 (medium): second sign-in 403, but row keeps `role=admin`, `is_active=True` and the old token keeps working (`auth.py:44-54` rejects before touching the row; `get_current_user` `auth.py:90-111` reads role from DB). Fix: deactivate/demote on group-rejection + `token_valid_after`. |
| E_A06_004 state_replay_rejected | TEST/RIG BUG | `first callback failed: 500` (collision). | - | - | rig fix 1. Expected: pass (state deleted at `routers/auth.py:118-120`, replay -> 400). |
| E_A06_004 parallel_state_reuse | TEST/RIG BUG | Each round `[400, 500]`: one request wins the state and then dies on the collision (500), the others correctly get 400. The atomic-consume part works. | - | - | rig fix 1. Expected: pass on one worker (read/delete/commit have no `await` between them, `routers/auth.py:107-120`). Not valid for multi-replica; note in test docstring. |
| (passes, but unreliable) E_A06_004 state_bound_to_initiating_browser | **false PASS** | Only asserts `not token_of(r)`; the 500 from the collision also has no token. | - | B-13 | After rig fix 1 it will FAIL (REAL B-13, low): browser B completes browser A's login. Add `assert r.status_code in (400,401,403)`. |

### test_a07_authentication

| Test | Verdict | Reason | Sev. | Candidate | Proposed fix |
|---|---|---|---|---|---|
| E_A07_001 limit_not_bypassable_by_forwarded_for | **REAL** | 40 distinct `X-Forwarded-For` values -> 40 evaluated 401s (limit 5/min). Rig starts uvicorn without the Dockerfile flags (`rig/up.sh:153`), but uvicorn trusts `127.0.0.1` by default, so the effect equals production's `--forwarded-allow-ips='*'` (`backend/Dockerfile:26`); `app/limiter.py:4` keys on `get_remote_address`. | high | B-02 | Key the limiter on the socket peer or a trusted-proxy hop count; keep forwarded-ips narrow; add per-username counter in DB. Optional rig fidelity: pass the same flags as the Dockerfile in `up.sh` and `_RestartBackend._start`. |
| E_A07_004 nonascii_and_malformed_credentials_no_5xx | **REAL** | First body (non-ASCII password) -> 500, `TypeError: comparing strings with non-ASCII characters` at `routers/auth.py:168` (log line 355530). Next request got `httpx.ReadError` (connection dropped after the unhandled error), which the test does not catch, so bodies 3-5 (100 kB password, `null`, wrong types) were never evaluated. | low | B-16 | Compare `.encode()` bytes or HMAC digests; bound field lengths in `SuperadminLoginRequest`. Test: catch `httpx.HTTPError` per body, record as bad, open a fresh client per body. |
| E_A07_003 [list_admin] | TEST/RIG BUG | 500 at user INSERT (collision, root cause 1). Legit exact-list sign-in. | - | B-15 | rig fix 1. Expected pass. |
| E_A07_003 [list_manager] | TEST/RIG BUG | same | - | B-15 | same |
| E_A07_003 [list_user] | TEST/RIG BUG | same | - | B-15 | same |
| E_A07_003 [list_user_and_admin_highest_wins] | TEST/RIG BUG | same | - | B-15 | same |
| E_A07_003 [string_exact_admin_documented] | TEST/RIG BUG | same (exact string equals the admin group; test accepts admin or reject) | - | B-15 | same |
| E_A07_003 [huge_list_admin_last_exact] | TEST/RIG BUG | same (100 001-element list incl. exact group, legit) | - | B-15 | same |
| E_A07_003 [string_substring_xadminx] | **REAL** | Sign-in reaches the INSERT (500 by collision). The reject path is a 403 before any DB access (`auth.py:44-54`), so the mapping granted a role: `"rig-admins" in "xrig-adminsx"` is True (`auth.py:44`). After rig fix 1 it returns 302 + admin token. | high | B-01 | Require `isinstance(groups, list)` of `str`, exact compare, 403 otherwise. |
| E_A07_003 [string_prefix_x] | **REAL** | same, `x` + group | high | B-01 | same |
| E_A07_003 [string_suffix_x] | **REAL** | same, group + `x` | high | B-01 | same |
| E_A07_003 [string_admin_dash_evil] | **REAL** | same, `rig-admins-evil` | high | B-01 | same |
| E_A07_003 [string_not_admin] | **REAL** | same, `not-rig-admins` gets admin | high | B-01 | same |
| E_A07_003 [string_manager_substring] | **REAL** | same, grants manager to a non-member | medium | B-01 | same |
| E_A07_003 [string_user_substring] | **REAL** | same, grants user to a non-member | medium | B-01 | same |
| E_A07_003 [string_space_separated_groups] | **REAL** | `"rig-users rig-admins"` is accepted as a substring hit and yields admin. Test expects reject; either reject non-list or split on a documented separator, but never substring. | high | B-01 | same |
| E_A07_003 [string_comma_separated_groups] | **REAL** | same | high | B-01 | same |
| E_A07_003 [string_json_encoded_list] | **REAL** | `'["rig-admins"]'` contains the group name -> admin | high | B-01 | same |
| E_A07_003 [dict_with_admin_key] | **REAL** | `in` on dict tests keys -> admin | high | B-01 | same |
| E_A07_003 [dict_with_admin_key_false] | **REAL** | `{"rig-admins": False}` still grants admin (key test) | high | B-01 | same |
| E_A07_003 [huge_string_containing_admin] | **REAL** | 2 MB string containing the name -> role granted (reaches INSERT) | high | B-01 | same |
| E_A07_003 [null] | **REAL** | `TypeError: argument of type 'NoneType' is not a container or iterable` (`auth.py:44`), 500 | low-medium (unauth'd-by-app 500 from a malformed IdP claim) | B-01 | same |
| E_A07_003 [int] | **REAL** | same TypeError for `int` | low-medium | B-01 | same |
| E_A07_003 [bool_true] | **REAL** | same TypeError for `bool` | low-medium | B-01 | same |
| E_A07_003 [float] | **REAL** | same TypeError for `float` | low-medium | B-01 | same |
| E_A07_003 [nested_list] | TEST/RIG BUG | App answered 403 (passed `status < 500` at test line 193); failed at line 195 `users_by_email`: `admin/users -> 403` (superadmin demoted, root cause 2). | - | (B-06 behind it) | rig fix 2. Expected pass. |
| E_A07_003 [nested_object_in_list] | TEST/RIG BUG | same | - | - | same |
| E_A07_003 [dict_groups_wrapper] | TEST/RIG BUG | same | - | - | same |
| E_A07_003 [case_variant] | TEST/RIG BUG | same | - | - | same |
| E_A07_003 [leading_space] | TEST/RIG BUG | same | - | - | same |
| E_A07_003 [trailing_newline] | TEST/RIG BUG | same | - | - | same |
| E_A07_003 [unicode_lookalike] | TEST/RIG BUG | same | - | - | same |
| E_A07_003 [null_byte_suffix] | TEST/RIG BUG | same | - | - | same |
| E_A07_003 [empty_list] | TEST/RIG BUG | same | - | - | same |
| E_A07_003 [empty_string] | TEST/RIG BUG | same | - | - | same |
| E_A07_003 [list_of_non_strings] | TEST/RIG BUG | same | - | - | same |
| E_A07_003 [huge_list_no_match] | TEST/RIG BUG | same | - | - | same |
| E_A07_003 [deeply_nested] | TEST/RIG BUG | same | - | - | same |

E-A07-003 summary: 17 REAL (13 substring/dict-key shapes = B-01, 4 non-iterable types = B-01 500 aspect), 19 TEST/RIG BUG (6 legit sign-ins killed by the username collision, 13 correct 403s killed by the demoted superadmin). It is therefore **both** substring acceptance and 500, not a test bug, for the 17; 500 for the 13 string/dict shapes is only the collision masking the elevated role.

### test_a09_logging

| Test | Verdict | Reason | Sev. | Candidate | Proposed fix |
|---|---|---|---|---|---|
| E_A09_001 no_secrets_in_logs_and_security_events_present | TEST/RIG BUG | `sign-in failed: 500` at test line 66 (username collision, root cause 1); the log assertions never ran. | - | B-11 | rig fix 1. Expected after fix: REAL B-11 (medium): no application-level event for failed superadmin login or rejected sign-in (no `logger` in `auth.py`/`routers/auth.py`), and the uvicorn access log prints the callback URL with the single-use `code=`/`state=` (`routers/auth.py:100`). Also the `null` groups step (line 63-64) produces a 500 traceback (B-01). Make the test tolerant of the order: it already asserts per problem, so keep it as is. |

### test_a10_exceptional

| Test | Verdict | Reason | Sev. | Candidate | Proposed fix |
|---|---|---|---|---|---|
| E_A10_001 postgres_stopped_midrequest | REAL (low) + test fragility | With the DB stopped every request ends in an unhandled `OperationalError` (log: 88 refused, 12 "shutting down", 3 "closed unexpectedly") -> bare 500 "Internal Server Error", no 503 mapping, and the connection is dropped (`httpx.ReadError` at test line 90 on `POST reading`). Same drop pattern as A07-004 after an unhandled error. The worker loop records such errors as `-1`, so the test would also fail at "transport level" even if the app were otherwise fine. | low | none exact (closest B-21/B-22 "DB/IdP errors unmapped") | App: global handler for `sqlalchemy.exc.OperationalError`/`DBAPIError` -> 503 with fixed text and `Connection` kept; short `pool_timeout`. Test: wrap each `down_checks` request in try/except `httpx.HTTPError`, use `limits=httpx.Limits(max_keepalive_connections=0)` for this test client. |
| E_A10_002 idp_unavailable_at_token_exchange[idp_down] | TEST/RIG BUG | `RuntimeError: Cannot open a client instance more than once` (`test_a10_exceptional.py:141`): `_prepared_flow` already used the client (implicit open) and the test then does `async with http`. Never reached the app. | - | B-22 (expected) | `_prepared_flow` should return the client unopened: `http = new_http(rig); async with http: ...` inside the helper (or `try/finally await http.aclose()` in the test and drop `async with http`). Expected after fix: down -> token endpoint 503 -> `routers/auth.py:134` 400 (pass). |
| E_A10_002 idp_unavailable_at_token_exchange[idp_slow] | TEST/RIG BUG | Same client-reuse error. | - | B-22 | Same. Expected after fix: REAL low: with `delay_s=25` the 15 s httpx timeout (`routers/auth.py:124`) raises `ReadTimeout` -> 500 (not caught; only status != 200 is mapped). Fix: `except httpx.HTTPError -> 502/504`. Use `delay_s=17` in the test (just above the 15 s timeout) to save 8 s. |
| E_A10_002 idp_down_at_login_with_cold_discovery_cache | **REAL** | `/api/auth/login` with IdP `down` returns 500 (`_oidc_config()` -> `resp.raise_for_status()` -> `HTTPStatusError`, `routers/auth.py:53-55`, not mapped; cold cache after restart is a legit precondition). Errors are not cached, so every login re-hits the IdP. | low | B-22 | Catch `httpx.HTTPError` in `_oidc_config`, answer 502/503, add a 30-60 s negative cache. |
| E_A10_002 gateway_ocr_failure_modes[gateway-down-llm] | **REAL** | 503 returned but upload remains: `filepath.write_bytes` at `routers/ocr.py:329` runs before `_run_ocr_on_file`; the `except LLMError` (`:336-337`) raises without unlinking. Same finding as group 3 `failed_scan_leaves_no_orphan_file`. | low | B-24 | `try/finally` unlink on error (also the serial crops `:342-352`), or write after success. |
| E_A10_002 gateway_ocr_failure_modes[gateway-unauthorized-llm] | **REAL** | same, 502 | low | B-24 | same |
| E_A10_002 gateway_ocr_failure_modes[ocr-down-ocr] | **REAL** | same, 503 from `routers/ocr.py:172-177` after the file was stored | low | B-24 | same |
| E_A10_002 gateway_ocr_failure_modes[ocr-unauthorized-ocr] | **REAL** | same (401 -> `raise_for_status` -> `httpx.HTTPError` -> 503, orphan left) | low | B-24 | same |
| E_A10_002 gateway_ocr_failure_modes[ocr-garbage-ocr] | **REAL** | HTTP 500: non-JSON body -> `resp.json()` `JSONDecodeError` (ValueError, not `httpx.HTTPError`) in `services/ocr_pipeline.py:416-423` (see triage-group3 root cause 1). The `gateway-garbage-llm` param passes (LLM path validates). | low-medium | B-25 / B-28 | `try/except ValueError` -> 503; validate the response model. |
| E_A10_002 optional_subsystems_unconfigured[llm_and_ocr_unconfigured] | **REAL** (not a rig problem) | The restart with `OCR_URL=""`, `OLLAMA_URL=""` etc. worked: backend healthy, first engine `ocr` answered the expected fixed 503 (`routers/ocr.py:164-169`), then the orphan check fails: file already written at `:329` before the engine check. Engines `llm`/`auto` were not reached (assert aborts in the first loop turn). | low | B-24 | Validate engine availability before `write_bytes` (move the 503 check above `:329`) plus unlink on error. |
| E_A10_002 optional_subsystems_unconfigured[ocr_unconfigured_gateway_configured] | **REAL** (not a rig problem) | same, `engine=ocr` with only `OCR_URL=""` | low | B-24 | same |

Note on the "unconfigured" restarts: `restart_backend(env)` (`conftest.py:271-318`) kills the uvicorn process group, starts a new one with `backend.env` + overrides and restores the original on exit. In the log the restarts succeeded (`Started server process`, `Application startup complete`). The fixture is sound; only `_RestartBackend._start` lacks the Dockerfile's `--proxy-headers --forwarded-allow-ips` flags (not relevant to these tests).

## Counts

| | REAL | TEST/RIG BUG | Total |
|---|---|---|---|
| a06 races | 0 | 8 | 8 |
| a07 | 19 (A07-001, A07-004, 17 x E-A07-003) | 19 (E-A07-003) | 38 |
| a09 | 0 | 1 | 1 |
| a10 | 9 | 2 | 11 |
| **Sum** | **28** | **30** | **58** |

REAL by severity (12 high, 2 medium, 5 low-medium, 9 low = 28):
- high: E_A07_001 forwarded_for (B-02); E_A07_003 xadminx, prefix_x, suffix_x, admin_dash_evil, not_admin, space_separated, comma_separated, json_encoded_list, dict_with_admin_key, dict_with_admin_key_false, huge_string_containing_admin (B-01) = 12 high.
- medium: E_A07_003 string_manager_substring, string_user_substring (B-01) = 2.
- low-medium: E_A07_003 null/int/bool_true/float (B-01, 500) = 4; E_A10_002 ocr-garbage (B-25/B-28).
- low: E_A07_004 (B-16); E_A10_001 (DB outage unmapped, no exact candidate); E_A10_002 cold-cache login (B-22); 4 x orphan after failed scan, 2 x orphan when unconfigured (B-24).

Latent, expected after rig fix: B-04 (medium), B-11 (medium), B-13 (low, currently a false PASS), B-18, and the proposed new B-32 (medium, pool/event-loop stall).

## Why the run took 2h48m (10109 s) instead of ~30 min

The log has no per-test durations, so this is an estimate from the code and the backend log:

1. **A06-001 and A06-003 dominate (roughly 2.5 h of the 2.8 h).** Both run `ROUNDS=100` rounds (`RIG_RACE_ROUNDS` default 100). Because of root causes 1 and 3 every round failed, mostly with `ReadTimeout` after `new_http(..., timeout=90)` (`idp_helpers.py:55`): 100 rounds x up to 90 s. 330k of the 385k backend log lines (lines ~22.7k-354.8k) belong to the A06 span. In a healthy run a round is ~1-3 s (A06-001: 100 sign-ins against a stand-in IdP).
2. **No fail-fast.** `run_rounds` (`idp_helpers.py:151-180`) and the conftest `repeat` (`conftest.py:188-210`) run all N rounds even when the first 10 fail identically.
3. **Rate-limit sleeps:** `send()` in `test_a01_access_control.py:62-70` and the bulk-scan retry (`:344-349`) sleep 10 s per 429 (scan 20/min, bulk-scan 4/min); `test_a05_injection.py:355` sleeps 2 s per 429. Backend restarts: 15 uvicorn starts in the log (~5-8 s each, about 1.5-2 min total).
4. Smaller: IdP `slow` 25 s (never executed due to the client bug), gateway/OCR `slow` 8 s x ~14 scans, 5 MB/25 MB hostile responses, 100k-element group lists, 4 x 90 s read timeouts in A06-002.

### Speedup proposals

- **Make the rounds pass** (rig fix 1 + 2): removes the 90 s timeouts, A06 drops from ~2.5 h to a few minutes.
- **Fail-fast in `run_rounds`:** stop after the first `K` failures where `K = 5` (and abort when the first 10 rounds all fail). Add `asyncio.wait_for(fn(), 30)` per round and use `timeout=20` for the race clients (default 90 only for scans/bulk).
- **Env profiles:** `RIG_RACE_ROUNDS=20`, `RIG_RACE_SIGNINS=30` (<= pool size 30, avoids testing B-32 in every sign-in test), `RIG_RACE_WRITES=20` for the quick/CI profile; full 100x100 only in a nightly profile. Document the statistical trade-off (20 rounds detect a race with >=15 % hit rate at 96 % power).
- **Isolation between flood tests:** `restart_backend()` (or `wait_healthy`) after A06-001/003, so a wedged backend cannot cascade into A06-002/004.
- **Rate limiter off by default in the rig:** add a rig-only launcher module (in `security/rig/`, no app edit) that imports `app.main`, sets `app.limiter.limiter.enabled = False` when `RIG_DISABLE_LIMITER=1`, and run uvicorn against it; only the rate-limit tests (A07-001 x3, scan/bulk limit tests) restart with `RIG_DISABLE_LIMITER` unset. This removes all 10 s 429 waits and the restart-only-for-fresh-window cases (A10 slow, A07-004). Cheaper alternative without a launcher: send a unique `X-Forwarded-For` per request (works only while B-02 is open, so it is a stop-gap).
- **IdP slow test:** `delay_s=17` instead of 25 (just above the app's 15 s client timeout).
- **Observability:** run pytest with `--durations=30 -ra`, and `pytest-timeout` (`--timeout=300`) so a wedged test cannot burn minutes.
- Optional: `pytest-xdist` is not recommended (shared DB state, rate limiter, restarts), but A06-002/004 could run after one shared backend restart.
