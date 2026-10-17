"""A02 Security misconfiguration (E-A02-001, E-A02-002).

E-A02-001 starts the application's ``Settings`` in a throw-away subprocess (clean environment, empty
working directory so no ``.env`` is picked up) with insecure configuration and expects a refusal.
E-A02-002 inspects what the running rig exposes: headers, documentation endpoints, error bodies, CORS.

Fixtures: ``rig`` (parsed rig.env). See security/rig/CONTRACT.md.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO / "backend"

DEFAULT_DB = "postgresql://zaehlwart:zaehlwart@localhost:5432/zaehlwart"
GOOD_DB = "postgresql://rig:s3cr3t-rig-pw@127.0.0.1:5432/rig"
GOOD_JWT = "k8Jx2pQ9vL0aZt4YhR7uWmN3sB6eDfGc1VqXoTiPyH5"  # 43 random-looking chars


# --------------------------------------------------------------------------- E-A02-001 helpers
def _start_settings(tmp_path, env: dict) -> subprocess.CompletedProcess:
    """Instantiate app.config.Settings in a fresh interpreter. Returns the finished process."""
    full_env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "PYTHONPATH": str(BACKEND_DIR),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    full_env.update(env)
    return subprocess.run(  # nosec B603 - fixed argv, test-only
        [sys.executable, "-c", "from app.config import Settings; Settings(); print('STARTED')"],
        cwd=tmp_path, env=full_env, capture_output=True, text=True, timeout=60,
    )


def _refused(p: subprocess.CompletedProcess) -> bool:
    return p.returncode != 0 and "STARTED" not in p.stdout


GOOD_ENV = {"DATABASE_URL": GOOD_DB, "JWT_SECRET_KEY": GOOD_JWT, "OCR_BACKEND": "ollama"}


@pytest.mark.security
def test_E_A02_001_control_secure_configuration_starts(tmp_path):
    """E-A02-001 (control): a strong JWT secret and a non-default DB URL start fine.

    Guards the refusal tests below against a broken harness (missing deps, wrong PYTHONPATH).
    """
    p = _start_settings(tmp_path, GOOD_ENV)
    assert p.returncode == 0 and "STARTED" in p.stdout, p.stderr[-800:]


@pytest.mark.security
def test_E_A02_001_default_jwt_secret_refuses_start(tmp_path):
    """E-A02-001 default JWT secret `change-me-in-production` (also: unset, i.e. the code default).

    Expected (secure): process refuses to start, message names JWT_SECRET_KEY.
    """
    for env in (
        {"DATABASE_URL": GOOD_DB, "JWT_SECRET_KEY": "change-me-in-production"},
        {"DATABASE_URL": GOOD_DB},
    ):
        p = _start_settings(tmp_path, env)
        assert _refused(p), f"started with default JWT secret {env}"
        assert "JWT_SECRET_KEY" in p.stderr


@pytest.mark.security
def test_E_A02_001_default_database_url_refuses_start(tmp_path):
    """E-A02-001 default DB URL (`zaehlwart:zaehlwart@localhost`, explicit and implicit).

    Expected (secure): refuses to start, message names DATABASE_URL.
    """
    for env in (
        {"DATABASE_URL": DEFAULT_DB, "JWT_SECRET_KEY": GOOD_JWT},
        {"JWT_SECRET_KEY": GOOD_JWT},
    ):
        p = _start_settings(tmp_path, env)
        assert _refused(p), f"started with default database URL {env}"
        assert "DATABASE_URL" in p.stderr


@pytest.mark.security
def test_E_A02_001_both_defaults_refuse_start(tmp_path):
    """E-A02-001 nothing configured at all. Expected (secure): refuses to start."""
    assert _refused(_start_settings(tmp_path, {}))


@pytest.mark.security
@pytest.mark.parametrize(
    "secret", ["", " ", "changeme", "change-me", "secret", "password", "admin", "jwt", "x", "a" * 16, "123456789012345"]
)
def test_E_A02_001_weak_jwt_secret_refuses_start(tmp_path, secret):
    """E-A02-001 weak JWT secret: only the exact sentinel is rejected by the code today.

    Expected (secure): empty / short (< 32 chars) / obvious secrets are refused as well; an HS256
    key shorter than the hash size is brute-forceable offline from any captured token.
    (Suspected weakness: `_validate_security_settings` compares against one literal only.)
    """
    p = _start_settings(tmp_path, {"DATABASE_URL": GOOD_DB, "JWT_SECRET_KEY": secret})
    assert _refused(p), f"started with weak JWT secret {secret!r}"


@pytest.mark.security
@pytest.mark.parametrize("password", ["admin", "admin123", "password", "changeme", "123456", "superadmin", "x"])
def test_E_A02_001_weak_superadmin_password_refuses_start(tmp_path, password):
    """E-A02-001 weak/default superadmin password (env-only local account that bypasses OIDC).

    Expected (secure): a configured superadmin password that is short or a well-known default is
    refused at start-up. (Suspected weakness: no validation of SUPERADMIN_PASSWORD at all.)
    """
    env = dict(GOOD_ENV, SUPERADMIN_USER="superadmin", SUPERADMIN_PASSWORD=password)
    assert _refused(_start_settings(tmp_path, env)), f"started with superadmin password {password!r}"


@pytest.mark.security
def test_E_A02_001_superadmin_user_without_password_or_vice_versa(tmp_path):
    """E-A02-001 half-configured superadmin (password but no user name).

    Expected (secure): refused (or the local account disabled). With an empty user name the login
    form accepts an empty username plus the password, i.e. the account is silently half-configured.
    """
    env = dict(GOOD_ENV, SUPERADMIN_PASSWORD="Zq8!vN3#rT6@wK1$pL5^")
    assert _refused(_start_settings(tmp_path, env)), "started with SUPERADMIN_PASSWORD but empty SUPERADMIN_USER"


@pytest.mark.security
def test_E_A02_001_gateway_without_key_or_short_timeout_refuses_start(tmp_path):
    """E-A02-001 gateway backend misconfigured (no API key / timeout < 300 s / no URL).

    Expected (secure): refuses to start (fail closed instead of sending images unauthenticated).
    """
    base = dict(GOOD_ENV, OCR_BACKEND="gateway", GATEWAY_BASE_URL="http://127.0.0.1:1/v1", GATEWAY_API_KEY="k")
    assert _start_settings(tmp_path, base).returncode == 0, "control for gateway settings failed"
    for override in ({"GATEWAY_API_KEY": ""}, {"GATEWAY_BASE_URL": ""}, {"GATEWAY_TIMEOUT_S": "10"}, {"OCR_BACKEND": "bogus"}):
        p = _start_settings(tmp_path, dict(base, **override))
        assert _refused(p), f"started with {override}"


# --------------------------------------------------------------------------- E-A02-002
@pytest.fixture
async def anon(rig):
    async with httpx.AsyncClient(base_url=rig["BACKEND_URL"], timeout=30, follow_redirects=False) as c:
        yield c


LEAK_PATTERNS = re.compile(
    r"Traceback|File \"|site-packages|sqlalchemy|psycopg|asyncpg|uvicorn|starlette|/usr/lib|/home/|/app/|"
    r"\bSELECT\b|\bINSERT\b|postgres(ql)?://|password|secret|authlib|pydantic_core|\.py\b",
    re.I,
)


DOC_PATHS = ["/docs", "/redoc", "/openapi.json", "/api/docs", "/api/redoc", "/api/openapi.json", "/api/docs/oauth2-redirect"]


@pytest.mark.security
async def test_E_A02_002_api_documentation_not_public(rig, restart_backend):
    """E-A02-002 `/docs`, `/redoc`, `/openapi.json` (also under `/api`) on an internet-facing instance.

    Expected (secure, feature 010 / F-031): with the DEFAULT configuration (``ENABLE_API_DOCS`` unset) none of
    them is reachable (404). The rig itself runs with ENABLE_API_DOCS=true (experiments enumerate the OpenAPI), so
    this experiment restarts the backend with the variable unset and restores the rig afterwards. A control check
    proves the opt-in still works (``ENABLE_API_DOCS=true`` -> /api/openapi.json 200) so the 404s are not a harness defect."""
    from idp_helpers import Restarted, new_http, wait_healthy

    async with Restarted(restart_backend, {"ENABLE_API_DOCS": None}):
        async with new_http(rig) as http:
            assert await wait_healthy(http, 60)
            seen = {path: (await http.get(path)).status_code for path in DOC_PATHS}
    assert all(code in (401, 403, 404) for code in seen.values()), f"documentation reachable by default: {seen}"
    async with new_http(rig) as http:  # rig default again (ENABLE_API_DOCS=true): control
        assert await wait_healthy(http, 60)
        assert (await http.get("/api/openapi.json")).status_code == 200, "ENABLE_API_DOCS=true no longer exposes the docs (control)"


def _repo_text(globs: list[str]) -> str:
    out = []
    for g in globs:
        for f in REPO.glob(g):
            if f.is_file() and f.stat().st_size < 1_000_000 and not f.relative_to(REPO).parts[0] == "security":
                try:
                    out.append(f.read_text(errors="ignore"))
                except OSError:
                    pass
    return "\n".join(out)


HEADER_REQUIREMENTS = {
    "x-content-type-options": (lambda v: v.lower() == "nosniff", r"nosniff|contentTypeNosniff|X-Content-Type-Options"),
    "referrer-policy": (lambda v: bool(v), r"Referrer-Policy|referrerPolicy"),
    "strict-transport-security": (lambda v: "max-age" in v.lower(), r"Strict-Transport-Security|stsSeconds|hsts"),
    "x-frame-options|content-security-policy": (
        lambda v: bool(v), r"X-Frame-Options|frame-ancestors|frameDeny|customFrameOptionsValue"
    ),
}


@pytest.mark.security
async def test_E_A02_002_security_headers_set_by_app_or_documented_ingress(anon):
    """E-A02-002 security response headers.

    Expected (secure): each of nosniff, Referrer-Policy, HSTS and a framing restriction is either
    sent by the application itself or demonstrably configured on the ingress in this repository
    (Traefik middleware / chart / docs). Anything in neither place is a gap.
    """
    r = await anon.get("/api/health/live")
    deploy_text = _repo_text(["charts/**/*", "chart/**/*", "deploy/**/*", "helm/**/*", "k8s/**/*", "docs/**/*",
                              "docker-compose*.y*ml", "nginx*/**/*", "frontend/nginx*", "frontend/*.conf"])
    missing = []
    for names, (ok, doc_regex) in HEADER_REQUIREMENTS.items():
        values = [r.headers.get(n, "") for n in names.split("|")]
        if any(v and ok(v) for v in values):
            continue
        if re.search(doc_regex, deploy_text, re.I):
            continue
        missing.append(names)
    assert not missing, f"not sent by the app and not configured in repo ingress/docs: {missing}"


@pytest.mark.security
async def test_E_A02_002_no_server_version_banner(anon):
    """E-A02-002 `Server` / `X-Powered-By` must not reveal product versions. Expected (secure): no version digits."""
    r = await anon.get("/api/health/live")
    for h in ("server", "x-powered-by", "x-aspnet-version"):
        assert not re.search(r"\d+\.\d+", r.headers.get(h, "")), f"{h}: {r.headers.get(h)}"


@pytest.mark.security
async def test_E_A02_002_health_endpoint_minimal_to_anonymous(anon):
    """E-A02-002 unauthenticated `/api/health`.

    Expected (secure, feature 010 / F-031): liveness style answer only -- exactly ``{"status": ...}``. Version, LLM
    model name and which backends (DB, OCR, LLM) are up are reconnaissance data and need a login."""
    r = await anon.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"status"}, f"anonymous /api/health exposes {sorted(set(body) - {'status'})}"


@pytest.mark.security
async def test_E_A02_002_health_details_need_login_and_hide_version_from_non_admins(anon, client):
    """E-A02-002 `/api/health/details` (feature 010): 401 anonymously; for a logged-in non-admin without
    ``version`` / ``llm_model``; admins get them."""
    assert (await anon.get("/api/health/details")).status_code == 401
    user = await client("user").get("/api/health/details")
    assert user.status_code == 200
    leaked = {k for k in ("version", "llm_model") if k in user.json()}
    assert not leaked, f"non-admin sees {sorted(leaked)}"
    admin = await client("admin").get("/api/health/details")
    assert admin.status_code == 200 and "db" in admin.json()


async def _provoke(anon, rig) -> list[tuple[str, httpx.Response]]:
    """Requests designed to trigger the framework's error paths without needing credentials or data."""
    rs: list[tuple[str, httpx.Response]] = []
    rnd = "00000000-0000-0000-0000-0000000000aa"
    rs.append(("404 unknown path", await anon.get("/api/does-not-exist")))
    rs.append(("404 root", await anon.get("/")))
    rs.append(("405 wrong method", await anon.put("/api/health")))
    rs.append(("401 no token", await anon.get("/api/properties/")))
    rs.append(("401 bad token", await anon.get("/api/properties/", headers={"Authorization": "Bearer a.b.c"})))
    rs.append(("422 bad uuid", await anon.get(f"/api/properties/not-a-uuid/meters/")))
    rs.append(("login bad json", await anon.post("/api/auth/superadmin-login", content=b"{", headers={"content-type": "application/json"})))
    rs.append(("callback no params", await anon.get("/api/auth/callback")))
    rs.append(("callback bad state", await anon.get("/api/auth/callback", params={"code": "x", "state": "y" * 5000})))
    rs.append(("upload no auth", await anon.get("/api/uploads/..%2f..%2fetc%2fpasswd")))
    rs.append(("huge header", await anon.get("/api/health/live", headers={"X-Junk": "A" * 20000})))
    rs.append(("bad encoding", await anon.get("/api/properties/%ff%fe")))
    rs.append(("null byte", await anon.get(f"/api/uploads/a%00b")))
    return rs


