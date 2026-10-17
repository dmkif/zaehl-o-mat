# Rerun 1 (profile `--quick`, rig fixes + speedups)

Command: `scripts/security-rig.sh --quick` (RIG_RACE_ROUNDS=20, RIG_RACE_SIGNINS=30, limiter off rig-wide). No `backend/` file changed.

| | Before (full-run-1) | After (this rerun) |
|---|---|---|
| Runtime | about 2.8 h | **7 min 6 s** pytest (7 min 22 s wall, incl. rig up/down) |
| Tests | 246 | 246 (1 skipped: A02 needs `SEC_RIG_APP_DEBUG=false`) |
| Failed | 119 (46 + 58 + 15) | **94** (93-95: E-A10-001 is intermittent, see below) |
| Passed | | 151 |
| TEST/RIG-BUG failures | 33 (3 in A05, 30 in group 2) | **0** |

## Per file (pass / fail)

| File | pass | fail |
|---|---|---|
| test_a01_access_control | 30 | 10 |
| test_a02_misconfiguration | 15 (+1 skip) | 24 |
| test_a04_crypto_tokens | 46 | 7 |
| test_a05_injection | 6 | 4 |
| test_a06_races | 5 | 5 |
| test_a07_authentication | 22 | 19 |
| test_a08_integrity | 13 | 10 |
| test_a09_logging | 0 | 1 |
| test_a10_exceptional | 4 | 9 (10 when E-A10-001 hits its race) |
| test_uploads | 10 | 5 |

Slowest (from `--durations=30`): A05-001 json fields 64 s, A06-001 63 s, A05-001 query params 50 s, A08 ocr_slow 30 s, A10 IdP/gateway slow tests 14-16 s each, A07-001 x2 about 14 s (backend restarts with real limiter). Everything else is below 10 s.

## What was fixed (rig/test side)

1. `set_claims`: unique `preferred_username` (`u-<sub>`) per identity (removes the `users_username_key` 500s, B-15 masking).
2. Seed roles: autouse fixture `_reset_seed_roles` (SQL, after every test) plus SQL restore in the `finally` of E-A01-003 (log shows 2 restores; no more `admin/users -> 403` cascade).
3. A05: `_http` / A01 actors use `AsyncHTTPTransport(retries=2)` with `max_keepalive_connections=0`; `new_http` has no keep-alive; transport errors retried once in the three A05 request loops; header check now only flags a separate `x-injected` line or a `set-cookie:` line containing `pwned` (the echoed X-Request-ID value is no longer a false positive); A08 `digits()` uses `str()`.
4. A10: `_prepared_flow` client closed with `aclose()` (no double open); `idp_slow` uses `delay_s=17`; E-A10-001 tolerates transport errors as fail-closed signal (each probe wrapped) but still asserts bounded latency, no internals in answered 5xx, no partial write for requests answered 5xx, recovery after restart. Requests that got no answer are not counted as "failed" for the partial-write check (outcome unknowable).
5. Races: `run_rounds` fail-fast (5 failures, covers "first 10 all fail"), per-round `wait_for` 30 s, race clients 20 s, `make_meter` waits for health, sign-in flood tests (A06-001, A06-003 role change) restart the backend afterwards, `--quick` flag.
6. Limiter: `security/rig/rig_app.py` (uvicorn target `rig_app:app`, PYTHONPATH=`security/rig`) disables slowapi when `RIG_DISABLE_LIMITER=1` (rig default, in `.state/backend.env`); only E-A07-001 (x2) restart with `{"RIG_DISABLE_LIMITER": None}`. A07-004 and the A10 slow test no longer restart. Documented in `rig/CONTRACT.md`. `rig_app.py` also registers SIGUSR1 -> thread dump to the backend log (hang analysis).
7. pytest: `addopts = --durations=30 -ra --timeout=300`, `pytest-timeout` in requirements and in the venv check of `up.sh`.

## Remaining failures: all REAL

