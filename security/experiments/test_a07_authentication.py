"""A07 Authentication Failures (E-A07-001 .. E-A07-003, plus E-A07-004 extension).

Assumed fixtures (CONTRACT.md): rig, client(role), oidc (fake IdP control client),
barrier_gather(n, coro_factory), restart_backend(env_overrides).
Strict assertions: a failing test is a weakness.
"""
import contextlib
import os
import time

import pytest

from idp_helpers import (
    ADMIN_GROUP, MANAGER_GROUP, USER_GROUP, Restarted, authorize, barrier, callback, cfg, get_client,
    me, new_http, set_claims, set_mode, signin, start_flow, status_of, token_of, unique_identity, users_by_email,
)

pytestmark = pytest.mark.security

# the rig runs with the rate limiter disabled (RIG_DISABLE_LIMITER=1); E-A07-001 restarts the backend with the real one
LIMITER_ON = {"RIG_DISABLE_LIMITER": None}


# --------------------------------------------------------------------------- E-A07-001

async def test_E_A07_001_parallel_bruteforce_superadmin_login(rig, restart_backend, barrier_gather):
    """E-A07-001: 200 parallel wrong-password attempts against /api/auth/superadmin-login
    from one source, then the correct password. Secure: the 5/minute limit holds under
    concurrency (at most 5 responses are evaluated as 401, the rest 429), no 5xx, and the
    correct password is also refused (429) while the window is exhausted."""
    async with Restarted(restart_backend, LIMITER_ON):  # real limiter (rig default: off), fresh in-memory window
        async with new_http(rig) as http:
            user = cfg(rig, "SUPERADMIN_USER")
            results = await barrier(
                barrier_gather, 200,
                lambda i: http.post("/api/auth/superadmin-login", json={"username": user, "password": f"wrong-{i}"}),
            )
            codes = [status_of(r) for r in results]
            assert not [c for c in codes if c >= 500 or c == -1], f"5xx/errors: {sorted(set(codes))}"
            evaluated = codes.count(401)
            assert evaluated <= 5, f"{evaluated} password guesses evaluated in one window (limit 5/minute)"
            assert codes.count(200) == 0
            ok = await http.post(
                "/api/auth/superadmin-login",
                json={"username": user, "password": cfg(rig, "SUPERADMIN_PASSWORD")},
            )
            assert ok.status_code == 429, f"correct password accepted while rate limit exhausted: {ok.status_code}"


async def test_E_A07_001_limit_not_bypassable_by_forwarded_for(rig, restart_backend):
    """E-A07-001 (bypass): 40 sequential wrong guesses, each with a different X-Forwarded-For.
    Secure: the limiter keys on a value the client cannot choose, so still at most 5 guesses
    are evaluated. Suspected: backend/Dockerfile starts uvicorn with --forwarded-allow-ips='*'
    and slowapi keys on request.client.host, so a spoofed header rotates the bucket."""
    async with Restarted(restart_backend, LIMITER_ON):
        async with new_http(rig) as http:
            user = cfg(rig, "SUPERADMIN_USER")
            codes = []
            for i in range(40):
                r = await http.post(
                    "/api/auth/superadmin-login",
                    json={"username": user, "password": f"wrong-{i}"},
                    headers={"X-Forwarded-For": f"203.0.113.{i + 1}", "X-Real-IP": f"198.51.100.{i + 1}"},
                )
                codes.append(r.status_code)
            assert not [c for c in codes if c >= 500]
            assert codes.count(401) <= 5, f"rate limit bypassed with spoofed X-Forwarded-For: {codes.count(401)} guesses evaluated"


