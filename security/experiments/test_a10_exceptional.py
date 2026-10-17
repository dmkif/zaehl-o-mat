"""A10 Mishandling of Exceptional Conditions (E-A10-001, E-A10-002).

Fault injection: PostgreSQL container stop/start, fake IdP/gateway/OCR in `down`, `slow`,
`unauthorized`, `garbage` modes, backend restarted with optional subsystems unconfigured.

Assumed fixtures (CONTRACT.md): rig, client(role), oidc, gateway, ocr (control clients,
auto-reset), pg_stop(), pg_start(), restart_backend(env_overrides).
pg_stop/pg_start/restart_backend may be sync or async; blocking ones run in a thread.
Strict assertions: a failing test is a weakness.
"""
import asyncio
import contextlib
import inspect
import os
import time
from pathlib import Path

import httpx
import pytest

from idp_helpers import (
    TINY_PNG, USER_GROUP, Restarted, authorize, callback, cfg, get_client, leaks_internals, make_meter,
    new_http, set_claims, set_mode, start_flow, token_of, unique_identity, wait_healthy,
)

pytestmark = pytest.mark.security

BOUND_S = 45.0  # upper bound for any single failing request


async def _call(fn):
    if inspect.iscoroutinefunction(fn):
        return await fn()
    res = await asyncio.to_thread(fn)
    return await res if inspect.isawaitable(res) else res


def _no_internals(resp, rig, what):
    body = resp.text
    found = leaks_internals(body)
    db_url = cfg(rig, "DATABASE_URL", "") or ""
    pw = db_url.split(":")[2].split("@")[0] if db_url.count(":") >= 2 and "@" in db_url else ""
    if pw and pw in body:
        found.append("database password")
    assert not found, f"{what}: response leaks internals {found}: {body[:200]!r}"


def _files(rig) -> set[str]:
    return {p.name for p in Path(cfg(rig, "UPLOAD_DIR")).glob("*")}


# --------------------------------------------------------------------------- E-A10-001