| IDs | Verdict |
|---|---|
| A01_002 x6 (cross-tenant OCR hint, filename reference, unattached scan readable, hint-rescan foreign image) | REAL (B-07 and related) |
| A01_003 manager escalation, A01_003 admin demotes superadmin | REAL (B-05, B-06) |
| A01_005 meter patch foreign property, A01_006 integration_config 500 | REAL (B-19 / meters) |
| A02 x24 (weak JWT secret x11, weak superadmin password x7, no-password pair, docs public x2, health detail, error bodies internals, X-Request-ID reflection) | REAL |
| A04 x7 (headers, no exp, exp inf, sub 123 / True / list / dict) | REAL |
| A05_001 json fields (NUL 500, lone surrogate 500 in 422 serialization), query/path (callback `state` NUL, offset overflow), multipart (hint-rescan NUL path) | REAL (B-19) |
| A05_001 enum members | REAL, **new** (see below) |
| A06_002 same_hash x2 (3-6 x 201 for one hash), A06_002 identical reading (20 identical rows) | REAL (B-18; identical-rows part is "no uniqueness rule", design decision) |
| A06_003 privilege after IdP removal | REAL (B-04) |
| A06_004 state not bound to browser | REAL (login CSRF / fixation) |
| A07_001 X-Forwarded-For bypass (40 guesses evaluated) | REAL (B-02) |
| A07_004 non-ASCII / malformed credentials (500 for 2 bodies) | REAL (B-16) |
| A07_003 x17 (13 substring/dict shapes accepted, 4 non-iterable types give 500) | REAL (B-01) |
| A08 x10 (hostile huge/nested/types/garbage responses, reading==serial on OCR path, orphan file on failed scan x2, bulk stream) | REAL (B-24, B-28) |
| A09_001 (code/state in access log, no security events) | REAL (B-11) |
| A10_002 idp_slow 500, cold-cache login 500 | REAL (B-22) |
| A10_002 orphan file after failed scan: gateway down/unauthorized, ocr down/unauthorized, 2 x unconfigured | REAL (B-24) |
| A10_002 ocr garbage -> 500 | REAL |
| uploads x5 (truncated images stored, EXIF GPS kept, EXIF date abuse, decompression bomb, foreign upload adoption) | REAL |
| A10_001 postgres stopped | REAL (low), **intermittent** (failed in 1 of 2 runs): `db.refresh()` after `commit()` (`routers/readings.py:100`) fails while the DB goes down, the request answers 500 but the reading is stored (acknowledgement lost, a client retry duplicates it). Passes when no request lands in that window. Also: uvicorn closes the connection after unhandled 500s (stale-socket `ReadError` for keep-alive clients). |

## New candidates

* **B-32 confirmed and stronger than predicted (A06-002 pool claim, medium).** `callback` is `async def` with synchronous SQLAlchemy calls (`routers/auth.py:149` -> `auth.py:56`), pool 10 + 20. Measured on the rig, one identity, N parallel callbacks (script was an ad-hoc client, not a test): N=30 all 307 in 3.3 s; N=32 31 of 32 requests time out (60 s client limit), N=40 36/40, N=60 57/60, N=100 97/100 (120 s limit). During the stall `/api/health/live` also hangs (>120 s): the whole process is blocked. `pg_stat_activity` then shows exactly 30 connections `idle in transaction` (all on `SELECT users...`), all 31 backend threads in futex wait, and a SIGUSR1 thread dump shows the event-loop thread blocked in `QueuePool._do_get -> queue.get` (sqlalchemy `pool/impl.py:156`): synchronous checkout on the loop while the 30 holders need the loop to finish and release. It did not recover within 6 minutes and neither a QueuePool timeout line nor any 500 was logged in that time (the earlier 311 QueuePool errors came from a 2.8 h run). Only a restart recovers. Threshold = pool size + overflow (30). Reachable by any account that can sign in at the IdP (one valid code per request). Fix: plain `def` handlers or `run_in_threadpool` for the DB part, small `pool_timeout`, concurrency limit on `/callback`. This is why the quick profile uses 30 sign-ins and the full profile (100) will fail A06-001 after 5 rounds (about 2.5 min) and needs the post-flood restart.
* **Enum drift between model and migration (new, medium for fresh installs).** `POST /meters` with valid schema members `kWh`, `L` (as `unit`) and `smart_meter` (as `integration_type`) gives 500 on a DB built by `alembic upgrade head`: `invalid input value for enum meterunit: "kwh"` / `integrationtype: "smart_meter"`. `models.py` persists enum *names* (`Enum(MeterUnit)`: m3, kwh, liter; manual, smart_meter, shelly, homeassistant) but `alembic/versions/0000_initial_schema.py:67,72` creates labels `kWh, m3, L, MWh, GJ, other` and `manual, homeassistant, modbus, api`. Check whether production was created by `create_all` (then only the migration is wrong) before rating. Seen by A05-001 `enum_members_do_not_crash`.
* **FastAPI 422 with a lone surrogate (low).** Body value `"\ud800"` raises `RequestValidationError` serialization failure -> 500 (A05-001 json fields, `property_type`, `meter_type`). Same class as B-19 (no global exception handler).
* **A06-002 duplicates are a design question:** 20 identical readings from 20 parallel posts; the app has no uniqueness rule for readings without image. Keep as finding only if duplicate protection is intended.

## Notes for the next run

* Full profile: `scripts/security-rig.sh` (100 x 100) will take longer because of A06-001 (expected REAL until B-32 is fixed) and A06-003 (restart afterwards); it fails fast.
* Unfixed app weaknesses make 94 tests fail by design; once fixed, expect the A05/A10 rows to turn green, E-A10-001 stays timing dependent.
