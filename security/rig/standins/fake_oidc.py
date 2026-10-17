"""Fake OIDC IdP (discovery, jwks, authorize, token, userinfo). Contract: security/rig/CONTRACT.md.
Binds 127.0.0.1 only. RSA key generated at start. Note: the backend only uses /userinfo, never the id_token;
token-level modes (bad_signature, alg_none, wrong_nonce) therefore affect the id_token only."""
import base64
import hashlib
import secrets
from urllib.parse import urlencode

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.responses import RedirectResponse
from jose import jwt
from jose.utils import long_to_base64

MODES = {"ok", "claims", "expired_token", "bad_signature", "alg_none", "wrong_nonce",
         "no_pkce_check", "slow", "down"}
import argparse
import asyncio
import json
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

_REDACT = {"authorization", "cookie", "x-api-key", "proxy-authorization"}
STATE = {"mode": "ok", "params": {}}
REQUESTS: list[dict] = []


def install_control(app: FastAPI) -> None:
    @app.middleware("http")
    async def _record(request: Request, call_next):
        if not request.url.path.startswith("/_control"):
            body = await request.body()
            headers = {k: ("<redacted>" if k.lower() in _REDACT else v) for k, v in request.headers.items()}
            REQUESTS.append({
                "ts": time.time(), "method": request.method, "path": request.url.path,
                "query": request.url.query, "headers": headers, "body_size": len(body),
                "mode": STATE["mode"],
            })
            del REQUESTS[:-5000]
        return await call_next(request)

    @app.post("/_control/mode")
    async def set_mode(request: Request):
        data = await request.json()
        mode = data.pop("mode", None)
        if not isinstance(mode, str) or mode not in MODES:
            return JSONResponse({"error": "unknown mode", "modes": sorted(MODES)}, status_code=400)
        STATE["mode"], STATE["params"] = mode, data
        return {"mode": mode, "params": data}

    @app.get("/_control/mode")
    async def get_mode():
        return STATE

    @app.post("/_control/reset")
    async def reset():
        STATE["mode"], STATE["params"] = "ok", {}
        REQUESTS.clear()
        return STATE

    @app.get("/_control/requests")
    async def get_requests():
        return REQUESTS


def main(app: FastAPI, default_port: int) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=default_port)
    ap.add_argument("--api-key", default=None, help="if set, require this Bearer key (401 otherwise)")
    ap.add_argument("--client-secret", default=None, help="oidc only: required client_secret at /token")
    args = ap.parse_args()
    ARGS.update(vars(args))
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


ARGS: dict = {}


def _gen():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()).decode()
    return k, pem


KEY, KEY_PEM = _gen()
_, WRONG_PEM = _gen()  # used for bad_signature
KID = "rig-key-1"
CODES: dict[str, dict] = {}
TOKENS: dict[str, dict] = {}

app = FastAPI()
install_control(app)


def _issuer(request: Request) -> str:
    return f"http://127.0.0.1:{request.url.port}"


def _claims() -> dict:
    """userinfo claims from the current mode params (groups may be any JSON value, incl. null)."""
    p = STATE["params"]
    gc = p.get("groups_claim", "groups")
    c = {"sub": p.get("sub", "rig-oidc-sub-1"), "email": p.get("email", "rig-oidc@example.test"),
         "preferred_username": p.get("preferred_username", "rig-oidc")}
    c[gc] = p["groups"] if "groups" in p else ["rig-users"]
    for k in p.get("omit", []):   # e.g. {"omit": ["sub"]}: claim absent (not null)
        c.pop(k, None)
    return c


async def _gate():
    if STATE["mode"] == "slow":
        await asyncio.sleep(float(STATE["params"].get("delay_s", 5)))
    if STATE["mode"] == "down":
        return JSONResponse({"error": "service unavailable"}, status_code=503)
    return None


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _id_token(request: Request, claims: dict, nonce: str | None, client_id: str) -> str:
    mode, now = STATE["mode"], int(time.time())
    body = {**claims, "iss": _issuer(request), "aud": client_id, "iat": now, "exp": now + 300}
    if nonce is not None:
        body["nonce"] = "wrong-" + nonce if mode == "wrong_nonce" else nonce
    if mode == "expired_token":
        body["iat"], body["exp"] = now - 7200, now - 3600
    if mode == "alg_none":
        h = _b64(json.dumps({"alg": "none", "typ": "JWT"}).encode())
        return f"{h}.{_b64(json.dumps(body).encode())}."
    pem = WRONG_PEM if mode == "bad_signature" else KEY_PEM
    return jwt.encode(body, pem, algorithm="RS256", headers={"kid": KID})


@app.get("/.well-known/openid-configuration")
async def discovery(request: Request):
    err = await _gate()
    if err:
        return err
    b = _issuer(request)
    return {"issuer": b, "authorization_endpoint": f"{b}/authorize", "token_endpoint": f"{b}/token",
            "userinfo_endpoint": f"{b}/userinfo", "jwks_uri": f"{b}/jwks",
            "response_types_supported": ["code"], "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": ["openid", "email", "profile", "groups"]}


@app.get("/jwks")
async def jwks():
    err = await _gate()
    if err:
        return err
    n = KEY.public_key().public_numbers()
    return {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": KID,
                      "n": long_to_base64(n.n).decode(), "e": long_to_base64(n.e).decode()}]}


@app.get("/authorize")
async def authorize(request: Request):
    """Auto-approves (no login UI) and redirects to redirect_uri with code+state."""
    err = await _gate()
    if err:
        return err
    q = request.query_params
    redirect_uri = q.get("redirect_uri")
    if not redirect_uri:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    code = secrets.token_urlsafe(24)
    CODES[code] = {"challenge": q.get("code_challenge"), "method": q.get("code_challenge_method"),
                   "nonce": q.get("nonce"), "client_id": q.get("client_id", "rig-client"),
                   "redirect_uri": redirect_uri}
    sep = "&" if "?" in redirect_uri else "?"
    out = {"code": code}
    if q.get("state") is not None:
        out["state"] = q["state"]
    return RedirectResponse(redirect_uri + sep + urlencode(out), status_code=302)


@app.post("/token")
async def token(request: Request):
    err = await _gate()
    if err:
        return err
    form = dict((await request.form()).items())
    if ARGS.get("client_secret") and form.get("client_secret") != ARGS["client_secret"]:
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    entry = CODES.pop(form.get("code", ""), None)  # single use
    if entry is None:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if STATE["mode"] != "no_pkce_check" and entry["challenge"]:
        verifier = form.get("code_verifier", "")
        if _b64(hashlib.sha256(verifier.encode()).digest()) != entry["challenge"]:
            return JSONResponse({"error": "invalid_grant", "error_description": "PKCE"}, status_code=400)
    claims = _claims()
    at = "at-" + secrets.token_urlsafe(24)
    TOKENS[at] = {"issued": time.time()}
    return {"access_token": at, "token_type": "Bearer", "expires_in": 300, "scope": "openid email profile groups",
            "id_token": _id_token(request, claims, entry["nonce"], entry["client_id"])}


@app.get("/userinfo")
async def userinfo(request: Request):
    err = await _gate()
    if err:
        return err
    auth = request.headers.get("authorization", "")
    tok = auth[7:] if auth.lower().startswith("bearer ") else ""
    if tok not in TOKENS or STATE["mode"] == "expired_token":
        return JSONResponse({"error": "invalid_token"}, status_code=401,
                            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})
    return _claims()


if __name__ == "__main__":
    main(app, 18081)