async def test_E_A10_001_postgres_stopped_midrequest(rig, client, pg_stop, pg_start):
    """E-A10-001: stop PostgreSQL while readings are being written. Secure: every failing
    request ends as a clean, bounded 5xx (preferably 503) without exception names, SQL, driver
    or credentials in the body; a reading exists after the restart iff its request got 201
    (no partial write); health reports degraded (503) and liveness stays 200; the service
    recovers by itself after PostgreSQL is back."""
    admin = await get_client(client, "admin")
    pid, mid = await make_meter(admin, "pgstop")
    base = f"/api/properties/{pid}/meters/{mid}/readings/"
    outcomes: list[tuple[int, int, float, str]] = []  # (value, status, latency, body)
    stop_done = asyncio.Event()
    stopped_at = {}

    async def worker(w):
        i = 0
        while not stop_done.is_set() or time.monotonic() < stopped_at.get("t", 1e18) + 2:
            value = 100_000 + w * 10_000 + i
            i += 1
            t0 = time.monotonic()
            try:
                r = await asyncio.wait_for(admin.post(base, json={"value": str(value)}), 90)
                outcomes.append((value, r.status_code, time.monotonic() - t0, r.text))
            except Exception as exc:  # noqa: BLE001
                outcomes.append((value, -1, time.monotonic() - t0, repr(exc)))
            await asyncio.sleep(0.02)

    async def stopper():
        await asyncio.sleep(0.5)
        await _call(pg_stop)
        stopped_at["t"] = time.monotonic()
        stop_done.set()

    try:
        await asyncio.gather(stopper(), *[worker(w) for w in range(6)])

        # While the DB is down: representative endpoints. A transport-level error (connection dropped by the
        # server after an unhandled error) is a fail-closed signal, not a reason to abort: it is recorded and
        # judged below, the checks on the remaining endpoints still run.
        async def probe(c, method, url, **kw):
            try:
                return await c.request(method, url, **kw)
            except httpx.TransportError as exc:
                return exc

        down_checks = {
            "GET properties": await probe(admin, "GET", "/api/properties/"),
            "POST reading": await probe(admin, "POST", base, json={"value": "1"}),
        }
        async with new_http(rig) as anon:
            down_checks["superadmin-login"] = await probe(
                anon, "POST", "/api/auth/superadmin-login", json={"username": cfg(rig, "SUPERADMIN_USER"), "password": "x"}
            )
            down_checks["oidc callback"] = await probe(anon, "GET", "/api/auth/callback", params={"code": "c", "state": "s"})
            health = await probe(anon, "GET", "/api/health")
            live = await probe(anon, "GET", "/api/health/live")
        for what, r in down_checks.items():
            if isinstance(r, Exception):
                print(f"[A10-001] {what}: transport error {type(r).__name__} while DB down (fail-closed signal)")
                continue
            assert r.status_code >= 500 or r.status_code in (400, 401, 429), f"{what} while DB down -> HTTP {r.status_code}"
            if r.status_code >= 500:
                assert r.status_code in (500, 502, 503, 504), f"{what}: odd 5xx {r.status_code}"
                _no_internals(r, rig, what)
        assert not isinstance(health, Exception), f"health endpoint unreachable while DB down: {health!r}"
        assert health.status_code == 503 and health.json().get("db") is False, "health does not report the DB outage"
        assert not isinstance(live, Exception) and live.status_code == 200, "liveness must not depend on the DB"
        _no_internals(health, rig, "health")
    finally:
        await _call(pg_start)

    assert await wait_healthy(admin, 90), "service did not recover after PostgreSQL came back (needs restart?)"

    errs = [o for o in outcomes if o[1] >= 500 or o[1] == -1]
    assert outcomes and errs, "fault was not observed by any request (stop too slow/fast?); rerun"
    n_transport = sum(1 for o in errs if o[1] == -1)
    if n_transport:
        print(f"[A10-001] {n_transport}/{len(errs)} failing requests ended as transport errors (connection dropped): fail-closed")
    for value, status, latency, body in errs:
        # a dropped connection is accepted as fail-closed, but still bounded and still judged for partial writes below
        assert latency < BOUND_S, f"failing request took {latency:.1f}s"
        if status != -1:  # for a transport error `body` is only our own repr(exception), not a server response
            assert leaks_internals(body) == [], f"5xx body leaks internals: {body[:200]!r}"
    stored = {round(float(r["value"])) for r in (await admin.get(base + "?limit=10000")).json()}
    created = {v for v, s, *_ in outcomes if s == 201}
    # outcome of a request that got no HTTP answer is unknowable (the commit may have preceded the drop): only
    # requests answered with 5xx must not have written anything
    failed = {v for v, s, *_ in outcomes if s >= 500}
    assert created <= stored, f"{len(created - stored)} readings acknowledged with 201 but missing after restart"
    assert not (failed & stored), f"{len(failed & stored)} readings stored although the request failed (partial write)"


# --------------------------------------------------------------------------- E-A10-002 IdP

async def _prepared_flow(rig, oidc):
    sub, email = unique_identity("a10")
    await set_claims(oidc, [USER_GROUP], sub, email)
    # the client is returned unopened-by-context: callers close it with aclose() (an `async with` after use
    # raises "Cannot open a client instance more than once")
    http = new_http(rig)
    flow = await authorize(http, await start_flow(http))
    return http, flow


