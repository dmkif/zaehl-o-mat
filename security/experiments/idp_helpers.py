"""Shared helpers for the A06/A07/A09/A10 experiments (feature 004).

Deliberately defensive about the exact shape of the conftest fixtures (sync or
async callables, dict or attribute access) because conftest.py is written in
parallel; see security/rig/CONTRACT.md for the contract these rely on.
"""
from __future__ import annotations

import asyncio
import base64
import inspect
import os
import time
import uuid
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx

ADMIN_GROUP = os.environ.get("RIG_ADMIN_GROUP", "rig-admins")
MANAGER_GROUP = os.environ.get("RIG_MANAGER_GROUP", "rig-managers")
USER_GROUP = os.environ.get("RIG_USER_GROUP", "rig-users")

# Valid 1x1 PNG (passes magic-byte detection and PIL decoding).
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

INTERNALS = (
    "traceback", "sqlalchemy", "psycopg", "operationalerror", "interfaceerror", "asyncpg",
    "postgresql://", "file \"/", "site-packages", "select ", "insert into", "httpx.", "connecterror",
    "readtimeout", "errno", "keyerror", "typeerror", "valueerror", "app/routers", "app.routers",
)


async def maybe(x):
    return await x if inspect.isawaitable(x) else x


def cfg(rig, key, default=None):
    for k in (key, key.lower()):
        try:
            return rig[k]
        except (KeyError, TypeError, IndexError):
            pass
        if hasattr(rig, k):
            return getattr(rig, k)
    return default


async def get_client(client, role):
    return await maybe(client(role))


def new_http(rig, **kw) -> httpx.AsyncClient:
    """Fresh anonymous client (own cookie jar), no redirect following."""
    return httpx.AsyncClient(
        base_url=cfg(rig, "BACKEND_URL"),
        follow_redirects=False,
        timeout=kw.pop("timeout", 90),
        # no keep-alive: uvicorn drops the connection after an unhandled 500 -> stale-socket ReadError on reuse
        limits=httpx.Limits(max_connections=400, max_keepalive_connections=0),
        **kw,
    )


async def set_mode(ctl, mode: str, **params):
    r = await maybe(ctl.post("/_control/mode", json={"mode": mode, **params}))
    assert r.status_code < 300, f"control mode {mode} rejected: {r.status_code} {r.text[:200]}"


async def set_claims(oidc, groups, sub: str | None = None, email: str | None = None, **extra):
    sub = sub or f"sub-{uuid.uuid4()}"
    email = email or f"{sub}@rig.example.test"
    # the fake IdP defaults preferred_username to a constant ("rig-oidc"); the app's users.username is unique, so
    # a second identity would hit a username collision (B-15, tested separately) and mask the real experiment
    extra.setdefault("preferred_username", f"u-{sub}")
    await set_mode(oidc, "claims", groups=groups, sub=sub, email=email, **extra)
    return sub, email


def unique_identity(prefix="id"):
    u = uuid.uuid4().hex[:16]
    return f"{prefix}-{u}", f"{prefix}-{u}@rig.example.test"


@dataclass
class Flow:
    authorize_url: str
    state: str
    challenge: str | None
    method: str | None
    query: dict
    code: str | None = None
    returned_state: str | None = None


async def start_flow(http: httpx.AsyncClient) -> Flow:
    r = await http.get("/api/auth/login")
    assert r.status_code in (302, 303, 307), f"/auth/login -> {r.status_code}"
    loc = r.headers["location"]
    q = {k: v[0] for k, v in parse_qs(urlparse(loc).query).items()}
    return Flow(loc, q.get("state", ""), q.get("code_challenge"), q.get("code_challenge_method"), q)


async def authorize(http: httpx.AsyncClient, flow: Flow) -> Flow:
    """Walk the fake IdP authorize endpoint (no auto-login UI assumed: it redirects back with code+state)."""
    r = await http.get(flow.authorize_url)
    if r.status_code in (301, 302, 303, 307, 308):
        q = {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}
    else:
        q = r.json()
    flow.code = q.get("code")
    flow.returned_state = q.get("state", flow.state)
    assert flow.code, f"IdP authorize gave no code: {r.status_code} {r.text[:200]}"
    return flow


async def callback(http: httpx.AsyncClient, code: str, state: str, **kw) -> httpx.Response:
    return await http.get("/api/auth/callback", params={"code": code, "state": state}, **kw)


