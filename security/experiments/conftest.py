"""Fixtures for the security experiments (contract: security/rig/CONTRACT.md).

Requires a running rig (security/rig/up.sh, normally via scripts/security-rig.sh).
Without rig.env every test using a rig fixture is skipped with a clear reason.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from authlib.jose import JsonWebToken

# make helper modules (idp_helpers, sec_common) importable regardless of rootdir/rootdir mode
sys.path.insert(0, str(Path(__file__).resolve().parent))

SEC_DIR = Path(__file__).resolve().parents[1]
STATE = SEC_DIR / "rig" / ".state"
ROLES = ("superadmin", "admin", "manager", "user_a", "user_b", "nogroup")
TOKEN_LIFETIME = timedelta(hours=4)  # longer than any run; the app default (60 min) would expire mid-suite

# registered by pytest.ini; repeated here so the file also works with another config
def pytest_configure(config):
    config.addinivalue_line("markers", "security: attack/fault-injection experiment against the isolated rig")
    config.addinivalue_line("markers", "race: concurrency experiment")


def pytest_collection_modifyitems(config, items):
    for item in items:  # every experiment in this directory is a security experiment
        item.add_marker(pytest.mark.security)


class Rig(dict):
    """Parsed rig.env; supports rig["KEY"] and rig.KEY / rig.key."""

    def __getattr__(self, name):
        for k in (name, name.upper()):
            if k in self:
                return self[k]
        raise AttributeError(name)


def _parse_env(path: Path) -> dict:
    out = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


# ---------------------------------------------------------------------------------- basic fixtures
@pytest.fixture(scope="session")
def rig() -> Rig:
    f = STATE / "rig.env"
    if not f.exists():
        pytest.skip("rig is not up (run scripts/security-rig.sh or security/rig/up.sh)")
    r = Rig(_parse_env(f))
    for key in ("BACKEND_URL", "OIDC_URL", "GATEWAY_URL", "OCR_URL"):
        assert r[key].startswith("http://127.0.0.1:"), f"refusing non-loopback {key}={r[key]}"
    return r


@pytest.fixture(scope="session")
def seed(rig) -> dict:
    f = STATE / "seed.json"
    if not f.exists():
        pytest.skip("seed.json missing (rig/seed.py did not run)")
    return json.loads(f.read_text())


def db_token_version(rig, sub: str) -> int:
    """users.token_version of ``sub`` straight from the rig DB (feature 007: get_current_user rejects a
    token whose ``tv`` differs from this value)."""
    from sqlalchemy import create_engine, text

    eng = create_engine(rig["DATABASE_URL"])
    try:
        with eng.connect() as c:
            row = c.execute(text("SELECT token_version FROM users WHERE id = :i"), {"i": sub}).first()
    finally:
        eng.dispose()
    assert row is not None, f"no users row for {sub}"
    return int(row[0])


def mint_token(rig, sub: str, role: str | None = None, lifetime: timedelta = TOKEN_LIFETIME, **extra) -> str:
    """Same as backend/app/auth.py issue_user_token: HS256, claims sub/role/tv/iat/exp (int); ``tv`` is read
    from the rig DB unless the caller passes one (pass tv=<n> to forge a stale/foreign counter)."""
    payload = {"sub": sub, **({"role": role} if role else {}), **extra}
    payload.setdefault("tv", db_token_version(rig, sub))
    payload.setdefault("iat", int(datetime.now(timezone.utc).timestamp()))
    payload["exp"] = int((datetime.now(timezone.utc) + lifetime).timestamp())
    return JsonWebToken(["HS256"]).encode({"alg": "HS256"}, payload, rig["JWT_SECRET_KEY"]).decode()


def _http(rig, **kw) -> httpx.AsyncClient:
    kw.setdefault("timeout", 90)
    kw.setdefault("follow_redirects", False)
    # no pooled keep-alive (uvicorn drops connections after unhandled 500s -> stale-socket ReadError) + connect retries
    kw.setdefault("transport", httpx.AsyncHTTPTransport(retries=2, limits=httpx.Limits(max_keepalive_connections=0)))
    return httpx.AsyncClient(base_url=rig["BACKEND_URL"], **kw)


@pytest.fixture
async def client(rig, seed):
    """client(role) -> httpx.AsyncClient with a valid bearer token for that seed role
    (superadmin, admin, manager, user_a, user_b, nogroup; "user" is an alias of user_a)."""
    made: list[httpx.AsyncClient] = []

    def factory(role: str) -> httpx.AsyncClient:
        role = "user_a" if role == "user" else role
        if role not in seed["users"]:
            raise KeyError(f"unknown role {role!r}; known: {sorted(seed['users'])}")
        u = seed["users"][role]
        c = _http(rig, headers={"Authorization": f"Bearer {mint_token(rig, u['id'], u['role'])}"})
        made.append(c)
        return c

    yield factory
    for c in made:
        with contextlib.suppress(Exception):
            await c.aclose()


@pytest.fixture
async def anon(rig):
    async with _http(rig) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_seed_roles(request):
    """After every test: seed users get their seed role back (SQL), so one test that demotes the
    superadmin cannot turn later tests into false failures."""
    yield
    if not (STATE / "rig.env").exists() or not (STATE / "seed.json").exists():
        return
    try:
        from sec_common import restore_seed_roles

        fixed = restore_seed_roles(Rig(_parse_env(STATE / "rig.env")), json.loads((STATE / "seed.json").read_text()))
        if fixed:
            print(f"[rig] restored {fixed} drifted seed user role(s) after {request.node.name}")
    except Exception as exc:  # noqa: BLE001 - never fail a test because of the reset
        print(f"[rig] seed role reset failed: {exc!r}")


# ---------------------------------------------------------------------------------- stand-in control
class Control(httpx.AsyncClient):
    async def set_mode(self, mode: str, **params) -> httpx.Response:
        r = await self.post("/_control/mode", json={"mode": mode, **params})
        assert r.status_code == 200, f"stand-in refused mode {mode}: {r.status_code} {r.text[:200]}"
        return r

    async def get_mode(self) -> dict:
        return (await self.get("/_control/mode")).json()

    async def requests(self) -> list:
        return (await self.get("/_control/requests")).json()

    async def reset(self) -> None:
        await self.post("/_control/reset")


async def _control(base: str):
    c = Control(base_url=base, timeout=30)
    try:
        await c.reset()
        yield c
    finally:
        with contextlib.suppress(Exception):
            await c.reset()
        await c.aclose()


@pytest.fixture
async def gateway(rig):
    async for c in _control(rig["GATEWAY_URL"].removesuffix("/v1")):
        yield c


@pytest.fixture
async def oidc(rig):
    async for c in _control(rig["OIDC_URL"]):
        yield c


@pytest.fixture
async def ocr(rig):
    async for c in _control(rig["OCR_URL"]):
        yield c


# ---------------------------------------------------------------------------------- concurrency helpers
@pytest.fixture
def barrier_gather():
    """barrier_gather(n, factory): factory(i) (or factory()) returns a coroutine; all n are
    created first, then released at the same instant. Returns results; exceptions stay values."""
    async def run(n: int, factory):
        gate = asyncio.Event()
        takes_arg = len(inspect.signature(factory).parameters) > 0

        async def one(i):
            await gate.wait()
            coro = factory(i) if takes_arg else factory()
            return await coro

        tasks = [asyncio.ensure_future(one(i)) for i in range(n)]
        await asyncio.sleep(0)  # let every task park on the gate
        gate.set()
        return await asyncio.gather(*tasks, return_exceptions=True)

    return run


@pytest.fixture
def repeat():
    """repeat(n, fn) -> (failures, n). fn(i) may be sync or async; a round fails if it raises or
    returns False. The failure rate is printed (visible with -s / in the report)."""
    async def run(n: int, fn):
        failures = 0
        takes_arg = len(inspect.signature(fn).parameters) > 0
        for i in range(n):
            try:
                res = fn(i) if takes_arg else fn()
                if inspect.isawaitable(res):
                    res = await res
                if res is False:
                    failures += 1
            except Exception:  # noqa: BLE001 - any exception is a failed round
                failures += 1
        print(f"[repeat] failures {failures}/{n} ({100.0 * failures / max(n, 1):.1f} %)")
        return failures, n

    return run


# ---------------------------------------------------------------------------------- fault injection
def _podman(*args, check=True):
    return subprocess.run(["podman", *args], check=check, capture_output=True, text=True, timeout=120)


def _pg_ready(rig, timeout=60) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        r = _podman("exec", rig["PG_CONTAINER"], "pg_isready", "-h", "127.0.0.1", "-U", "rig", "-d", "rig", check=False)
        if r.returncode == 0:
            return
        time.sleep(0.5)
    raise RuntimeError("postgres did not come back")


@pytest.fixture
def pg_stop(rig):
    """Blocking helper: stop the Postgres container (SIGTERM, fast)."""
    return lambda: _podman("stop", "-t", "2", rig["PG_CONTAINER"])


@pytest.fixture
def pg_start(rig):
    """Blocking helper: start the container again and wait until it accepts connections."""
    def start():
        _podman("start", rig["PG_CONTAINER"])
        _pg_ready(rig)
    return start


def _backend_proc_env(overrides: dict | None) -> dict:
    env = {"PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin", "HOME": str(STATE), "LANG": "C.UTF-8",
           "PYTHONUNBUFFERED": "1",
           "PYTHONPATH": str(SEC_DIR / "rig")}
    env.update(_parse_env(STATE / "backend.env"))
    for k, v in (overrides or {}).items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = str(v)
    return env


class _RestartBackend:
    """Replace the uvicorn process (fresh rate limiter, cold OIDC discovery cache, new env).
    Usable as `with` and `async with`; leaving restores the original configuration.
    The log file is appended to, so backend_log() keeps one continuous history."""

    def __init__(self, rig, overrides):
        self.rig, self.overrides = rig, dict(overrides or {})

    def _stop(self):
        pidf = STATE / "pids" / "backend"
        pid = int(pidf.read_text())
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGTERM)
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGKILL)
        time.sleep(0.3)

    def _start(self, overrides):
        rig = self.rig
        port = rig["BACKEND_URL"].rsplit(":", 1)[1]
        log = open(rig["BACKEND_LOG"], "ab")
        with open(os.devnull, "rb") as devnull:
            p = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "rig_app:app", "--app-dir", rig["BACKEND_DIR"],
                 "--host", "127.0.0.1", "--port", port, "--log-level", "debug", "--no-proxy-headers"],
                env=_backend_proc_env(overrides), cwd=STATE, stdin=devnull, stdout=log, stderr=log,
                start_new_session=True)
        log.close()
        (STATE / "pids" / "backend").write_text(str(p.pid))
        end = time.monotonic() + 60
        while time.monotonic() < end:
            if p.poll() is not None:
                raise RuntimeError(f"backend exited during restart (rc={p.returncode}); see {rig['BACKEND_LOG']}")
            try:
                if httpx.get(rig["BACKEND_URL"] + "/api/health/live", timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise RuntimeError("backend not healthy after restart")

    def __enter__(self):
        self._stop()
        self._start(self.overrides)
        return self

    def __exit__(self, *exc):
        self._stop()
        self._start(None)
        return False

    async def __aenter__(self):
        return await asyncio.to_thread(self.__enter__)

    async def __aexit__(self, *exc):
        await asyncio.to_thread(self.__exit__, *exc)
        return False


@pytest.fixture
def restart_backend(rig):
    """restart_backend(env_overrides=None) -> (async) context manager; value None unsets a variable."""
    return lambda env_overrides=None: _RestartBackend(rig, env_overrides)


@pytest.fixture
def backend_log(rig):
    """backend_log() -> full text of the uvicorn log so far (slice with len() before/after)."""
    return lambda: Path(rig["BACKEND_LOG"]).read_text(errors="replace")