@pytest.mark.security
async def test_E_A02_002_error_bodies_contain_no_internals(anon, rig):
    """E-A02-002 error bodies (404/405/401/422/400 paths).

    Expected (secure): no stack traces, file paths, library names, SQL or connection strings in any error
    response body.
    """
    bad = []
    for label, r in await _provoke(anon, rig):
        if r.status_code < 400:
            continue
        m = LEAK_PATTERNS.search(r.text)
        # words like "password" occur legitimately in a validation message about *our* field name; ignore only that
        if m and m.group(0).lower() == "password" and r.status_code == 422:
            continue
        if m:
            bad.append((label, r.status_code, m.group(0), r.text[:160]))
    assert not bad, bad


@pytest.mark.security
async def test_E_A02_002_anonymous_provocations_never_cause_5xx(anon, rig):
    """E-A02-002 malformed anonymous input must not cause a server error. Expected (secure): all < 500."""
    bad = [(l, r.status_code) for l, r in await _provoke(anon, rig) if r.status_code >= 500]
    assert not bad, bad


@pytest.mark.security
async def test_E_A02_002_authenticated_error_bodies_contain_no_internals(rig, client):
    """E-A02-002 error bodies on authenticated paths (validation errors, numeric overflow, bad JSON).

    Expected (secure): no stack traces / SQL / driver names; unexpected failures answer with a generic
    body. (A 500 here is additionally a robustness finding for A05/A10.)
    """
    import inspect

    c = client("admin")
    if inspect.isawaitable(c):
        c = await c
    props = (await c.get("/api/properties/")).json()
    pid = props[0]["id"]
    meters = (await c.get(f"/api/properties/{pid}/meters/")).json()
    mid = meters[0]["id"]
    rpath = f"/api/properties/{pid}/meters/{mid}/readings/"
    probes = [
        ("overflow value", lambda: c.post(rpath, json={"value": "1e100000"})),
        ("nan value", lambda: c.post(rpath, json={"value": "NaN"})),
        ("huge value", lambda: c.post(rpath, json={"value": "9" * 400})),
        ("bad read_at", lambda: c.post(rpath, json={"value": "1", "read_at": "not-a-date"})),
        ("bad json", lambda: c.post(rpath, content=b"{", headers={"content-type": "application/json"})),
        ("long name", lambda: c.post(f"/api/properties/{pid}/meters/", json={"meter_type": "water", "unit": "m³", "name": "n" * 100000})),
        ("long serial", lambda: c.patch(f"/api/properties/{pid}/meters/{mid}", json={"serial_number": "s" * 100000})),
        ("nul in name", lambda: c.patch(f"/api/properties/{pid}", json={"name": "a\x00b"})),
        ("huge limit", lambda: c.get(rpath, params={"limit": 10**12})),
        ("reading id overflow", lambda: c.delete(f"{rpath}99999999999999999999")),
    ]
    bad = []
    for label, make in probes:
        try:
            try:
                r = await make()
            except httpx.TransportError:  # may be a stale keep-alive socket after an earlier 500: retry once
                r = await make()
        except httpx.TransportError as exc:  # server closed the connection instead of answering
            bad.append((label, "no HTTP answer", type(exc).__name__))
            continue
        if r.status_code >= 500:
            bad.append((label, "5xx", r.status_code))
        m = LEAK_PATTERNS.search(r.text) if r.status_code >= 400 else None
        if m and m.group(0).lower() not in ("password",):
            bad.append((label, r.status_code, m.group(0), r.text[:160]))
    assert not bad, bad