async def test_E_A07_001_trusted_proxy_rightmost_untrusted_entry_is_the_key(rig, restart_backend):
    """E-A07-001 (trusted proxy variant): the rig peer (loopback) is declared a trusted proxy via
    TRUSTED_PROXIES=127.0.0.0/8, as a real reverse proxy would be. The proxy appends the address it saw
    to the *right* of whatever the client sent. Secure: the key is the rightmost entry that is not a
    trusted proxy, so a spoofed leftmost entry neither rotates the bucket (at most 5 guesses for one real
    client) nor leaks into another real client's bucket (a second real client is still evaluated)."""
    async with Restarted(restart_backend, {**LIMITER_ON, "TRUSTED_PROXIES": "127.0.0.0/8"}):
        async with new_http(rig) as http:
            user = cfg(rig, "SUPERADMIN_USER")

            async def guess(i, real, spoof):
                r = await http.post(
                    "/api/auth/superadmin-login",
                    json={"username": user, "password": f"wrong-{i}"},
                    headers={"X-Forwarded-For": f"{spoof}, {real}"},
                )
                return r.status_code

            codes = [await guess(i, "198.51.100.7", f"203.0.113.{i + 1}") for i in range(20)]
            assert not [c for c in codes if c >= 500]
            assert codes.count(401) <= 5, f"spoofed leftmost XFF entry changed the key: {codes.count(401)} guesses evaluated"
            assert codes.count(429) >= 15, f"no limiting for one real client behind the trusted proxy: {codes}"
            # a different real client (rightmost entry) has its own bucket: proves the key is the rightmost entry,
            # not the proxy peer for everybody
            other = await guess(99, "198.51.100.8", "203.0.113.1")
            assert other == 401, f"second real client shares the first client's bucket (got {other})"
            # garbage rightmost entry: falls back to the peer, never to a left (client-controlled) entry
            r = await http.post("/api/auth/superadmin-login", json={"username": user, "password": "x"},
                                headers={"X-Forwarded-For": "203.0.113.200, not-an-ip"})
            assert r.status_code in (401, 429)


async def test_E_A07_004_nonascii_and_malformed_credentials_no_5xx(rig, restart_backend):
    """E-A07-004 (extension of E-A07-001): non-ASCII, huge and wrongly typed credentials.
    Secure: 401/422, never 5xx. Suspected: secrets.compare_digest(str, str) raises TypeError
    for non-ASCII strings, giving an unauthenticated HTTP 500."""
    async with contextlib.nullcontext():  # limiter is off rig-wide (rig/rig_app.py); this test is not about the limit
        async with new_http(rig) as http:
            user = cfg(rig, "SUPERADMIN_USER")
            bodies = [
                {"username": user, "password": "pässwörd-ÄÖÜ-€"},
                {"username": "admïn", "password": "x"},
                {"username": user, "password": "x" * 100_000},
                {"username": user, "password": None},
                {"username": 1, "password": ["a"]},
            ]
            bad = []
            for b in bodies:
                r = await http.post("/api/auth/superadmin-login", json=b)
                if r.status_code >= 500 or r.status_code == 200:
                    bad.append((str(b)[:40], r.status_code))
            assert not bad, f"unexpected responses: {bad}"


# --------------------------------------------------------------------------- E-A07-002

async def test_E_A07_002_pkce_parameters_in_authorization_request(rig):
    """E-A07-002 (PKCE present): every /login redirect carries a fresh S256 challenge and a
    fresh high-entropy state; no verifier or client secret is exposed; redirect_uri cannot be
    influenced by the caller."""
    async with new_http(rig) as http:
        flows = [await start_flow(http) for _ in range(20)]
        assert all(f.method == "S256" for f in flows), "code_challenge_method is not S256"
        assert all(f.challenge and len(f.challenge) >= 43 for f in flows), "missing/short code_challenge"
        assert len({f.challenge for f in flows}) == 20, "code_challenge repeats"
        assert len({f.state for f in flows}) == 20, "state repeats"
        assert all(len(f.state) >= 22 for f in flows), "state below 128 bit"
        assert all(f.query.get("response_type") == "code" for f in flows)
        for f in flows:
            low = f.authorize_url.lower()
            assert "client_secret" not in low and "code_verifier" not in low
        r = await http.get("/api/auth/login", params={"redirect_uri": "https://evil.example/cb", "client_id": "evil"})
        loc = r.headers["location"]
        assert "evil.example" not in loc and "client_id=evil" not in loc, "caller-controlled redirect_uri/client_id"