@pytest.mark.parametrize("mode,params", [("down", {}), ("slow", {"delay_s": 17})], ids=["idp_down", "idp_slow"])
async def test_E_A10_002_idp_unavailable_at_token_exchange(rig, oidc, mode, params):
    """E-A10-002 (IdP down/slow at callback): code obtained, then the IdP stops answering.
    Secure: no token, a clean 4xx/502/503/504 without internals, finished within BOUND_S.
    Suspected: httpx errors / raise_for_status() on userinfo are unhandled -> HTTP 500."""
    http, flow = await _prepared_flow(rig, oidc)
    try:
        await set_mode(oidc, mode, **params)
        t0 = time.monotonic()
        r = await callback(http, flow.code, flow.returned_state)
        elapsed = time.monotonic() - t0
        assert not token_of(r), "session issued while IdP unavailable"
        assert r.status_code in (400, 401, 403, 502, 503, 504), f"IdP {mode}: HTTP {r.status_code} (want clean fail-closed, not 500)"
        _no_internals(r, rig, f"idp {mode}")
        assert elapsed < BOUND_S, f"callback waited {elapsed:.1f}s for a {mode} IdP"
    finally:
        await http.aclose()


async def test_E_A10_002_idp_down_at_login_with_cold_discovery_cache(rig, oidc, restart_backend):
    """E-A10-002 (IdP down at /login): after a backend restart the discovery document is not
    cached. Secure: /api/auth/login answers a clean 502/503/504 without internals, in bounded time."""
    async with Restarted(restart_backend):
        await set_mode(oidc, "down")
        async with new_http(rig) as http:
            t0 = time.monotonic()
            r = await http.get("/api/auth/login")
            assert time.monotonic() - t0 < BOUND_S
            assert r.status_code in (502, 503, 504), f"login with IdP down -> HTTP {r.status_code}"
            _no_internals(r, rig, "login idp down")
        await set_mode(oidc, "ok")


# --------------------------------------------------------------------------- E-A10-002 gateway / OCR

async def _scan(admin, engine, meter_id=None):
    data = {"engine": engine}
    if meter_id:
        data["meter_id"] = meter_id
    t0 = time.monotonic()
    r = await asyncio.wait_for(
        admin.post("/api/ocr/scan", files={"file": ("m.png", TINY_PNG, "image/png")}, data=data), 400
    )
    return r, time.monotonic() - t0


@pytest.mark.parametrize(
    "target,mode,engine",
    [
        ("gateway", "down", "llm"),
        ("gateway", "unauthorized", "llm"),
        ("gateway", "garbage", "llm"),
        ("ocr", "down", "ocr"),
        ("ocr", "unauthorized", "ocr"),
        ("ocr", "garbage", "ocr"),
    ],
    ids=lambda v: str(v),
)
async def test_E_A10_002_gateway_ocr_failure_modes_fail_closed(rig, client, gateway, ocr, restart_backend, target, mode, engine):
    """E-A10-002 (gateway/OCR down, 401, garbage): scan fails closed. Secure: either a clean
    502/503/504 with a fixed text (no internals, no echo of API keys/URLs) or a 200 that
    claims no detection (detected_value null); never 500; bounded time; a failed scan leaves
    no orphan file in the upload directory (no partial write)."""
    ctl = gateway if target == "gateway" else ocr
    admin = await get_client(client, "admin")
    await set_mode(ctl, mode)
    before = _files(rig)
    r, elapsed = await _scan(admin, engine)
    assert r.status_code != 500 and r.status_code != 429, f"{target} {mode}: HTTP {r.status_code}"
    assert elapsed < BOUND_S, f"{target} {mode}: {elapsed:.1f}s"
    _no_internals(r, rig, f"{target} {mode}")
    for secret in (cfg(rig, "GATEWAY_API_KEY"), cfg(rig, "OCR_API_KEY"), cfg(rig, "GATEWAY_URL"), cfg(rig, "OCR_URL")):
        assert not secret or secret not in r.text, f"{target} {mode}: response echoes configuration"
    if r.status_code == 200:
        assert r.json().get("detected_value") in (None, ""), f"{target} {mode}: value invented from a {mode} backend"
    else:
        assert r.status_code in (502, 503, 504), f"{target} {mode}: HTTP {r.status_code}"
        orphans = _files(rig) - before
        assert not orphans, f"failed scan left {len(orphans)} uploaded file(s) behind (image stored before OCR succeeded)"


