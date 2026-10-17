"""A04 Cryptographic failures: JWT handling (E-A04-001).

Every forged / malformed / stale token must be answered with 401 (never 200,
never 5xx). Tokens are built by hand (hmac + base64) so the experiment controls
every byte of header, payload and signature independently of the app's own
JWT library.

Fixtures used: ``rig`` (parsed rig.env), ``seed`` (seed.json), ``client``
(admin access for the user-deleted case). See security/rig/CONTRACT.md.
"""
import base64
import hashlib
import hmac
import inspect
import json
import time
import uuid

import httpx
import pytest

PROBE = "/api/auth/me"          # authenticated endpoint that echoes the account
PROBE2 = "/api/properties/"     # second, different dependency chain


# --------------------------------------------------------------------------- helpers
def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jdump(obj) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode()


def forge(header, payload, key: bytes | str | None, hash_name: str = "sha256", sig: bytes | None = None) -> str:
    """header/payload dicts (or raw bytes) -> compact JWT. ``key=None`` => empty signature."""
    h = header if isinstance(header, bytes) else jdump(header)
    p = payload if isinstance(payload, bytes) else jdump(payload)
    signing_input = f"{b64(h)}.{b64(p)}".encode()
    if sig is None:
        if key is None:
            sig = b""
        else:
            k = key.encode() if isinstance(key, str) else key
            sig = hmac.new(k, signing_input, getattr(hashlib, hash_name)).digest()
    return f"{signing_input.decode()}.{b64(sig)}"


def claims(sub: str, **extra) -> dict:
    d = {"sub": sub, "role": "user", "exp": int(time.time()) + 600}
    d.update(extra)
    return d


@pytest.fixture
async def http(rig):
    async with httpx.AsyncClient(base_url=rig["BACKEND_URL"], timeout=30, follow_redirects=False) as c:
        yield c


async def _call(http, token: str, path: str = PROBE, scheme: str = "Bearer") -> httpx.Response:
    return await http.get(path, headers={"Authorization": f"{scheme} {token}"})


async def _assert_rejected(http, token: str, label: str, paths=(PROBE, PROBE2)):
    for path in paths:
        r = await _call(http, token, path)
        assert r.status_code == 401, f"{label}: {path} answered {r.status_code} {r.text[:200]!r}, expected 401"


@pytest.fixture
def user_a_id(seed) -> str:
    return seed["users"]["user_a"]["id"]


# --------------------------------------------------------------------------- sanity
@pytest.mark.security
async def test_E_A04_001_valid_token_is_accepted_control(http, rig, user_a_id):
    """E-A04-001 (control): a correctly signed, unexpired HS256 token is accepted.

    Without this the rejection tests below could pass vacuously (e.g. wrong secret in the harness).
    Expected: 200 and the account of ``sub``.
    """
    tok = forge({"alg": "HS256", "typ": "JWT"}, claims(user_a_id), rig["JWT_SECRET_KEY"])
    r = await _call(http, tok)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == user_a_id


# --------------------------------------------------------------------------- alg none
@pytest.mark.security
@pytest.mark.parametrize("alg", ["none", "None", "NONE", "nOnE", "", None])
async def test_E_A04_001_alg_none_rejected(http, user_a_id, alg):
    """E-A04-001 alg=none: unsigned token (empty signature) with any spelling of "none".

    Expected (secure): 401 on every probe endpoint.
    """
    header = {"alg": alg, "typ": "JWT"} if alg is not None else {"typ": "JWT"}
    tok = forge(header, claims(user_a_id), None)
    await _assert_rejected(http, tok, f"alg={alg!r} empty signature")
    # also without the trailing dot / with a bogus signature
    await _assert_rejected(http, tok.rstrip("."), f"alg={alg!r} no signature segment")
    await _assert_rejected(http, forge(header, claims(user_a_id), None, sig=b"AAAA"), f"alg={alg!r} junk signature")


# --------------------------------------------------------------------------- wrong key / algorithm
@pytest.mark.security
@pytest.mark.parametrize("key", ["wrong-secret", "", "change-me-in-production", "secret", "a" * 64])
async def test_E_A04_001_wrong_key_rejected(http, user_a_id, key):
    """E-A04-001 wrong key: HS256 token signed with a guessable / default / empty secret.

    Expected (secure): 401 (includes the documented default secret `change-me-in-production`).
    """
    tok = forge({"alg": "HS256", "typ": "JWT"}, claims(user_a_id), key)
    await _assert_rejected(http, tok, f"signed with {key[:12]!r}")