async def test_E_A07_002_pkce_verifier_mismatch_and_missing_params_rejected(rig, oidc):
    """E-A07-002 (mismatch/missing): the code of login A presented with the state of login B
    (verifier mismatch at the IdP), and callbacks lacking code or state. Secure: no token,
    4xx and never 5xx."""
    sub, email = unique_identity("pkce")
    await set_claims(oidc, [USER_GROUP], sub, email)
    async with new_http(rig) as http:
        a = await authorize(http, await start_flow(http))
        b = await authorize(http, await start_flow(http))
        r = await callback(http, a.code, b.returned_state)
        assert not token_of(r), "token issued although code_verifier does not match the code_challenge"
        assert 400 <= r.status_code < 500, f"verifier mismatch -> HTTP {r.status_code}"

        for params in ({}, {"code": "x"}, {"state": "y"}, {"code": "", "state": ""}):
            r = await http.get("/api/auth/callback", params=params)
            assert 400 <= r.status_code < 500 and not token_of(r), f"{params}: HTTP {r.status_code}"

        # An authorization code obtained for another login must not work without its state either
        c = await authorize(http, await start_flow(http))
        r = await callback(http, c.code, "not-a-state")
        assert not token_of(r) and 400 <= r.status_code < 500


# --------------------------------------------------------------------------- E-A07-003

HUGE = 100_000
_OPTS = [ADMIN_GROUP, MANAGER_GROUP, USER_GROUP]

# (id, groups, expected) ; expected = role string, "reject" (403, no user row) or "admin|reject"
_CASES = [
    ("list_admin", [ADMIN_GROUP], "admin"),
    ("list_manager", [MANAGER_GROUP], "manager"),
    ("list_user", [USER_GROUP], "user"),
    ("list_user_and_admin_highest_wins", [USER_GROUP, ADMIN_GROUP], "admin"),
    ("string_exact_admin_documented", ADMIN_GROUP, "admin|reject"),
    ("string_substring_xadminx", f"x{ADMIN_GROUP}x", "reject"),
    ("string_prefix_x", f"x{ADMIN_GROUP}", "reject"),
    ("string_suffix_x", f"{ADMIN_GROUP}x", "reject"),
    ("string_admin_dash_evil", f"{ADMIN_GROUP}-evil", "reject"),
    ("string_not_admin", f"not-{ADMIN_GROUP}", "reject"),
    ("string_manager_substring", f"x{MANAGER_GROUP}x", "reject"),
    ("string_user_substring", f"x{USER_GROUP}x", "reject"),
    ("string_space_separated_groups", f"{USER_GROUP} {ADMIN_GROUP}", "reject"),
    ("string_comma_separated_groups", f"{USER_GROUP},{ADMIN_GROUP}", "reject"),
    ("string_json_encoded_list", f'["{ADMIN_GROUP}"]', "reject"),
    ("nested_list", [[ADMIN_GROUP]], "reject"),
    ("nested_object_in_list", [{"name": ADMIN_GROUP}], "reject"),
    ("dict_with_admin_key", {ADMIN_GROUP: True}, "reject"),
    ("dict_with_admin_key_false", {ADMIN_GROUP: False}, "reject"),
    ("dict_groups_wrapper", {"groups": [ADMIN_GROUP]}, "reject"),
    ("case_variant", [ADMIN_GROUP.upper()], "reject"),
    ("leading_space", [f" {ADMIN_GROUP}"], "reject"),
    ("trailing_newline", [f"{ADMIN_GROUP}\n"], "reject"),
    ("unicode_lookalike", [ADMIN_GROUP.replace("a", "а")], "reject"),
    ("null_byte_suffix", [f"{ADMIN_GROUP}\x00"], "reject"),
    ("null", None, "reject"),
    ("empty_list", [], "reject"),
    ("empty_string", "", "reject"),
    ("int", 42, "reject"),
    ("bool_true", True, "reject"),
    ("float", 3.14, "reject"),
    ("list_of_non_strings", [None, 1, True, 2.5], "reject"),
    ("huge_list_no_match", [f"g{i}" for i in range(HUGE)], "reject"),
    ("huge_list_admin_last_exact", [f"g{i}" for i in range(HUGE)] + [ADMIN_GROUP], "admin|reject"),
    ("huge_string_containing_admin", ("a" * 2_000_000) + ADMIN_GROUP, "reject"),
    ("deeply_nested", [[[[[[[[[[[ADMIN_GROUP]]]]]]]]]]], "reject"),
]