@pytest.mark.security
async def test_E_A02_002_cors_foreign_origin_not_allowed(anon):
    """E-A02-002 CORS: preflight and simple requests from foreign origins (incl. `null`, look-alike hosts).

    Expected (secure): no `Access-Control-Allow-Origin` echo for any origin other than the configured
    app origin, never `*` together with credentials.
    """
    evil = ["https://evil.example", "null", "http://localhost:5173.evil.example", "https://zaehl-o-mat.example.com.evil.example"]
    for origin in evil:
        pre = await anon.options(
            "/api/properties/",
            headers={"Origin": origin, "Access-Control-Request-Method": "DELETE", "Access-Control-Request-Headers": "authorization"},
        )
        assert pre.headers.get("access-control-allow-origin") not in (origin, "*"), f"preflight echoes {origin}"
        get = await anon.get("/api/health/live", headers={"Origin": origin})
        assert get.headers.get("access-control-allow-origin") not in (origin, "*"), f"GET echoes {origin}"
        assert get.headers.get("access-control-allow-credentials", "").lower() != "true" or \
            get.headers.get("access-control-allow-origin") not in (origin, "*")


@pytest.mark.security
async def test_E_A02_002_cors_does_not_trust_dev_origin_in_production(anon, rig):
    """E-A02-002 CORS: the Vite dev origin `http://localhost:5173` is only trusted when DEBUG is set.

    Expected (secure): without DEBUG not allowed. up.sh starts the rig with DEBUG=true unless
    SEC_RIG_APP_DEBUG=false, in which case the test is skipped (documented in main.py: dev-only).
    """
    if os.environ.get("SEC_RIG_APP_DEBUG", "true").lower() != "false":
        pytest.skip("rig backend runs with DEBUG=true (up.sh default); start it with SEC_RIG_APP_DEBUG=false to run this")
    r = await anon.options(
        "/api/properties/",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert r.headers.get("access-control-allow-origin") != "http://localhost:5173", \
        "dev origin trusted (backend probably running with DEBUG=true; check the rig environment)"


@pytest.mark.security
async def test_E_A02_002_request_id_header_not_reflected_unbounded(anon):
    """E-A02-002 `X-Request-ID` is copied from the request into the response (and likely into logs).

    Expected (secure): a client-supplied id that is not a short token (here 8 KB of arbitrary
    characters) is replaced or truncated, otherwise it enables log forging / response bloat.
    """
    big = "A" * 8000
    r = await anon.get("/api/health/live", headers={"X-Request-ID": big})
    echoed = r.headers.get("x-request-id", "")
    assert len(echoed) <= 128 and echoed != big, f"echoed {len(echoed)} chars of client-controlled id"
    # (a tab or other control character cannot be sent: the HTTP client refuses it before it hits the wire)
    for odd in ("abc def;<script>", 'a"b', "x" * 65, "id\u00e4", "a/b", ""):
        try:
            r = await anon.get("/api/health/live", headers={"X-Request-ID": odd})
        except (httpx.LocalProtocolError, UnicodeEncodeError):
            continue
        echoed = r.headers.get("x-request-id")
        assert echoed != odd, f"request id {odd[:20]!r} echoed verbatim"
        assert echoed and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", echoed), f"replacement id not a safe token: {echoed!r}"
    ok = "client-id.1_ok"
    r = await anon.get("/api/health/live", headers={"X-Request-ID": ok})
    assert r.headers.get("x-request-id") == ok, "a valid client id is no longer propagated (correlation)"


# --------------------------------------------------------------------------- E-A02-004 (feature 012, trailing-slash redirect)

@pytest.mark.security
async def test_E_A02_004_redirect_keeps_https_behind_a_trusted_proxy(rig, restart_backend):
    """E-A02-004: `GET /api/properties` (no trailing slash) is answered with a redirect to `/api/properties/`.
    uvicorn runs with --no-proxy-headers (F-002), so behind a TLS terminating proxy the Location used to be `http://`
    (mixed content / blocked by CSP). Expected: with a TRUSTED peer (127.0.0.0/8, which the rig client is) and
    `X-Forwarded-Proto: https` the Location starts with `https://`; with an UNTRUSTED peer (rig default
    TRUSTED_PROXIES=10.255.255.0/24) the header is ignored (`http://`) so a client cannot choose the scheme; and in
    the trusted case garbage / downgrade values (`javascript`, `ftp`, `https, http` = last hop wins) never yield
    anything but `http://`/`https://` and the last hop decides."""
    from idp_helpers import Restarted, new_http, wait_healthy

    async def loc(http, xfp):
        h = {"X-Forwarded-Proto": xfp} if xfp is not None else {}
        r = await http.get("/api/properties", headers=h)
        assert r.status_code in (301, 302, 307, 308), f"no slash redirect: HTTP {r.status_code}"
        return r.headers["location"]

    async with new_http(rig) as http:  # untrusted peer
        assert (await loc(http, "https")).startswith("http://"), "X-Forwarded-Proto honoured from an UNTRUSTED peer"
        assert (await loc(http, None)).startswith("http://")
    async with Restarted(restart_backend, {"TRUSTED_PROXIES": "127.0.0.0/8"}):
        async with new_http(rig) as http:
            assert await wait_healthy(http, 60)
            got = await loc(http, "https")
            assert got.startswith("https://") and got.endswith("/api/properties/"), got
            assert (await loc(http, None)).startswith("http://"), "no header: scheme must stay http"
            assert (await loc(http, "http")).startswith("http://")
            assert (await loc(http, "javascript")).startswith("http://"), "unknown scheme must be ignored"
            assert (await loc(http, "ftp")).startswith("http://")
            assert (await loc(http, "https, http")).startswith("http://"), "last (closest) hop must win"
            assert (await loc(http, "http, https")).startswith("https://")
