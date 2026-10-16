# Rig contract (shared by up.sh, stand-ins, seed.py, conftest.py, experiments)

State file written by `rig/up.sh`: `security/rig/.state/rig.env` (shell `KEY=value`, no quotes), removed by `down.sh`.

| Key | Meaning |
|---|---|
| BACKEND_URL | `http://127.0.0.1:<port>` uvicorn running `backend/app` |
| DATABASE_URL | `postgresql://rig:<random>@127.0.0.1:<port>/rig` (podman container `zom-sec-pg`) |
| PG_CONTAINER | `zom-sec-pg` (experiments stop/start it for fault injection) |
| JWT_SECRET_KEY | generated per run |
| SUPERADMIN_USER / SUPERADMIN_PASSWORD | generated per run |
| UPLOAD_DIR | `security/rig/.state/uploads` |
| OIDC_URL | fake IdP base `http://127.0.0.1:<port>` |
| GATEWAY_URL | fake gateway base, OpenAI-compatible, `.../v1` |
| OCR_URL | fake OCR service base |
| BACKEND_PID / STANDIN_PIDS | for teardown |
| BACKEND_LOG | log file of uvicorn (A09 greps it; run with `LOG_LEVEL=debug`) |

Backend env set by up.sh: DATABASE_URL, JWT_SECRET_KEY, OIDC_* (discovery `$OIDC_URL/.well-known/openid-configuration`, client id `rig-client`, secret generated, redirect `$BACKEND_URL/api/auth/callback`, groups `rig-admins`/`rig-managers`/`rig-users`), SUPERADMIN_*, UPLOAD_PATH, OCR_BACKEND=gateway + GATEWAY_BASE_URL=$GATEWAY_URL + GATEWAY_API_KEY=generated, OCR_URL=$OCR_URL.

## Stand-in control API (all three stand-ins)

- `POST /_control/mode` JSON `{"mode": "<name>", ...params}` sets behaviour; `GET /_control/mode`; `POST /_control/reset` back to `ok`.
- `GET /_control/requests` returns the recorded inbound requests (method, path, headers redacted for keys, body size) for assertions.
- Modes - gateway/ocr: `ok`, `hostile_huge`, `hostile_nested`, `hostile_types`, `reading_equals_serial`, `slow` (param `delay_s`), `down` (HTTP 503), `unauthorized` (401), `garbage` (non-JSON).
- Modes - oidc: `ok`, `claims` (param `groups` = any JSON value: list, string, nested, huge, null; also `email`, `sub`), `expired_token`, `bad_signature`, `alg_none`, `wrong_nonce`, `no_pkce_check`, `slow`, `down`.
- Stand-ins are plain FastAPI/uvicorn apps started by `up.sh` as `python security/rig/standins/<name>.py --port N`.

## Synthetic data (seed.py)

`python security/rig/seed.py` (reads rig.env, writes via the backend DB with SQLAlchemy models) creates users with fixed usernames `rig-superadmin`, `rig-admin`, `rig-manager`, `rig-user-a`, `rig-user-b`, `rig-nogroup`; properties `prop-a` (user A access) and `prop-b` (user B access); meters and readings on each; small generated PNG/JPEG files in UPLOAD_DIR. Prints a JSON map of ids to stdout and writes it to `security/rig/.state/seed.json`.

## Experiment fixtures (conftest.py)

`rig` (parsed rig.env), `seed` (seed.json), `client(role)` (httpx.AsyncClient with a valid bearer token for that role, signed with JWT_SECRET_KEY the same way the app does), `anon`, `gateway`/`oidc`/`ocr` (control clients with auto-reset), `barrier_gather(n, coro_factory)` (all coroutines released at the same instant), `repeat(n, fn)` -> `(failures, n)` and prints failure rate, `pg_stop()/pg_start()` helpers, `restart_backend(env_overrides)` context manager, `backend_log()` text.

## Rate limiter switch and run profiles (rerun tooling)

* The backend is started as `uvicorn rig_app:app` (`rig/rig_app.py`, PYTHONPATH=`rig/`), a thin wrapper that imports the
  unmodified `app.main` and, when `RIG_DISABLE_LIMITER=1` (default for the whole rig, written to `.state/backend.env`),
  sets `app.limiter.limiter.enabled = False`. No file under `backend/` is touched.
* Rate-limit experiments (E-A07-001) call `restart_backend({"RIG_DISABLE_LIMITER": None})` to run against the real limiter;
  leaving the context restores the rig default (limiter off).
* `scripts/security-rig.sh --quick` sets `RIG_RACE_ROUNDS=20`, `RIG_RACE_SIGNINS=30` (defaults for a full run: 100/100).
  Race rounds fail fast (`RIG_RACE_MAX_FAILURES`, default 5) and are bounded by `RIG_RACE_ROUND_TIMEOUT` (30 s).
* pytest runs with `--durations=30 -ra --timeout=300` (`pytest.ini`).
* An autouse fixture resets the seed users' role/is_active via SQL after every test.