@pytest.mark.parametrize("name,groups,expected", _CASES, ids=[c[0] for c in _CASES])
async def test_E_A07_003_group_claim_shapes(rig, client, oidc, name, groups, expected):
    """E-A07-003: group claim shape via fake IdP mode `claims`. Secure: only EXACT membership
    in the configured group list grants a role; every other shape (substring of a string,
    dict key, nested, wrong type, null, huge) is rejected with 403 (no user row, no token) and
    never causes a 5xx. Known seeded weakness FR-016: `oidc_admin_group in groups` is a substring
    test for strings and a key test for dicts, and None/int crash with TypeError (HTTP 500)."""
    superadmin = await get_client(client, "superadmin")
    sub, email = unique_identity("grp")
    await set_claims(oidc, groups, sub, email)
    async with new_http(rig, timeout=120) as http:
        started = time.monotonic()
        resp, token, _ = await signin(http)
        elapsed = time.monotonic() - started
        assert resp.status_code < 500, f"{name}: HTTP {resp.status_code} (unhandled error on hostile claim)"
        assert elapsed < 60, f"{name}: sign-in took {elapsed:.1f}s"
        rows = await users_by_email(superadmin, email)
        if expected == "reject" or (expected == "admin|reject" and not token):
            assert resp.status_code == 403 and not token, f"{name}: not rejected (HTTP {resp.status_code})"
            assert rows == [], f"{name}: user row created for rejected sign-in"
            return
        assert token, f"{name}: legitimate exact membership refused (HTTP {resp.status_code})"
        role = (await me(http, token)).json()["role"]
        want = expected.split("|")[0]
        assert role == want, f"{name}: role {role!r}, expected {want!r}"


# --------------------------------------------------------------------------- E-A07-008 (feature 010, F-017)

def _binder_of(resp) -> str | None:
    return resp.cookies.get("oidc_binder")


async def _users_named(superadmin, email: str) -> list:
    return await users_by_email(superadmin, email)


async def test_E_A07_008_login_binder_cookie_attributes(rig):
    """E-A07-008 (login CSRF, F-017): /api/auth/login sets an `oidc_binder` cookie that is HttpOnly, SameSite=Lax,
    scoped to /api/auth (no Domain), short lived and different for every login."""
    async with new_http(rig) as http:
        raws, values = [], []
        for _ in range(3):
            r = await http.get("/api/auth/login")
            assert r.status_code in (302, 303, 307)
            sc = [v for k, v in r.headers.multi_items() if k.lower() == "set-cookie" and v.startswith("oidc_binder=")]
            assert len(sc) == 1, f"expected exactly one oidc_binder Set-Cookie, got {sc}"
            raws.append(sc[0]); values.append(sc[0].split(";", 1)[0].split("=", 1)[1])
        low = raws[0].lower()
        assert "httponly" in low and "samesite=lax" in low and "path=/api/auth" in low, raws[0]
        assert "domain=" not in low, raws[0]
        import re as _re
        age = _re.search(r"max-age=(\d+)", low)
        assert age and 0 < int(age.group(1)) <= 900, raws[0]
        assert len(set(values)) == 3 and all(len(v) >= 32 for v in values), "binder not unique / too short"


