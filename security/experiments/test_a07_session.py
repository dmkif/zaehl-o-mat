"""Feature 007/008/009 regression experiments (F-007, F-008, F-012, F-014).

E-A07-005  login code exchange: JWT never in the redirect, code single use / expired / unknown / parallel replay
E-A07-006  logout revokes the session token (and every other token of that user)
E-A07-007  role change (admin API, role change at sign-in, IdP group removal) revokes older tokens
E-A10-003  bulk-scan: 413 over the cumulative budget and over the body size, 400 over the file count,
           nothing stored and no OCR call for refused requests
E-A02-003  every meter type x unit x integration type is creatable on the migration-built rig DB (F-014)
"""
from __future__ import annotations

import base64
import socket
from contextlib import aclosing
from urllib.parse import urlparse
import json
import uuid

import httpx
import pytest
from sqlalchemy import create_engine, text

from idp_helpers import (
    USER_GROUP, ADMIN_GROUP, authorize, callback, cfg, code_of, exchange, exchange_token, get_client, jwt_in_url,
    me, new_http, set_claims, signin, start_flow, unique_identity,
)
from sec_common import list_files, upload_dir

pytestmark = pytest.mark.security


def _claims_of(jwt: str) -> dict:
    p = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))


def _sql(rig, stmt: str, **params):
    eng = create_engine(rig["DATABASE_URL"])
    try:
        with eng.begin() as c:
            res = c.execute(text(stmt), params)
            return res.scalar() if res.returns_rows else None
    finally:
        eng.dispose()


async def _login(rig, oidc, groups, sub=None, email=None, http=None):
    """Sign a (new or existing) identity in; returns (http, response, code, jwt)."""
    if sub is None:
        sub, email = unique_identity("sess")
    await set_claims(oidc, groups, sub, email)
    http = http or new_http(rig, timeout=30)
    flow = await authorize(http, await start_flow(http))
    resp = await callback(http, flow.code, flow.returned_state)
    code = code_of(resp)
    jwt = await exchange_token(http, code) if code else None
    return http, resp, code, jwt, sub, email


# ------------------------------------------------------------------------------------------ E-A07-005
async def test_E_A07_005_redirect_carries_code_not_jwt(rig, oidc):
    """E-A07-005 / F-008: the callback redirects to <app>/auth/token#code=<code>. No JWT anywhere in the Location
    header, the code is in the fragment (never in the query), the exchange returns a JWT carrying sub/role/tv/iat/exp."""
    async with new_http(rig, timeout=30) as http:
        sub, email = unique_identity("exch")
        await set_claims(oidc, [USER_GROUP], sub, email)
        flow = await authorize(http, await start_flow(http))
        resp = await callback(http, flow.code, flow.returned_state)
        assert resp.status_code in (302, 303, 307), resp.status_code
        loc = resp.headers["location"]
        assert "/auth/token#code=" in loc, loc
        assert "?" not in loc.split("#")[0], f"query string on the token redirect: {loc}"
        assert not jwt_in_url(resp), f"JWT or token= in the redirect: {loc}"
        code = code_of(resp)
        assert code and len(code) >= 32, "login code too short / missing"
        tok = await exchange_token(http, code)
        claims = _claims_of(tok)
        for k in ("sub", "role", "tv", "iat", "exp"):
            assert k in claims, f"JWT lacks {k}: {sorted(claims)}"
        assert isinstance(claims["tv"], int) and claims["exp"] > claims["iat"]
        assert (await me(http, tok)).status_code == 200


async def test_E_A07_005_code_is_single_use(rig, oidc):
    """E-A07-005: the second exchange of a code is refused (400), the first JWT keeps working."""
    http, resp, code, tok, _, _ = await _login(rig, oidc, [USER_GROUP])
    async with aclosing(http):
        assert tok
        again = await exchange(http, code)
        assert again.status_code == 400 and "access_token" not in again.text, (again.status_code, again.text[:200])
        assert (await me(http, tok)).status_code == 200