@pytest.mark.security
@pytest.mark.parametrize("alg,hash_name", [("HS384", "sha384"), ("HS512", "sha512")])
async def test_E_A04_001_other_hmac_algorithm_with_correct_key_rejected(http, rig, user_a_id, alg, hash_name):
    """E-A04-001 algorithm not pinned: valid secret but header says HS384/HS512.

    Expected (secure): 401, the verifier accepts only the configured algorithm (HS256).
    """
    tok = forge({"alg": alg, "typ": "JWT"}, claims(user_a_id), rig["JWT_SECRET_KEY"], hash_name)
    await _assert_rejected(http, tok, f"{alg} with correct key")


def _rsa_pair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return key, pub_pem


@pytest.mark.security
async def test_E_A04_001_hs_rs_algorithm_confusion_rejected(http, user_a_id):
    """E-A04-001 HS/RS confusion: classic attack, HMAC-sign using an RSA public key as the "secret".

    Variants: header RS256 / HS256 / RS512 with HMAC over the PEM (and DER) public key, plus a
    genuine RS256 signature made with an attacker key, with the attacker key embedded as `jwk`
    and as `jku`/`kid` hints. Expected (secure): 401 for all.
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key, pub_pem = _rsa_pair()
    pub_der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    for alg in ("RS256", "HS256", "RS512", "ES256", "PS256"):
        for secret in (pub_pem, pub_der):
            tok = forge({"alg": alg, "typ": "JWT"}, claims(user_a_id), secret)
            await _assert_rejected(http, tok, f"alg={alg} HMAC with public key")

    # genuine RS256 signature by an attacker key
    header = {"alg": "RS256", "typ": "JWT", "jku": "http://127.0.0.1:9/jwks.json", "kid": "attacker"}
    signing_input = f"{b64(jdump(header))}.{b64(jdump(claims(user_a_id)))}".encode()
    sig = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    await _assert_rejected(http, f"{signing_input.decode()}.{b64(sig)}", "RS256 by attacker key")

    # attacker key embedded in the header as JWK
    nums = key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "n": b64(nums.n.to_bytes((nums.n.bit_length() + 7) // 8, "big")),
        "e": b64(nums.e.to_bytes((nums.e.bit_length() + 7) // 8, "big")),
    }
    header = {"alg": "RS256", "typ": "JWT", "jwk": jwk}
    signing_input = f"{b64(jdump(header))}.{b64(jdump(claims(user_a_id)))}".encode()
    sig = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    await _assert_rejected(http, f"{signing_input.decode()}.{b64(sig)}", "RS256 with embedded jwk")


@pytest.mark.security
async def test_E_A04_001_unusual_headers_rejected(http, rig, user_a_id):
    """E-A04-001 header abuse: `crit`, `b64:false`, `zip`, array/number `alg`, non-object header.

    Expected (secure): 401, never 500.
    """
    secret = rig["JWT_SECRET_KEY"]
    headers = [
        {"alg": ["HS256"]},
        {"alg": 256},
        {"alg": "HS256", "crit": ["exp"], "exp": 1},
        {"alg": "HS256", "crit": ["b64"], "b64": False},
        {"alg": "HS256", "zip": "DEF"},
        ["HS256"],
        "HS256",
        None,
    ]
    for h in headers:
        tok = forge(h, claims(user_a_id), secret)
        await _assert_rejected(http, tok, f"header {h!r}")


# --------------------------------------------------------------------------- expired
@pytest.mark.security
@pytest.mark.parametrize("delta", [-1, -60, -3600, -10 * 365 * 86400])
async def test_E_A04_001_expired_token_rejected(http, rig, user_a_id, delta):
    """E-A04-001 expired: correctly signed token whose `exp` is in the past.

    Expected (secure): 401.
    """
    tok = forge({"alg": "HS256"}, claims(user_a_id, exp=int(time.time()) + delta), rig["JWT_SECRET_KEY"])
    await _assert_rejected(http, tok, f"exp {delta}s")


@pytest.mark.security
async def test_E_A04_001_token_without_expiry_rejected(http, rig, user_a_id):
    """E-A04-001 missing `exp`: a signed token that never expires.

    Expected (secure): 401. (Suspected weakness: the app calls `claims.validate()` without
    requiring `exp`, so a non-expiring token with a valid signature would be accepted.)
    """
    payload = {"sub": user_a_id, "role": "user"}
    tok = forge({"alg": "HS256"}, payload, rig["JWT_SECRET_KEY"])
    await _assert_rejected(http, tok, "no exp claim")


@pytest.mark.security
@pytest.mark.parametrize("exp", ["9999999999", "never", None, True, [], {}, -1, 0, 1e400])
async def test_E_A04_001_malformed_exp_rejected(http, rig, user_a_id, exp):
    """E-A04-001 malformed `exp`: string, null, bool, containers, zero, negative, overflow.

    Expected (secure): 401 (`exp` must be a number in the future), never 5xx.
    """
    payload = claims(user_a_id)
    payload["exp"] = exp
    raw = json.dumps(payload).encode()  # allows Infinity for 1e400
    tok = forge({"alg": "HS256"}, raw, rig["JWT_SECRET_KEY"])
    await _assert_rejected(http, tok, f"exp={exp!r}")


@pytest.mark.security
async def test_E_A04_001_not_yet_valid_token_rejected(http, rig, user_a_id):
    """E-A04-001 `nbf` in the future (and absurd `iat`).

    Expected (secure): 401.
    """
    tok = forge({"alg": "HS256"}, claims(user_a_id, nbf=int(time.time()) + 3600), rig["JWT_SECRET_KEY"])
    await _assert_rejected(http, tok, "nbf in future")


# --------------------------------------------------------------------------- tampered
@pytest.mark.security
async def test_E_A04_001_tampered_token_rejected(http, rig, seed, user_a_id):
    """E-A04-001 tampered: payload changed after signing (different sub, role=superadmin, exp), flipped
    or truncated signature, swapped segments.

    Expected (secure): 401 for all; the app never trusts an unverified payload.
    """
    secret = rig["JWT_SECRET_KEY"]
    good = forge({"alg": "HS256", "typ": "JWT"}, claims(user_a_id), secret)
    h, p, s = good.split(".")
    admin_id = seed["users"]["admin"]["id"]

    forged_payloads = [
        claims(admin_id),
        claims(user_a_id, role="superadmin"),
        claims(user_a_id, exp=int(time.time()) + 10**9),
    ]
    for pl in forged_payloads:
        await _assert_rejected(http, f"{h}.{b64(jdump(pl))}.{s}", f"payload swap {list(pl.items())[:2]}")

    flipped = bytearray(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))
    flipped[0] ^= 0x01
    await _assert_rejected(http, f"{h}.{p}.{b64(bytes(flipped))}", "signature bit flip")
    await _assert_rejected(http, f"{h}.{p}.{s[:-4]}", "truncated signature")
    await _assert_rejected(http, f"{h}.{p}.", "stripped signature")
    await _assert_rejected(http, f"{p}.{h}.{s}", "swapped header/payload")
    await _assert_rejected(http, f"{good}.extra", "extra segment")
    await _assert_rejected(http, f"{h}.{p}", "two segments")
    await _assert_rejected(http, good[:-1] + ("A" if good[-1] != "A" else "B"), "last char changed")


@pytest.mark.security
async def test_E_A04_001_role_claim_not_trusted(http, rig, user_a_id):
    """E-A04-001 privilege claim in a *validly signed* token must not grant rights.

    The token for the plain user carries role=superadmin; the app has to take the role from the
    database. Expected (secure): admin endpoint answers 403.
    """
    tok = forge({"alg": "HS256"}, claims(user_a_id, role="superadmin", roles=["admin"], is_admin=True), rig["JWT_SECRET_KEY"])
    r = await _call(http, tok, "/api/admin/users")
    assert r.status_code == 403, f"{r.status_code} {r.text[:200]}"


# --------------------------------------------------------------------------- missing / malformed claims
@pytest.mark.security
@pytest.mark.parametrize(
    "sub",
    [
        "MISSING", None, "", 0, 123, True, [], ["x"], {}, {"id": "x"},
        "not-a-uuid", "1' OR '1'='1", "00000000-0000-0000-0000-000000000000", str(uuid.uuid4()),
        "../../etc/passwd", "A" * 10000,
    ],
    ids=lambda v: repr(v)[:24],
)
async def test_E_A04_001_missing_or_malformed_sub_rejected(http, rig, sub):
    """E-A04-001 missing claims: validly signed token with absent / null / wrongly typed / unknown `sub`.

    Expected (secure): 401 and *never* 5xx. (Suspected weakness: `uuid.UUID(<list/int>)` raises
    TypeError/AttributeError which `get_current_user` does not catch -> 500.)
    """
    payload = {"exp": int(time.time()) + 600}
    if sub != "MISSING":
        payload["sub"] = sub
    tok = forge({"alg": "HS256"}, payload, rig["JWT_SECRET_KEY"])
    await _assert_rejected(http, tok, f"sub={str(sub)[:30]!r}")


@pytest.mark.security
async def test_E_A04_001_payload_not_an_object_rejected(http, rig):
    """E-A04-001 payload is a JSON array / string / number / null.

    Expected (secure): 401, no 5xx.
    """
    for raw in (b"[]", b'["sub"]', b'"x"', b"1", b"null", b"{", b"\xff\xfe"):
        tok = forge({"alg": "HS256"}, raw, rig["JWT_SECRET_KEY"])
        await _assert_rejected(http, tok, f"payload {raw!r}")


# --------------------------------------------------------------------------- garbage / transport
@pytest.mark.security
async def test_E_A04_001_garbage_tokens_rejected(http):
    """E-A04-001 structurally invalid tokens. Expected (secure): 401 (or 4xx for oversized header), never 5xx."""
    for tok in ["x", "a.b", "a.b.c", "...", "....", "null", "undefined", "eyJ", "%00", "Bearer", "a b c"]:
        await _assert_rejected(http, tok, f"garbage {tok!r}")
    r = await _call(http, "A" * 100_000)
    assert r.status_code in (400, 401, 413, 431), f"100 KB token -> {r.status_code}"


@pytest.mark.security
async def test_E_A04_001_token_only_accepted_via_bearer_header(http, rig, user_a_id):
    """E-A04-001 transport: a valid token in query string, cookie or non-Bearer scheme must not authenticate.

    Expected (secure): 401 (tokens in URLs leak into logs and Referer headers).
    """
    tok = forge({"alg": "HS256"}, claims(user_a_id), rig["JWT_SECRET_KEY"])
    for q in ("token", "access_token", "jwt", "api_key"):
        r = await http.get(PROBE, params={q: tok})
        assert r.status_code == 401, f"?{q}= accepted ({r.status_code})"
    r = await http.get(PROBE, cookies={"access_token": tok, "token": tok, "session": tok})
    assert r.status_code == 401, f"cookie accepted ({r.status_code})"
    for scheme in ("Basic", "Token", "JWT", "Digest"):
        r = await _call(http, tok, scheme=scheme)
        assert r.status_code == 401, f"scheme {scheme} accepted ({r.status_code})"
    r = await http.get(PROBE, headers={"X-Forwarded-User": "rig-admin", "X-Auth-Request-User": "rig-admin",
                                       "X-Remote-User": "rig-admin"})
    assert r.status_code == 401, f"identity header accepted ({r.status_code})"


# --------------------------------------------------------------------------- account state
async def _client(client, name):
    c = client(name)
    if inspect.isawaitable(c):
        c = await c
    return c


def _db(rig):
    import psycopg2

    conn = psycopg2.connect(rig["DATABASE_URL"])
    conn.autocommit = True
    return conn


@pytest.mark.security
async def test_E_A04_001_deleted_or_deactivated_user_token_rejected(http, rig, client):
    """E-A04-001 user deleted / deactivated: a still-unexpired token of such an account.

    A disposable account is inserted straight into the rig database (no API creates users outside
    OIDC), a valid token is minted, then (a) the account is deactivated and (b) deleted through
    the admin API. Expected (secure): 200 before, 401 after each step.
    """
    uid = str(uuid.uuid4())
    name = f"rig-disposable-{uid[:8]}"
    conn = _db(rig)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, username, email, role, is_active, created_at) "
                "VALUES (%s, %s, %s, 'user', true, now())",
                (uid, name, f"{name}@rig.invalid"),
            )
        tok = forge({"alg": "HS256"}, claims(uid), rig["JWT_SECRET_KEY"])
        assert (await _call(http, tok)).status_code == 200, "control: disposable account must authenticate first"

        with conn.cursor() as cur:
            cur.execute("UPDATE users SET is_active = false WHERE id = %s", (uid,))
        await _assert_rejected(http, tok, "deactivated user")

        with conn.cursor() as cur:
            cur.execute("UPDATE users SET is_active = true WHERE id = %s", (uid,))
        assert (await _call(http, tok)).status_code == 200

        admin = await _client(client, "admin")
        r = await admin.delete(f"/api/admin/users/{uid}")
        assert r.status_code == 204, r.text
        await _assert_rejected(http, tok, "deleted user")
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (uid,))
        conn.close()
