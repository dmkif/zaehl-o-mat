"""A06 Insecure Design: race and logic experiments (E-A06-001 .. E-A06-004).

All races are driven with an asyncio barrier (all coroutines released at the same
instant) and repeated RIG_RACE_ROUNDS times (default 100, research.md R4); the
failure rate is printed by the conftest `repeat` helper and every deviation is a
failed round. A failing test is a real weakness: assertions are strict.

Assumed fixtures (CONTRACT.md): rig, client(role), oidc (control client for the fake
IdP, auto-reset), barrier_gather(n, coro_factory), repeat(n, fn).
"""
import asyncio
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from idp_helpers import (
    ADMIN_GROUP, USER_GROUP, authorize, barrier, callback, cfg, get_client, make_meter, me,
    new_http, run_rounds, set_claims, signin, start_flow, status_of, token_of, unique_identity,
    users_by_email,
)

ROUNDS = int(os.environ.get("RIG_RACE_ROUNDS", "100"))
PARALLEL_SIGNINS = int(os.environ.get("RIG_RACE_SIGNINS", "100"))
PARALLEL_WRITES = int(os.environ.get("RIG_RACE_WRITES", "20"))

pytestmark = [pytest.mark.security, pytest.mark.race]


@pytest.fixture(autouse=True)
async def _isolate_floods(request, restart_backend):
    """The sign-in floods (100 parallel callbacks) exhaust the backend's DB pool and can leave it wedged for
    minutes (see rerun notes, B-32). Restart it afterwards so the next experiment starts from a healthy process."""
    yield
    if any(k in request.node.name for k in ("parallel_first_signin", "role_change_vs_signin")):
        await asyncio.to_thread(restart_backend({}).__enter__)


def _age_pkce_state(rig, state: str, minutes: int) -> int:
    eng = create_engine(cfg(rig, "DATABASE_URL"))
    try:
        with eng.begin() as c:
            r = c.execute(
                text("UPDATE oidc_pkce_states SET created_at = now() - make_interval(mins => :m) WHERE state = :s"),
                {"m": minutes, "s": state},
            )
            return r.rowcount
    finally:
        eng.dispose()