async def test_E_A07_008_callback_requires_the_initiating_browsers_cookie(rig, client, oidc):
    """E-A07-008 (login CSRF, F-017): the callback of a login only succeeds with the oidc_binder cookie that /login of
    the SAME login issued. Without cookie, with the cookie of another login, with a tampered value -> 400, no login
    code, no user row; the state is consumed by the failed attempt (a retry with the right cookie also fails).
    Control: the right cookie works."""
    superadmin = await get_client(client, "superadmin")
    results = {}
    for label in ("control", "no_cookie", "foreign_cookie", "tampered", "retry_after_failure"):
        sub, email = unique_identity(f"bind-{label[:6]}")
        await set_claims(oidc, [USER_GROUP], sub, email)
        async with new_http(rig) as victim, new_http(rig) as attacker:
            flow = await authorize(victim, await start_flow(victim))
            other = await start_flow(attacker)            # attacker's own login supplies a foreign cookie
            assert flow.binder and other.binder and flow.binder != other.binder
            if label == "control":
                r = await callback(victim, flow.code, flow.returned_state)
            elif label == "no_cookie":
                async with new_http(rig) as nobody:      # a browser that never visited /login: empty cookie jar
                    r = await callback(nobody, flow.code, flow.returned_state, binder=None)
            elif label == "foreign_cookie":
                r = await callback(attacker, flow.code, flow.returned_state, binder=other.binder)
            elif label == "tampered":
                r = await callback(victim, flow.code, flow.returned_state, binder=flow.binder[:-2] + ("AA" if flow.binder[-2:] != "AA" else "BB"))
            else:
                bad = await callback(attacker, flow.code, flow.returned_state, binder=other.binder)
                assert bad.status_code == 400
                r = await callback(victim, flow.code, flow.returned_state)
            rows = await _users_named(superadmin, email)
            results[label] = (r.status_code, token_of(r), len(rows))
    assert results["control"][0] in (302, 303, 307) and results["control"][1] and results["control"][2] == 1, results
    for label in ("no_cookie", "foreign_cookie", "tampered", "retry_after_failure"):
        assert results[label] == (400, None, 0), f"{label}: {results[label]} (expected 400, no code, no user)"


async def test_E_A07_008_id_token_with_wrong_nonce_is_rejected(rig, client, oidc):
    """E-A07-008 (nonce, F-017): the IdP returns an id_token whose nonce is not the one of this login (fake IdP mode
    `wrong_nonce`) -> 400, no login code, no user row. Control: the same flow with a matching nonce succeeds."""
    superadmin = await get_client(client, "superadmin")
    sub, email = unique_identity("nonce")
    await set_claims(oidc, [USER_GROUP], sub, email)
    async with new_http(rig) as http:
        resp, token, _ = await signin(http)
        assert token, f"control sign-in failed: {resp.status_code}"
    sub2, email2 = unique_identity("nonce2")
    await set_claims(oidc, [USER_GROUP], sub2, email2)
    await set_mode(oidc, "wrong_nonce", groups=[USER_GROUP], sub=sub2, email=email2, preferred_username=f"u-{sub2}")
    async with new_http(rig) as http:
        resp, token, _ = await signin(http)
        assert resp.status_code == 400 and not token, f"wrong nonce accepted: {resp.status_code}"
    assert await _users_named(superadmin, email2) == []


# --------------------------------------------------------------------------- E-A07-009 (feature 010, F-018 / F-019)

async def _superadmin_row(superadmin, rig):
    r = await superadmin.get("/api/admin/users")
    rows = [u for u in r.json() if u["username"] == cfg(rig, "SUPERADMIN_USER")]
    assert len(rows) == 1 and rows[0]["role"] == "superadmin"
    return rows[0]


_BAD_SUBS = [
    ("omitted", {"omit": ["sub"]}),
    ("null", {"sub": None}),
    ("empty", {"sub": ""}),
    ("blank", {"sub": "   "}),
    ("int", {"sub": 12345}),
    ("list", {"sub": ["a"]}),
    ("object", {"sub": {"a": 1}}),
    ("bool", {"sub": True}),
    ("too_long", {"sub": "s" * 300}),
]