async def test_E_A07_005_expired_and_unknown_codes_refused(rig, oidc):
    """E-A07-005: an expired code (expires_at in the past), an unknown code, an empty/oversized/non-string code
    are refused with 4xx, never 5xx, never a token. The expired code is consumed (deleted) by the attempt."""
    sub, email = unique_identity("expd")
    await set_claims(oidc, [USER_GROUP], sub, email)
    async with new_http(rig, timeout=30) as http:
        flow = await authorize(http, await start_flow(http))
        code = code_of(await callback(http, flow.code, flow.returned_state))
        assert code
        stored = _sql(rig, "SELECT count(*) FROM login_codes WHERE user_id = (SELECT id FROM users WHERE oidc_sub = :s)", s=sub)
        assert stored == 1, "login code row missing"
        assert _sql(rig, "SELECT count(*) FROM login_codes WHERE code_hash = :c", c=code) == 0, \
            "the plaintext code is stored (only a hash may be)"
        _sql(rig, "UPDATE login_codes SET expires_at = now() - interval '1 minute' WHERE created_at > now() - interval '5 minutes'"
                  " AND user_id = (SELECT id FROM users WHERE oidc_sub = :s)", s=sub)
        r = await exchange(http, code)
        assert r.status_code == 400 and "access_token" not in r.text, (r.status_code, r.text[:200])
        for bad in ("nonexistent-" + uuid.uuid4().hex, "", "A" * 129, "A" * 5000):
            r = await exchange(http, bad)
            assert 400 <= r.status_code < 500 and "access_token" not in r.text, (bad[:20], r.status_code)
        for body in ({"code": 5}, {"code": None}, {"code": ["a"]}, {}, {"code": {"a": 1}}):
            r = await http.post("/api/auth/exchange", json=body)
            assert 400 <= r.status_code < 500 and "access_token" not in r.text, (body, r.status_code)
        r = await http.post("/api/auth/exchange", content=b"{", headers={"content-type": "application/json"})
        assert 400 <= r.status_code < 500
        assert _sql(rig, "SELECT count(*) FROM login_codes WHERE user_id = (SELECT id FROM users WHERE oidc_sub = :s)",
                    s=sub) == 0, "expired code row not consumed"


async def test_E_A07_005_parallel_replay_exactly_one_winner(rig, oidc, barrier_gather):
    """E-A07-005: 15 parallel exchanges of one code: exactly one 200, all others 4xx, no 5xx."""
    http, resp, code, _, _, _ = await _login(rig, oidc, [USER_GROUP])
    # _login already consumed the code; take a fresh one for a new identity
    await http.aclose()
    sub, email = unique_identity("par")
    await set_claims(oidc, [USER_GROUP], sub, email)
    async with new_http(rig, timeout=30) as h2:
        flow = await authorize(h2, await start_flow(h2))
        code = code_of(await callback(h2, flow.code, flow.returned_state))
        assert code
        res = await barrier_gather(15, lambda: exchange(h2, code))
        codes = [r.status_code if isinstance(r, httpx.Response) else -1 for r in res]
        assert not [c for c in codes if c >= 500 or c == -1], codes
        assert codes.count(200) == 1, f"single-use code redeemed {codes.count(200)} times: {sorted(codes)}"
        assert all(c == 400 for c in codes if c != 200), sorted(codes)