async def test_E_A06_001_parallel_first_signin_one_user(rig, client, oidc, barrier_gather, repeat):
    """E-A06-001: 100 parallel first sign-ins of one identity via the fake IdP.
    Secure: exactly one user row per identity, every sign-in succeeds (302 + token),
    all tokens belong to the same user, no 5xx; repeated >= 100 rounds with fresh identities."""
    admin = await get_client(client, "superadmin")

    async def one_round():
        sub, email = unique_identity("race1")
        await set_claims(oidc, [USER_GROUP], sub, email)
        async with new_http(rig, timeout=20) as http:
            async def prep(_i):
                return await authorize(http, await start_flow(http))
            flows = await asyncio.gather(*[prep(i) for i in range(PARALLEL_SIGNINS)])
            results = await barrier(
                barrier_gather, PARALLEL_SIGNINS,
                lambda i: callback(http, flows[i].code, flows[i].returned_state),
            )
            codes = [status_of(r) for r in results]
            assert not [c for c in codes if c >= 500 or c == -1], f"5xx/errors among sign-ins: {sorted(set(codes))}; {[r for r in results if not hasattr(r, 'status_code')][:2]}"
            assert all(c in (302, 303, 307) for c in codes), f"legit first sign-ins not all succeeded: {sorted(set(codes))}"
            tokens = [token_of(r) for r in results]
            assert all(tokens), "sign-in without token"
            ids = {(await me(http, tokens[i])).json()["id"] for i in (0, len(tokens) // 2, -1)}
            rows = await users_by_email(admin, email)
            assert len(rows) == 1, f"{len(rows)} user rows for one identity"
            assert ids == {rows[0]["id"]}, "tokens belong to different users"

    failures = await run_rounds(repeat, ROUNDS, one_round, "E-A06-001")
    assert not failures, f"{len(failures)}/{ROUNDS} rounds failed, e.g. {failures[:3]}"


def _write_image(rig, content: bytes, name: str | None = None) -> str:
    name = name or f"race-{uuid.uuid4().hex}.png"
    (Path(cfg(rig, "UPLOAD_DIR")) / name).write_bytes(content)
    return name


@pytest.mark.parametrize("variant", ["distinct_files_same_bytes", "same_path"])
async def test_E_A06_002_parallel_same_image_hash(rig, client, barrier_gather, repeat, variant):
    """E-A06-002 (same image): parallel POST of readings referencing the same image hash.
    Secure: exactly one 201, all others 409 duplicate_image, never a 5xx, exactly one
    stored reading with that hash. (check-then-insert without unique index = suspected race.)"""
    admin = await get_client(client, "admin")
    pid, mid = await make_meter(admin, "dup-img")
    created_files: list[str] = []

    async def one_round():
        content = b"\x89PNG\r\n\x1a\n" + os.urandom(64)
        names = (
            [_write_image(rig, content) for _ in range(PARALLEL_WRITES)]
            if variant == "distinct_files_same_bytes"
            else [_write_image(rig, content)] * PARALLEL_WRITES
        )
        created_files.extend(names)
        results = await barrier(
            barrier_gather, PARALLEL_WRITES,
            lambda i: admin.post(
                f"/api/properties/{pid}/meters/{mid}/readings/",
                json={"value": str(1000 + i), "image_path": f"uploads/{names[i]}"},
            ),
        )
        codes = [status_of(r) for r in results]
        assert not [c for c in codes if c >= 500 or c == -1], f"5xx/errors: {sorted(set(codes))}"
        assert codes.count(201) == 1, f"{codes.count(201)} readings created for one image hash; codes={sorted(set(codes))}"
        assert all(c in (201, 409) for c in codes), f"undefined responses: {sorted(set(codes))}"
        lst = await admin.get(f"/api/properties/{pid}/meters/{mid}/readings/?limit=10000")
        by_hash: dict[str, int] = {}
        for rd in lst.json():
            if rd["image_hash"]:
                by_hash[rd["image_hash"]] = by_hash.get(rd["image_hash"], 0) + 1
        dup = {h: n for h, n in by_hash.items() if n > 1}
        assert not dup, f"duplicate stored readings per image hash: {list(dup.values())}"

    try:
        failures = await run_rounds(repeat, ROUNDS, one_round, f"E-A06-002[{variant}]")
    finally:
        for n in set(created_files):
            (Path(cfg(rig, "UPLOAD_DIR")) / n).unlink(missing_ok=True)
        await admin.delete(f"/api/properties/{pid}")
    assert not failures, f"{len(failures)}/{ROUNDS} rounds failed, e.g. {failures[:3]}"


async def test_E_A06_002_parallel_identical_reading(rig, client, barrier_gather, repeat):
    """E-A06-002 (same reading): parallel POST of byte-identical readings (same meter, value,
    read_at, source). Secure: one stored or a defined conflict response (409); no 5xx, no
    duplicate rows. Note: the app has no uniqueness rule for readings without image."""
    admin = await get_client(client, "admin")
    pid, mid = await make_meter(admin, "dup-reading")

    async def one_round():
        value = str(5000 + int(uuid.uuid4().int % 100000))
        read_at = "2026-01-01T00:00:00+00:00"
        results = await barrier(
            barrier_gather, PARALLEL_WRITES,
            lambda i: admin.post(
                f"/api/properties/{pid}/meters/{mid}/readings/",
                json={"value": value, "read_at": read_at, "source": "manual"},
            ),
        )
        codes = [status_of(r) for r in results]
        assert not [c for c in codes if c >= 500 or c == -1], f"5xx/errors: {sorted(set(codes))}"
        lst = await admin.get(f"/api/properties/{pid}/meters/{mid}/readings/?limit=10000")
        same = [rd for rd in lst.json() if float(rd["value"]) == float(value)]
        assert len(same) == 1, f"{len(same)} identical readings stored from {PARALLEL_WRITES} parallel posts"

    try:
        failures = await run_rounds(repeat, ROUNDS, one_round, "E-A06-002[identical]")
    finally:
        await admin.delete(f"/api/properties/{pid}")
    assert not failures, f"{len(failures)}/{ROUNDS} rounds failed, e.g. {failures[:3]}"


async def test_E_A06_003_role_change_vs_signin(rig, client, oidc, barrier_gather, repeat):
    """E-A06-003: concurrent admin role change and sign-in of the same user.
    Secure: no 5xx, the user row stays unique, final role is one of the two legitimate
    outcomes (IdP-derived 'user' or admin-set 'manager'), never superadmin/admin."""
    admin = await get_client(client, "admin")
    superadmin = await get_client(client, "superadmin")

    async def one_round():
        sub, email = unique_identity("race3")
        await set_claims(oidc, [USER_GROUP], sub, email)
        async with new_http(rig, timeout=20) as http:
            resp, token, _ = await signin(http)
            assert token, f"initial sign-in failed: {resp.status_code}"
            uid = (await me(http, token)).json()["id"]
            n = 10
            flows = [await authorize(http, await start_flow(http)) for _ in range(n // 2)]

            def op(i):
                if i % 2 == 0:
                    return admin.patch(f"/api/admin/users/{uid}/role", json={"role": "manager"})
                f = flows[i // 2]
                return callback(http, f.code, f.returned_state)

            results = await barrier(barrier_gather, n, op)
            codes = [status_of(r) for r in results]
            assert not [c for c in codes if c >= 500 or c == -1], f"5xx/errors: {codes}"
            rows = await users_by_email(superadmin, email)
            assert len(rows) == 1, f"{len(rows)} rows"
            assert rows[0]["role"] in ("user", "manager"), f"unexpected final role {rows[0]['role']}"

    failures = await run_rounds(repeat, ROUNDS, one_round, "E-A06-003")
    assert not failures, f"{len(failures)}/{ROUNDS} rounds failed, e.g. {failures[:3]}"


async def test_E_A06_003_privilege_not_kept_after_idp_removal(rig, client, oidc):
    """E-A06-003 (stale privilege, design expectation): an admin whose IdP groups are removed
    is rejected at sign-in (403). Secure: the account must not keep admin power afterwards
    (deactivated/demoted or earlier token rejected). Suspected: the row keeps role=admin."""
    sub, email = unique_identity("race3b")
    await set_claims(oidc, [ADMIN_GROUP], sub, email)
    async with new_http(rig, timeout=20) as http:
        resp, token, _ = await signin(http)
        assert token, f"admin sign-in failed: {resp.status_code}"
        assert (await http.get("/api/admin/users", headers={"Authorization": f"Bearer {token}"})).status_code == 200
        await set_claims(oidc, [], sub, email)
        resp2, token2, _ = await signin(http)
        assert resp2.status_code == 403 and not token2, f"removed user not rejected: {resp2.status_code}"
        again = await http.get("/api/admin/users", headers={"Authorization": f"Bearer {token}"})
        assert again.status_code in (401, 403), (
            f"user removed from all IdP groups still has admin API access (HTTP {again.status_code})"
        )


async def test_E_A06_004_state_replay_rejected(rig, oidc):
    """E-A06-004 (replay): completing the callback twice with the same code+state.
    Secure: first 302 with token, second 400 and no token."""
    sub, email = unique_identity("race4a")
    await set_claims(oidc, [USER_GROUP], sub, email)
    async with new_http(rig, timeout=20) as http:
        flow = await authorize(http, await start_flow(http))
        first = await callback(http, flow.code, flow.returned_state)
        assert token_of(first), f"first callback failed: {first.status_code}"
        second = await callback(http, flow.code, flow.returned_state)
        assert second.status_code in (400, 401, 403) and not token_of(second), f"replay accepted: {second.status_code}"


async def test_E_A06_004_parallel_state_reuse(rig, oidc, barrier_gather, repeat):
    """E-A06-004 (reuse under concurrency): 10 parallel callbacks with one state.
    Secure: at most one success, the rest 4xx, never 5xx (single-use state, atomic consume)."""
    sub, email = unique_identity("race4b")
    await set_claims(oidc, [USER_GROUP], sub, email)

    async def one_round():
        async with new_http(rig, timeout=20) as http:
            flow = await authorize(http, await start_flow(http))
            results = await barrier(barrier_gather, 10, lambda i: callback(http, flow.code, flow.returned_state))
            codes = [status_of(r) for r in results]
            assert not [c for c in codes if c >= 500 or c == -1], f"5xx/errors: {sorted(set(codes))}"
            ok = [c for c in codes if c in (302, 303, 307)]
            assert len(ok) <= 1, f"one state produced {len(ok)} sessions"

    failures = await run_rounds(repeat, ROUNDS, one_round, "E-A06-004[reuse]")
    assert not failures, f"{len(failures)}/{ROUNDS} rounds failed, e.g. {failures[:3]}"


async def test_E_A06_004_expired_and_forged_state_rejected(rig, oidc):
    """E-A06-004 (expiry / forgery): state older than 10 min, unknown, injection-shaped and
    oversized state values. Secure: 400/4xx for all, never 5xx, no token."""
    sub, email = unique_identity("race4c")
    await set_claims(oidc, [USER_GROUP], sub, email)
    async with new_http(rig, timeout=20) as http:
        flow = await authorize(http, await start_flow(http))
        n = await asyncio.to_thread(_age_pkce_state, rig, flow.state, 11)
        assert n == 1, "could not age the state row (rig DB access)"
        r = await callback(http, flow.code, flow.returned_state)
        assert r.status_code in (400, 401, 403) and not token_of(r), f"expired state accepted/handled badly: {r.status_code}"
        for bad in ["nonexistent", "' OR '1'='1", "x" * 20000, "%00", "../../etc/passwd", ""]:
            r = await callback(http, flow.code, bad)
            assert 400 <= r.status_code < 500 and not token_of(r), f"state {bad[:20]!r}: HTTP {r.status_code}"


async def test_E_A06_004_state_bound_to_initiating_browser(rig, oidc):
    """E-A06-004 (login CSRF / session fixation): browser A starts the login, browser B (other
    cookie jar, no relation to A) completes the callback with A's code+state.
    Secure: rejected, because the state is bound to the initiating browser (cookie/nonce).
    Suspected: the state is only a server-side lookup key, so B receives a session."""
    sub, email = unique_identity("race4d")
    await set_claims(oidc, [USER_GROUP], sub, email)
    async with new_http(rig, timeout=20) as browser_a, new_http(rig, timeout=20) as browser_b:
        flow = await authorize(browser_a, await start_flow(browser_a))
        r = await callback(browser_b, flow.code, flow.returned_state)
        assert not token_of(r), "callback completed by a different browser than the one that started the login"
