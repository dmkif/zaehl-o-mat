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
    me, new_http, set_claims, signin, start_flow, status_of, token_of, unique_identity, users_by_email,
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