# ------------------------------------------------------------------------------------------ E-A07-006
async def test_E_A07_006_logout_revokes_token(rig, oidc):
    """E-A07-006 / F-007: after POST /auth/logout the token is dead (401 on /me), a second token issued earlier to
    the same user is dead too (counter, not a per-token list), a fresh sign-in works, logout with an invalid or
    already revoked token is a harmless 200 (nothing revoked), without Authorization also 200."""
    http, _, _, tok1, sub, email = await _login(rig, oidc, [USER_GROUP])
    async with aclosing(http):
        _, _, _, tok2, _, _ = await _login(rig, oidc, [USER_GROUP], sub, email, http=http)
        assert tok1 and tok2
        assert (await me(http, tok1)).status_code == 200 and (await me(http, tok2)).status_code == 200
        r = await http.post("/api/auth/logout", headers={"Authorization": f"Bearer {tok1}"})
        assert r.status_code == 200, r.text
        assert (await me(http, tok1)).status_code == 401, "token still valid after logout"
        assert (await me(http, tok2)).status_code == 401, "second session of the same user survives logout"
        for h in ({"Authorization": f"Bearer {tok1}"}, {"Authorization": "Bearer garbage"}, {}):
            assert (await http.post("/api/auth/logout", headers=h)).status_code == 200
        _, _, _, tok3, _, _ = await _login(rig, oidc, [USER_GROUP], sub, email, http=http)
        assert tok3 and (await me(http, tok3)).status_code == 200, "sign-in after logout broken"


async def test_E_A07_006_logout_does_not_touch_other_users(rig, oidc, client):
    """E-A07-006: logging a user out leaves other users' sessions alone."""
    other = await get_client(client, "user_b")
    assert (await other.get("/api/auth/me")).status_code == 200
    http, _, _, tok, _, _ = await _login(rig, oidc, [USER_GROUP])
    async with aclosing(http):
        assert (await http.post("/api/auth/logout", headers={"Authorization": f"Bearer {tok}"})).status_code == 200
    assert (await other.get("/api/auth/me")).status_code == 200, "foreign user lost their session"


# ------------------------------------------------------------------------------------------ E-A07-007
async def test_E_A07_007_admin_role_change_revokes_sessions(rig, oidc, client):
    """E-A07-007 / F-007: PATCH /admin/users/<id>/role invalidates every token the target holds (401), the
    target can sign in again and gets the new role; the acting admin's own session is untouched."""
    sa = await get_client(client, "superadmin")
    http, _, _, tok, sub, email = await _login(rig, oidc, [USER_GROUP])
    async with aclosing(http):
        uid = (await me(http, tok)).json()["id"]
        r = await sa.patch(f"/api/admin/users/{uid}/role", json={"role": "manager"})
        assert r.status_code == 200, r.text
        assert (await me(http, tok)).status_code == 401, "token survives an admin role change"
        assert (await sa.get("/api/auth/me")).status_code == 200, "acting admin lost their own session"
        _, _, _, tok2, _, _ = await _login(rig, oidc, [USER_GROUP], sub, email, http=http)
        assert tok2 and (await me(http, tok2)).status_code == 200


async def test_E_A07_007_role_change_at_signin_revokes_older_token(rig, oidc):
    """E-A07-007: the IdP promotes the user (user -> admin group); the next sign-in revokes the old user token and the
    new token carries the new role; with an unchanged role a re-login does NOT revoke older tokens."""
    http, _, _, tok_user, sub, email = await _login(rig, oidc, [USER_GROUP])
    async with aclosing(http):
        _, _, _, tok_same, _, _ = await _login(rig, oidc, [USER_GROUP], sub, email, http=http)
        assert (await me(http, tok_user)).status_code == 200, "re-login without role change revoked an older token"
        _, _, _, tok_admin, _, _ = await _login(rig, oidc, [ADMIN_GROUP], sub, email, http=http)
        assert tok_admin and (await me(http, tok_admin)).json()["role"] == "admin"
        assert (await me(http, tok_user)).status_code == 401, "pre-promotion token survives the role change"
        assert (await me(http, tok_same)).status_code == 401


