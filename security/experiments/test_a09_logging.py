"""A09 Security Logging and Alerting (E-A09-001).

The rig backend runs with LOG_LEVEL=debug (CONTRACT.md). The experiment drives sign-ins,
failed logins, a rejected sign-in, an upload and error paths with UNIQUE marker values and
then greps the new part of backend_log() for them.

Assumed fixtures: rig, client(role), oidc, backend_log() (text, sync or async).
"""
import re
import uuid
from urllib.parse import urlparse

import pytest

from idp_helpers import (
    TINY_PNG, USER_GROUP, authorize, callback, cfg, get_client, maybe, new_http, set_claims, signin,
    start_flow, token_of,
)

pytestmark = pytest.mark.security

_ACCESS_LINE = re.compile(r'"\s*(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) \S+ HTTP/')
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{20,}")


def _context(log: str, needle: str) -> str:
    i = log.find(needle)
    line_start = log.rfind("\n", 0, i) + 1
    line_end = log.find("\n", i)
    line = log[line_start: line_end if line_end != -1 else None]
    return line.replace(needle, "<SECRET>")[:160]


async def test_E_A09_001_no_secrets_in_logs_and_security_events_present(rig, client, oidc, backend_log):
    """E-A09-001: after sign-ins, failed logins, a rejected sign-in, an upload and error paths:
    (a) no token, authorization code, client secret, password, JWT secret, API key, e-mail
    address or group name of a rejected login appears in the DEBUG log; (b) application-level
    security events exist for the failed login and the rejected sign-in (uvicorn access lines
    do not count, they carry no reason).
    Suspected: no auth logging at all (events missing), and uvicorn's access log records the
    callback query string (`code=` and `state=`)."""
    start = len(await maybe(backend_log()))
    mark = uuid.uuid4().hex[:12]
    wrong_pw = f"badpw-{mark}"
    email_ok, email_rej, email_err = (f"pii-{k}-{mark}@rig.example.test" for k in ("ok", "rej", "err"))
    rej_group = f"leakgrp-{mark}"
    secrets_found: dict[str, str] = {}  # label -> value (never printed)

    async with new_http(rig) as http:
        # 1. failed and successful superadmin logins
        user = cfg(rig, "SUPERADMIN_USER")
        r = await http.post("/api/auth/superadmin-login", json={"username": user, "password": wrong_pw})
        assert r.status_code in (401, 429)
        r = await http.post(
            "/api/auth/superadmin-login", json={"username": user, "password": cfg(rig, "SUPERADMIN_PASSWORD")}
        )
        if r.status_code == 200:
            secrets_found["superadmin access token"] = r.json()["access_token"]

        # 2. successful OIDC sign-in
        await set_claims(oidc, [USER_GROUP], f"sub-ok-{mark}", email_ok)
        flow = await authorize(http, await start_flow(http))
        resp = await callback(http, flow.code, flow.returned_state)
        tok = token_of(resp)
        assert tok, f"sign-in failed: {resp.status_code}"
        secrets_found["oidc session token"] = tok
        secrets_found["oidc authorization code (ok flow)"] = flow.code

        # 3. rejected sign-in (no permitted group)
        await set_claims(oidc, [rej_group], f"sub-rej-{mark}", email_rej)
        flow2 = await authorize(http, await start_flow(http))
        resp = await callback(http, flow2.code, flow2.returned_state)
        assert resp.status_code == 403
        secrets_found["oidc authorization code (rejected flow)"] = flow2.code

        # 4. error paths: forged bearer, invalid state, hostile claim (null groups)
        forged = "eyJhbGciOiJub25lIn0." + "e30" * 6 + "." + "sig" * 5
        r = await http.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"})
        assert r.status_code == 401
        await callback(http, "code-" + mark, "state-" + mark)
        await set_claims(oidc, None, f"sub-err-{mark}", email_err)
        await signin(http)

    # 5. upload by an authenticated user
    admin = await get_client(client, "admin")
    secrets_found["admin bearer token"] = admin.headers.get("authorization", "").split(" ")[-1]
    r = await admin.post("/api/ocr/scan", files={"file": (f"{mark}.png", TINY_PNG, "image/png")}, data={"engine": "auto"})
    assert r.status_code < 500 or r.status_code == 503

    new = (await maybe(backend_log()))[start:]
    assert new, "backend log is empty (rig must run the backend with LOG_LEVEL=debug)"

    needles = {
        **{k: v for k, v in secrets_found.items() if v},
        "failed-login password": wrong_pw,
        "superadmin password": cfg(rig, "SUPERADMIN_PASSWORD"),
        "JWT secret key": cfg(rig, "JWT_SECRET_KEY"),
        "OIDC client secret": cfg(rig, "OIDC_CLIENT_SECRET"),
        "gateway API key": cfg(rig, "GATEWAY_API_KEY"),
        "OCR API key": cfg(rig, "OCR_API_KEY"),
        "e-mail (successful sign-in)": email_ok,
        "e-mail (rejected sign-in)": email_rej,
        "e-mail (error sign-in)": email_err,
        "group name of rejected login": rej_group,
        "database password": urlparse(cfg(rig, "DATABASE_URL", "")).password,
    }
    problems = []
    for label, val in needles.items():
        if val and len(val) >= 6 and val in new:
            problems.append(f"{label} found in log: ...{_context(new, val)}...")
    if _JWT_RE.search(new):
        problems.append("a JWT-shaped string appears in the log")
    if _BEARER_RE.search(new):
        problems.append("an 'Authorization: Bearer <token>' value appears in the log")

    app_lines = [ln for ln in new.splitlines() if ln.strip() and not _ACCESS_LINE.search(ln)]
    def has(pattern):
        rx = re.compile(pattern, re.I)
        return any(rx.search(ln) for ln in app_lines)

    if not has(r"(superadmin|login|credential|password|auth).*(fail|invalid|denied|reject|wrong|unsuccess)"
               r"|(fail|invalid|denied|reject|wrong|unsuccess).*(superadmin|login|credential|password|auth)"):
        problems.append("no application-level security event for the failed superadmin login")
    if not has(r"(sign.?in|login|oidc|callback|authori[sz]ation).*(reject|denied|not authori|forbidden|not a member|no permitted)"
               r"|(reject|denied|not authori|forbidden|not a member|no permitted).*(sign.?in|login|oidc|callback|user)"):
        problems.append("no application-level security event for the rejected OIDC sign-in")
    assert not problems, "\n".join(problems)