@pytest.mark.parametrize("name,extra", _BAD_SUBS, ids=[c[0] for c in _BAD_SUBS])
async def test_E_A07_009_userinfo_without_usable_sub_is_502_and_superadmin_untouched(rig, client, oidc, name, extra):
    """E-A07-009 (F-018): a userinfo without a usable `sub` (absent, null, empty, blank, wrong type, > 255 chars)
    gives 502 -- not 500, not a login code -- and never reads or changes the break-glass superadmin row (which has
    `oidc_sub IS NULL`): same id, role, e-mail and token_version before and after, no user row for the e-mail."""
    superadmin = await get_client(client, "superadmin")
    before = await _superadmin_row(superadmin, rig)
    _, email = unique_identity("nosub")
    params = {"groups": [ADMIN_GROUP], "email": email, "preferred_username": f"u-nosub-{name}", **extra}
    await set_mode(oidc, "claims", **params)
    async with new_http(rig) as http:
        resp, token, _ = await signin(http)
    assert resp.status_code == 502 and not token, f"{name}: HTTP {resp.status_code}"
    assert await _users_named(superadmin, email) == []
    after = await _superadmin_row(superadmin, rig)
    assert after == before, f"superadmin row changed: {before} -> {after}"
    assert (await superadmin.get("/api/auth/me")).status_code == 200, "superadmin token revoked by the bad login"


async def test_E_A07_009_username_collisions_do_not_merge_or_crash(rig, client, oidc):
    """E-A07-009 (F-019): (a) two identities with the same `preferred_username`, (b) a new identity that claims the
    superadmin's username -> sign-in succeeds (no 500), every identity gets its own user with a derived unique
    username, the superadmin row is unchanged and not merged. (c) A new identity with an e-mail already used by
    another account -> 409 (not 500, no merge, victim unchanged)."""
    superadmin = await get_client(client, "superadmin")
    before = await _superadmin_row(superadmin, rig)
    sa_name = cfg(rig, "SUPERADMIN_USER")
    clash = f"clash-{unique_identity()[0]}"
    users = []
    for pname in (clash, clash, sa_name):
        sub, email = unique_identity("coll")
        await set_claims(oidc, [USER_GROUP], sub, email, preferred_username=pname)
        async with new_http(rig) as http:
            resp, token, _ = await signin(http)
            assert resp.status_code in (302, 303, 307) and token, f"{pname}: sign-in failed with HTTP {resp.status_code}"
            ident = (await me(http, token)).json()
        users.append((sub, email, ident))
    ids = {u[2]["id"] for u in users}
    assert len(ids) == 3 and before["id"] not in ids, "identities were merged"
    names = [u[2]["username"] for u in users]
    assert len(set(names)) == 3 and names[2] != sa_name, f"usernames not unique: {names}"
    assert await _superadmin_row(superadmin, rig) == before

    victim_sub, victim_email, victim = users[0]
    sub2, _ = unique_identity("emailclash")
    await set_claims(oidc, [USER_GROUP], sub2, victim_email, preferred_username=f"u-{sub2}")
    async with new_http(rig) as http:
        resp, token, _ = await signin(http)
    assert resp.status_code == 409 and not token, f"e-mail collision answered {resp.status_code}"
    rows = await _users_named(superadmin, victim_email)
    assert [r["id"] for r in rows] == [victim["id"]], "e-mail collision created or merged an account"


# --------------------------------------------------------------------------- E-A07-010 (feature 011, F-030)

_OCR_SERVICE_RUNNER = r'''
import sys, types
sys.path.insert(0, sys.argv[1])
stub = types.ModuleType("app.pipeline")
stub._run_ocr_on_file_sync = lambda path, already_cropped=False: {"raw_texts": [], "detected_value": "1", "detected_serial": None, "detection_method": "stub"}
stub._extract_serial_sync = lambda path: "1234567"
sys.modules["app.pipeline"] = stub
import uvicorn
from app.main import app
uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[2]), log_level="warning")
'''