async def test_E_A07_007_idp_group_removal_revokes_sessions(rig, oidc):
    """E-A07-007 / F-007: a known user whose IdP groups no longer include a permitted group is rejected (403) and
    every open session of that user stops working immediately."""
    http, _, _, tok, sub, email = await _login(rig, oidc, [USER_GROUP])
    async with aclosing(http):
        assert (await me(http, tok)).status_code == 200
        await set_claims(oidc, ["rig-no-such-group"], sub, email)
        flow = await authorize(http, await start_flow(http))
        resp = await callback(http, flow.code, flow.returned_state)
        assert resp.status_code == 403 and not code_of(resp)
        assert (await me(http, tok)).status_code == 401, "session survives IdP group removal"


# ------------------------------------------------------------------------------------------ E-A10-003
def _blob(n: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * max(0, n - 8)


async def test_E_A10_003_bulk_scan_limits(rig, client, restart_backend, gateway, ocr):
    """E-A10-003 / F-012 (feature 008): with MAX_BULK_TOTAL_BYTES=300000 and MAX_BULK_FILES=3:
      * files within the file count but over the cumulative budget -> 413, no stored file, no OCR/gateway call
      * more files than allowed -> 400
      * declared Content-Length over budget+1 MiB slack -> 413 before the form is parsed (body not even sent)
      * a chunked body growing past the limit -> 413 (or a closed connection), never a 5xx
      * a request within the limits still streams normally (control)."""
    async with restart_backend({"MAX_BULK_TOTAL_BYTES": "300000", "MAX_BULK_FILES": "3", "RIG_DISABLE_LIMITER": "1"}):
        admin = await get_client(client, "admin")
        root = upload_dir(rig)
        before = list_files(root)
        gw0, ocr0 = len(await gateway.requests()), len(await ocr.requests())

        over_budget = [("files", (f"b{i}.png", _blob(120_000), "image/png")) for i in range(3)]
        r = await admin.post("/api/ocr/bulk-scan", files=over_budget, data={"engine": "llm"})
        assert r.status_code == 413, (r.status_code, r.text[:200])

        too_many = [("files", (f"c{i}.png", _blob(100), "image/png")) for i in range(4)]
        r = await admin.post("/api/ocr/bulk-scan", files=too_many, data={"engine": "llm"})
        assert r.status_code == 400, (r.status_code, r.text[:200])

        # declared Content-Length far above the limit: refused from the headers alone
        big = 300_000 + 1024 * 1024 + 5
        u = urlparse(rig["BACKEND_URL"])
        with socket.create_connection((u.hostname, u.port), timeout=10) as sock:
            sock.sendall((f"POST /api/ocr/bulk-scan HTTP/1.1\r\nHost: {u.hostname}\r\n"
                          f"Authorization: {admin.headers['authorization']}\r\n"
                          f"Content-Type: multipart/form-data; boundary=x\r\nContent-Length: {big}\r\n"
                          "Connection: close\r\n\r\n").encode())  # headers only, the body is never sent
            status_line = sock.recv(4096).split(b"\r\n", 1)[0].decode("latin-1")
        assert " 413 " in status_line, status_line

        # chunked body past the limit
        async def gen():
            # a well-formed multipart prefix, so the form parser is happily consuming when the limit is hit
            yield (b'--x\r\nContent-Disposition: form-data; name="files"; filename="a.png"\r\n'
                   b'Content-Type: image/png\r\n\r\n')
            for _ in range(40):
                yield b"\x00" * 100_000
        try:
            r = await admin.post("/api/ocr/bulk-scan", content=gen(), headers={"content-type": "multipart/form-data; boundary=x"})
            assert r.status_code == 413, (r.status_code, r.text[:200])
        except (httpx.TransportError,):
            pass  # server closed the connection while the client was still sending: also a refusal

        assert list_files(root) == before, "refused bulk requests left files behind"
        assert len(await gateway.requests()) == gw0 and len(await ocr.requests()) == ocr0, "refused bulk request reached OCR/LLM"

        ok = [("files", ("ok.png", _blob(2000), "image/png"))]
        r = await admin.post("/api/ocr/bulk-scan", files=ok, data={"engine": "llm"})
        assert r.status_code == 200 and "text/event-stream" in r.headers.get("content-type", ""), (r.status_code, r.text[:200])
        assert "event: complete" in r.text


# ------------------------------------------------------------------------------------------ E-A02-003
@pytest.mark.parametrize("mtype", ["water", "electricity", "oil"])
@pytest.mark.parametrize("unit", ["m³", "kWh", "L"])
async def test_E_A02_003_every_meter_type_and_unit_creatable(rig, client, mtype, unit):
    """E-A02-003 / F-014 (migration 0008): every meter_type x unit pair creates a meter on the migration-built rig DB
    (201), reads back with the same enum labels, and takes a reading. Includes kWh and L, which the initial schema
    had under different labels."""
    admin = await get_client(client, "admin")
    p = await admin.post("/api/properties/", json={"name": f"enum-{uuid.uuid4().hex[:8]}"})
    assert p.status_code == 201, p.text
    pid = p.json()["id"]
    try:
        m = await admin.post(f"/api/properties/{pid}/meters/", json={"meter_type": mtype, "unit": unit, "name": "enum"})
        assert m.status_code == 201, f"{mtype}/{unit}: {m.status_code} {m.text[:200]}"
        body = m.json()
        assert body["meter_type"] == mtype and body["unit"] == unit
        rd = await admin.post(f"/api/properties/{pid}/meters/{body['id']}/readings/",
                              json={"value": "12.5", "read_at": "2026-01-01T00:00:00+00:00"})
        assert rd.status_code == 201, f"{mtype}/{unit} reading: {rd.status_code} {rd.text[:200]}"
    finally:
        await admin.delete(f"/api/properties/{pid}")


@pytest.mark.parametrize("itype", ["manual", "smart_meter", "shelly", "homeassistant"])
async def test_E_A02_003_every_integration_type_creatable(rig, client, itype):
    """E-A02-003 / F-014: every integration_type (incl. smart_meter) can be stored and read back."""
    admin = await get_client(client, "admin")
    p = await admin.post("/api/properties/", json={"name": f"enum-{uuid.uuid4().hex[:8]}"})
    pid = p.json()["id"]
    try:
        m = await admin.post(f"/api/properties/{pid}/meters/",
                             json={"meter_type": "electricity", "unit": "kWh", "name": "enum", "integration_type": itype})
        assert m.status_code == 201, f"{itype}: {m.status_code} {m.text[:200]}"
        assert m.json()["integration_type"] == itype
        g = await admin.get(f"/api/properties/{pid}/meters/{m.json()['id']}")
        assert g.status_code == 200 and g.json()["integration_type"] == itype
    finally:
        await admin.delete(f"/api/properties/{pid}")


# ------------------------------------------------------------------------------------------ E-A10-004
async def test_E_A10_004_bulk_scan_holds_no_db_transaction_while_gateway_is_slow(rig, client, gateway):
    """E-A10-004 / F-013 (feature 008): while a bulk scan waits 9 s for the LLM gateway, the backend holds no database
    connection for it: pg_stat_activity shows no `idle in transaction` session of the rig role (the old request-scoped
    session kept one for the whole stream), and the request itself completes with the stream's `complete` event."""
    import asyncio

    admin = await get_client(client, "admin")
    await gateway.set_mode("slow", delay_s=9)
    from sec_common import bulk_scan, png_ok

    task = asyncio.ensure_future(bulk_scan(admin, [("slow.png", png_ok(seed=77), "image/png")], engine="llm"))
    await asyncio.sleep(3)
    assert not task.done(), "bulk scan finished before the gateway answered; the probe window is wrong"
    idle_tx = await asyncio.to_thread(
        _sql, rig, "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND usename = 'rig' "
                   "AND state LIKE 'idle in transaction%' AND pid <> pg_backend_pid()")
    r = await task
    assert idle_tx == 0, f"{idle_tx} session(s) idle in transaction while the stream waited for the gateway"
    assert r.status_code == 200 and "event: complete" in r.text, (r.status_code, r.text[:200])