async def test_E_A10_002_slow_backends_bounded_and_do_not_starve_api(rig, client, gateway, ocr, restart_backend):
    """E-A10-002 (slow): slow gateway/OCR/IdP. Secure: a single scan completes (or fails)
    within the configured bound; parallel slow scans do not make unrelated endpoints
    (health, property list) unresponsive (> 5 s) and /api/health itself stays quick."""
    async with contextlib.nullcontext():  # limiter is disabled rig-wide (rig/rig_app.py): no restart for a fresh rate-limit window needed
        admin = await get_client(client, "admin")
        await set_mode(gateway, "slow", delay_s=8)
        await set_mode(ocr, "slow", delay_s=8)
        r, elapsed = await _scan(admin, "llm")
        assert r.status_code in (200, 502, 503, 504), f"slow gateway: HTTP {r.status_code}"
        assert elapsed < BOUND_S, f"single scan took {elapsed:.1f}s with an 8s gateway"

        pending = [asyncio.create_task(_scan(admin, "llm")) for _ in range(12)]
        await asyncio.sleep(1.5)
        lat = {}
        async with new_http(rig) as anon:
            for label, coro in {
                "live": anon.get("/api/health/live"),
                "properties": admin.get("/api/properties/"),
            }.items():
                t0 = time.monotonic()
                resp = await coro
                lat[label] = (resp.status_code, time.monotonic() - t0)
            t0 = time.monotonic()
            h = await anon.get("/api/health")
            lat["health"] = (h.status_code, time.monotonic() - t0)
        results = await asyncio.gather(*pending, return_exceptions=True)
        assert all(isinstance(x, tuple) and x[0].status_code != 500 for x in results), "slow parallel scans gave 500/errors"
        for label, (code, secs) in lat.items():
            assert code in (200, 503), f"{label}: HTTP {code}"
            assert secs < 5.0, f"{label} took {secs:.1f}s while scans were pending (API starved)"


@pytest.mark.parametrize(
    "overrides,label",
    [
        ({"OCR_BACKEND": "ollama", "OLLAMA_URL": "", "GATEWAY_BASE_URL": "", "GATEWAY_API_KEY": "", "OCR_URL": ""}, "llm_and_ocr_unconfigured"),
        ({"OCR_URL": ""}, "ocr_unconfigured_gateway_configured"),
    ],
    ids=["llm_and_ocr_unconfigured", "ocr_unconfigured_gateway_configured"],
)
async def test_E_A10_002_optional_subsystems_unconfigured(rig, client, restart_backend, overrides, label):
    """E-A10-002 (unconfigured): backend restarted with the optional subsystem(s) unconfigured.
    Secure: starts, /api/health stays 200 (core only), scan with an explicitly requested
    missing engine answers a fixed 503 (no 500, no internals, no orphan file, no invented
    value); engine=auto never invents a reading."""
    async with Restarted(restart_backend, overrides):
        admin = await get_client(client, "admin")
        async with new_http(rig) as anon:
            assert await wait_healthy(anon, 90), "backend did not become healthy with optional subsystems unconfigured"
            h = (await anon.get("/api/health")).json()
        if "OCR_URL" in overrides:
            assert h["ocr"] is False
        for engine in ("ocr", "llm", "auto"):
            before = _files(rig)
            r, elapsed = await _scan(admin, engine)
            assert r.status_code != 500 and r.status_code != 429, f"{label}/{engine}: HTTP {r.status_code}"
            assert elapsed < BOUND_S
            _no_internals(r, rig, f"{label}/{engine}")
            if r.status_code == 200:
                body = r.json()
                if engine == "ocr" or (engine == "auto" and "OLLAMA_URL" in overrides):
                    assert not body.get("detected_value"), f"{label}/{engine}: value invented without a backend"
            else:
                assert r.status_code == 503, f"{label}/{engine}: HTTP {r.status_code}"
                assert not (_files(rig) - before), f"{label}/{engine}: failed scan left an orphan file"