def token_of(resp: httpx.Response) -> str | None:
    if resp.status_code not in (301, 302, 303, 307, 308):
        return None
    q = parse_qs(urlparse(resp.headers.get("location", "")).query)
    return (q.get("token") or [None])[0]


async def signin(http: httpx.AsyncClient):
    """Full flow; returns (callback_response, token_or_None, flow)."""
    flow = await authorize(http, await start_flow(http))
    resp = await callback(http, flow.code, flow.returned_state)
    return resp, token_of(resp), flow


async def me(http: httpx.AsyncClient, token: str) -> httpx.Response:
    return await http.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})


async def users_by_email(admin_client, email: str) -> list[dict]:
    r = await admin_client.get("/api/admin/users")
    assert r.status_code == 200, f"admin/users -> {r.status_code}"
    return [u for u in r.json() if u["email"] == email]


async def barrier(barrier_gather, n, factory):
    """barrier_gather wrapper: returns results, exceptions kept as values."""
    res = await maybe(barrier_gather(n, factory))
    return list(res)


def status_of(res) -> int:
    return res.status_code if isinstance(res, httpx.Response) else -1


def leaks_internals(text: str) -> list[str]:
    low = text.lower()
    return [m for m in INTERNALS if m in low]


RACE_MAX_FAILURES = int(os.environ.get("RIG_RACE_MAX_FAILURES", "5"))
RACE_ROUND_TIMEOUT_S = float(os.environ.get("RIG_RACE_ROUND_TIMEOUT", "30"))


async def run_rounds(repeat, n: int, fn, label: str = ""):
    """Run `fn` (async, raises on violation) up to n times; returns the list of failure messages.
    Fail-fast: aborts after RACE_MAX_FAILURES (5) failed rounds (which includes "the first 10 rounds all
    failed"); every round is bounded by RACE_ROUND_TIMEOUT_S (30 s). `repeat` is kept for signature
    compatibility only. Any failure still fails the test (strict)."""
    failures: list[str] = []
    done = 0
    for _ in range(n):
        done += 1
        try:
            await asyncio.wait_for(fn(), RACE_ROUND_TIMEOUT_S)
        except asyncio.TimeoutError:
            failures.append(f"TimeoutError: round exceeded {RACE_ROUND_TIMEOUT_S:.0f}s")
        except Exception as exc:  # noqa: BLE001 - every deviation counts as a failed round
            failures.append(f"{type(exc).__name__}: {str(exc)[:300]}")
        if len(failures) >= RACE_MAX_FAILURES:
            break
    note = f" (aborted after {done}/{n} rounds: fail-fast)" if done < n else ""
    print(f"[{label}] failure rate {len(failures)}/{done}{note}")
    return failures


async def enter(cm):
    """Enter a sync or async context manager; returns (value, exit_callable)."""
    if hasattr(cm, "__aenter__"):
        v = await cm.__aenter__()
        return v, lambda *a: cm.__aexit__(*(a or (None, None, None)))
    v = cm.__enter__()

    async def _exit(*a):
        cm.__exit__(*(a or (None, None, None)))

    return v, _exit


class Restarted:
    """`async with Restarted(restart_backend, env)` for sync/async restart_backend."""

    def __init__(self, restart_backend, env=None):
        self._cm = restart_backend(env or {})
        self._exit = None

    async def __aenter__(self):
        v, self._exit = await enter(self._cm)
        return v

    async def __aexit__(self, *a):
        await maybe(self._exit(*a))


async def wait_healthy(http: httpx.AsyncClient, timeout_s=60.0) -> bool:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        try:
            r = await http.get("/api/health")
            if r.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        await __import__("asyncio").sleep(1)
    return False


async def make_meter(admin_client, name="race"):
    await wait_healthy(admin_client, 30)  # a previous flood must not cascade into this setup
    p = await admin_client.post("/api/properties/", json={"name": f"{name}-{uuid.uuid4().hex[:8]}"})
    assert p.status_code == 201, f"create property -> {p.status_code} {p.text[:200]}"
    pid = p.json()["id"]
    m = await admin_client.post(
        f"/api/properties/{pid}/meters/",
        json={"meter_type": "water", "unit": "m³", "name": f"{name}-m"},
    )
    assert m.status_code == 201, f"create meter -> {m.status_code} {m.text[:200]}"
    return pid, m.json()["id"]