async def test_E_A07_010_ocr_service_requires_bearer_key(rig, tmp_path):
    """E-A07-010 (F-030): the REAL ocr-service FastAPI app (app/main.py, EasyOCR pipeline replaced by a stub so no
    model is needed) is started on loopback with OCR_API_KEY set. Expected: /health stays open; /scan and /serial answer
    401 (never 500) with no key, a wrong key, a wrong scheme, an empty bearer and a non-ASCII key; the right key passes
    the gate (200); the same app with no key configured still serves (documented unauthenticated mode, control)."""
    import socket
    import subprocess
    import sys
    from pathlib import Path

    import httpx

    from idp_helpers import TINY_PNG

    repo = Path(__file__).resolve().parents[2]
    runner = tmp_path / "run_ocr.py"
    runner.write_text(_OCR_SERVICE_RUNNER)

    def start(key):
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "LANG": "C.UTF-8", "PYTHONUNBUFFERED": "1"}
        if key:
            env["OCR_API_KEY"] = key
        p = subprocess.Popen([sys.executable, "-I", str(runner), str(repo / "ocr-service"), str(port)], env=env,
                             cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        end = time.monotonic() + 40
        while time.monotonic() < end:
            if p.poll() is not None:
                raise AssertionError(f"ocr-service exited: {p.stdout.read().decode()[-800:]}")
            try:
                if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).status_code == 200:
                    return p, port
            except httpx.HTTPError:
                time.sleep(0.3)
        p.kill()
        raise AssertionError("ocr-service did not start")

    files = lambda: {"file": ("m.png", TINY_PNG, "image/png")}  # noqa: E731
    key = "rig-ocr-key-" + os.urandom(8).hex()
    p, port = start(key)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=20) as c:
            assert (await c.get("/health")).status_code == 200, "/health must stay open for probes"
            bad_headers = [
                {}, {"Authorization": "Bearer wrong"}, {"Authorization": f"Basic {key}"}, {"Authorization": "Bearer"},
                {"Authorization": key}, {"Authorization": f"Bearer {key}x"}, {"Authorization": f"Bearer {key[:-1]}"},
                {"Authorization": "Bearer ä".encode("latin-1")},
            ]
            for path in ("/scan", "/serial"):
                for h in bad_headers:
                    r = await c.post(path, files=files(), headers=h)
                    assert r.status_code == 401, f"{path} with {h!r}: HTTP {r.status_code} {r.text[:120]}"
                    assert key not in r.text
                ok = await c.post(path, files=files(), headers={"Authorization": f"Bearer {key}"})
                assert ok.status_code == 200, f"{path} with the right key: HTTP {ok.status_code} {ok.text[:120]}"
    finally:
        p.terminate(); p.wait(10)

    p, port = start(None)   # control: no key configured -> open by design (startup warning)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=20) as c:
            assert (await c.post("/scan", files=files())).status_code == 200
    finally:
        p.terminate(); p.wait(10)


async def test_E_A07_010_backend_with_wrong_ocr_key_fails_closed(rig, client, restart_backend):
    """E-A07-010 (F-030, backend side): the backend must present the shared key. With a wrong OCR_API_KEY the (key
    checking) OCR stand-in answers 401 and the scan ends as a clean 502/503 -- no invented reading, no 500 -- while
    the rig's own key works (control: scan 200)."""
    from idp_helpers import TINY_PNG

    admin = await get_client(client, "admin")
    r = await admin.post("/api/ocr/scan", files={"file": ("m.png", TINY_PNG, "image/png")}, data={"engine": "ocr"})
    assert r.status_code == 200 and r.json().get("detected_value"), f"control scan failed: {r.status_code} {r.text[:200]}"
    async with Restarted(restart_backend, {"OCR_API_KEY": "not-the-key"}):
        admin = await get_client(client, "admin")
        r = await admin.post("/api/ocr/scan", files={"file": ("m.png", TINY_PNG, "image/png")}, data={"engine": "ocr"})
        assert r.status_code in (502, 503), f"wrong OCR key -> HTTP {r.status_code} {r.text[:200]}"
        assert "detected_value" not in r.text
