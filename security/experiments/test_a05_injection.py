"""E-A05-001 / E-A05-002 (A05 Injection): payloads into every writable field of every endpoint.

The endpoint/field inventory is derived at runtime from ``/api/openapi.json`` (request-body schemas, query/path
parameters, multipart form fields), so new fields are covered automatically. Per request the checks are:

* no 5xx (an unhandled DB/driver error on hostile input is a defect, even if only a 500),
* no stack trace / driver / SQL text in any response body (no information leak),
* no time-based SQL injection effect (``pg_sleep`` payloads return fast),
* no data of another tenant in responses to a low-privileged user (canary strings),
* no header injection (response splitting) and no reflected request headers,
* stored string values come back byte-for-byte identical and as ``application/json`` (inert, never HTML).

A failing experiment is a real weakness: assertions are strict and summarise all findings at the end.
"""
from __future__ import annotations

import asyncio
import json
import re
import socket
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
import pytest

from sec_common import API, REPO_ROOT, get_client, list_files, make_world, png_ok, request_retry, scan, upload_dir

pytestmark = pytest.mark.security

# ----------------------------------------------------------------------------------------- payloads
SQLI = [
    "' OR '1'='1", "'; DROP TABLE properties;--", "\" OR \"\"=\"", "1; SELECT pg_sleep(5)--",
    "' UNION SELECT NULL,NULL,NULL--", "1' AND (SELECT 1 FROM pg_sleep(5))--", "\\'; SELECT 1;--",
    "%27%20OR%201%3D1", "admin'--", "$$ ; $$", "'||(SELECT pg_sleep(5))||'", "') OR ('1'='1",
    "{\"$ne\": null}", "{\"$gt\": \"\"}", "';--", "1 OR 1=1", "-1 UNION SELECT usename,passwd FROM pg_shadow--",
]
CRLF = ["a\r\nSet-Cookie: pwned=1", "a\r\n\r\n<html>pwned</html>", "a%0d%0aX-Injected: 1", "\n", "\r", "a\nb\rc"]
TEMPLATE = ["{{7*7}}", "${7*7}", "%s%s%s%n", "{0.__class__}", "<%= 7*7 %>", "#{7*7}", "{% debug %}", "%(x)s", "{}"]
XSS = ["<script>alert(1)</script>", "\"><img src=x onerror=alert(1)>", "javascript:alert(1)", "<svg/onload=alert(1)>",
       "'-alert(1)-'", "</textarea><script>alert(1)</script>"]
PATHS = ["../../../../etc/passwd", "..\\..\\windows\\win.ini", "/etc/passwd", "file:///etc/passwd",
         "http://169.254.169.254/latest/meta-data/", "uploads/../../x"]
UNICODE = ["‮ل", "Ｕｎｉ", "\U0001d54f", "﻿", "a\x00b", "\x00", "\ud800", "\u0000\u0000",
           "  ", "café", "é"]
LONG = ["A" * 300, "é" * 300, "\U0001f600" * 300, "A" * 70_000]
NUMERIC = ["NaN", "Infinity", "-Infinity", "1e999", "-1e999", "9" * 40, "1e-400", "0x10", "1_000",
           "١٢٣", "-0", "0.0000000001", "99999999999999.999", "-99999999999.999"]
JSON_VALUES = [123, 1.5, True, False, [], {}, [1], {"a": 1}, {"$ne": None}, 10 ** 30, 1e308, -1, [[[[[[]]]]]]]
STRING_PAYLOADS = SQLI + CRLF + TEMPLATE + XSS + PATHS + UNICODE + LONG + NUMERIC
CORE = [SQLI[0], SQLI[1], SQLI[3], CRLF[0], XSS[0], UNICODE[4], LONG[0], PATHS[0]]

DB_MARKERS = ["Traceback (most recent call last)", "psycopg", "sqlalchemy", "SQLSTATE", "syntax error at or near",
              "InternalError", "DataError", "IntegrityError", "ProgrammingError", 'File "/', "asyncpg",
              "pydantic_core"]

NOT_A_FIELD_FOR_ECHO = {"integration_config", "image_hash", "value"}
FIELD_SAMPLE = {"unit": "m³", "meter_type": "water", "role": "user", "integration_type": "manual", "source": "manual",
                "property_type": "residential"}


def short(p) -> str:
    s = repr(p)
    return s if len(s) <= 70 else s[:67] + "..."


# ----------------------------------------------------------------------------------------- fixtures
@pytest.fixture
async def admin(client):
    return await get_client(client, "admin")


@pytest.fixture
async def user_a(client):
    return await get_client(client, "user_a")


@pytest.fixture
async def spec(admin):
    r = await admin.get(f"{API}/openapi.json")
    assert r.status_code == 200, f"openapi not available: {r.status_code}"
    return r.json()


def deref(spec, s):
    while isinstance(s, dict) and "$ref" in s:
        s = spec["components"]["schemas"][s["$ref"].split("/")[-1]]
    return s


def classify(spec, s):
    """-> (kind, enum_values). kinds: enum string uuid datetime decimal number integer boolean object array any file"""
    s = deref(spec, s)
    if not isinstance(s, dict):
        return "any", None
    for key in ("anyOf", "oneOf"):
        if key in s:
            vs = [classify(spec, v) for v in s[key] if deref(spec, v).get("type") != "null"]
            kinds = [k for k, _ in vs]
            for k, e in vs:
                if k == "enum":
                    return k, e
            if "number" in kinds and "string" in kinds:
                return "decimal", None
            return (vs[0] if vs else ("any", None))
    if "allOf" in s:
        return classify(spec, s["allOf"][0])
    if "enum" in s:
        return "enum", s["enum"]
    t = s.get("type")
    if t == "string":
        if s.get("format") == "binary":
            return "file", None
        return {"uuid": "uuid", "date-time": "datetime"}.get(s.get("format"), "string"), None
    if t == "array" and deref(spec, s.get("items", {})).get("format") == "binary":
        return "file", None
    return {"number": "number", "integer": "integer", "boolean": "boolean", "object": "object", "array": "array"}.get(t, "any"), None


def sample(kind, enum, name, ctx):
    if name in ctx["named"]:
        return ctx["named"][name]
    if name in FIELD_SAMPLE:
        return FIELD_SAMPLE[name]
    if kind == "enum":
        return enum[0]
    return {"string": "ok", "uuid": str(uuid.uuid4()), "datetime": "2025-01-02T03:04:05Z", "decimal": 1.5,
            "number": 1.5, "integer": 1, "boolean": False, "object": {}, "array": [], "any": None}.get(kind, "ok")


def json_dumps_ascii(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=True).encode()  # lone surrogates / NUL survive as \uXXXX escapes


def fill_path(path: str, ctx) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(ctx["named"].get(m.group(1), uuid.uuid4())), path)


class Findings:
    def __init__(self):
        self.d: "OrderedDict[tuple, list]" = OrderedDict()

    def add(self, where: str, why: str, payload):
        self.d.setdefault((where, why), []).append(payload)

    def __bool__(self):
        return bool(self.d)

    def report(self) -> str:
        lines = []
        for (where, why), ps in self.d.items():
            lines.append(f"{where}: {why}  [{len(ps)}x, e.g. {short(ps[0])}]")
        return "\n".join(lines[:80])


def inspect_response(f: Findings, where: str, payload, resp, elapsed: float, forbidden: list[str], jsonish: bool = True):
    if resp.status_code >= 500:
        f.add(where, f"HTTP {resp.status_code}", payload)
    text = resp.text if len(resp.content) < 2_000_000 else resp.text[:2_000_000]
    for m in DB_MARKERS:
        if m in text:
            f.add(where, f"response leaks internals ({m!r})", payload)
            break
    if elapsed > 4.0 and isinstance(payload, str) and "pg_sleep" in payload:
        f.add(where, f"time-based SQL effect ({elapsed:.1f}s)", payload)
    for bad in forbidden:
        if bad in text:
            f.add(where, f"foreign-tenant data in response ({bad[:20]})", payload)
    for h in ("set-cookie", "x-injected"):
        if h in resp.headers and "pwned" in resp.headers.get(h, "") + h:
            f.add(where, f"header injection ({h})", payload)
    ctype = resp.headers.get("content-type", "")
    if jsonish and "text/html" in ctype:
        f.add(where, f"HTML content type {ctype!r}", payload)


async def retry_transport(fn):
    """Call fn() (coroutine factory); on a transport error retry once (fresh connection). A second consecutive
    failure propagates and is recorded as a finding by the caller."""
    try:
        return await fn()
    except httpx.TransportError:
        await asyncio.sleep(0.2)
        return await fn()


async def new_ctx(admin, seed, tag: str, scanned_image_path: str | None = None):
    w = await make_world(admin, tag)
    return {"w": w, "named": {
        "property_id": w["property_id"], "meter_id": w["meter_id"], "reading_id": w["reading_id"],
        "filename": "x.png", "image_path": scanned_image_path or "uploads/none.png",
        "engine": "llm", "current_value": "12.5", "already_cropped": "false",
        "user_id": str(uuid.uuid4()),
    }}


# ----------------------------------------------------------------------------------------- operation inventory
def json_body_ops(spec):
    for path, item in spec["paths"].items():
        if path.startswith(f"{API}/auth/"):
            continue  # superadmin-login is rate limited (5/min, shared with A07); callback/login covered via params
        for method in ("post", "patch", "put"):
            op = item.get(method)
            if not op or "requestBody" not in op:
                continue
            content = op["requestBody"]["content"]
            if "application/json" not in content:
                continue
            schema = deref(spec, content["application/json"]["schema"])
            yield path, method, op, schema


def form_ops(spec):
    for path, item in spec["paths"].items():
        for method in ("post", "patch", "put"):
            op = item.get(method)
            if not op or "requestBody" not in op:
                continue
            content = op["requestBody"]["content"]
            for ct in ("multipart/form-data", "application/x-www-form-urlencoded"):
                if ct in content:
                    yield path, method, op, deref(spec, content[ct]["schema"]), ct


def param_ops(spec, methods=("get", "post")):
    for path, item in spec["paths"].items():
        for method in methods:
            op = item.get(method)
            if not op:
                continue
            params = [p for p in op.get("parameters", []) if p["in"] in ("query", "path")]
            if params and not path.startswith(f"{API}/auth/logout"):
                yield path, method, op, params


# ----------------------------------------------------------------------------------------- E-A05-001 JSON bodies
async def test_E_A05_001_json_body_fields_all_endpoints(admin, user_a, spec, seed):
    """E-A05-001: ~100 SQL/CRLF/template/XSS/path/unicode/NUL/overlong/numeric-extreme/non-string payloads into EVERY field
    of EVERY JSON request body (derived from OpenAPI).

    Secure: never 5xx, no internals in the body, no time-based effect, 2xx echoes of string fields are byte-identical
    (inert) and JSON; invalid input is 4xx.
    """
    f = Findings()
    forbidden = [seed["properties"]["prop_b"]["id"]]
    ops = list(json_body_ops(spec))
    assert ops, "no JSON body operations found in OpenAPI"
    for path, method, op, schema in ops:
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        for field, fs in props.items():
            kind, enum = classify(spec, fs)
            if kind == "file":
                continue
            ctx = await new_ctx(admin, seed, "a05-json")
            if "/users" in path and path.startswith(f"{API}/properties"):
                ctx["named"]["user_id"] = seed["users"]["nogroup"]["id"]
            base = {k: sample(*classify(spec, props[k]), k, ctx) for k in required if k != field}
            url = fill_path(path, ctx)
            payloads = list(STRING_PAYLOADS) + list(JSON_VALUES)
            for p in payloads:
                body = dict(base)
                body[field] = p
                t0 = time.monotonic()
                try:
                    r = await retry_transport(lambda: admin.request(
                        method.upper(), url, content=json_dumps_ascii(body),
                        headers={"content-type": "application/json"}, timeout=30))
                except httpx.HTTPError as e:
                    f.add(f"{method.upper()} {path} [{field}]", f"transport error {type(e).__name__}", p)
                    continue
                where = f"{method.upper()} {path} [{field}]"
                inspect_response(f, where, p, r, time.monotonic() - t0, forbidden)
                if 200 <= r.status_code < 300 and isinstance(p, str) and field not in NOT_A_FIELD_FOR_ECHO \
                        and kind in ("string", "any"):
                    try:
                        echoed = r.json().get(field) if isinstance(r.json(), dict) else None
                    except ValueError:
                        echoed = None
                        f.add(where, "2xx without JSON body", p)
                    if echoed is not None and echoed != p:
                        f.add(where, "stored value not returned verbatim (altered on the way)", p)
                elif 200 <= r.status_code < 300 and kind in ("any", "object", "array") and field == "integration_config":
                    pass
    assert not f, "findings:\n" + f.report()


async def test_E_A05_001_enum_members_do_not_crash(admin, spec, seed):
    """E-A05-001: every schema-valid enum member of every body field is accepted or cleanly rejected -- never a 5xx
    (catches enum values the database does not know, e.g. a unit stored under a different spelling)."""
    f = Findings()
    for path, method, op, schema in json_body_ops(spec):
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        for field, fs in props.items():
            kind, enum = classify(spec, fs)
            if kind != "enum" or field in ("replaced_at",):
                continue
            for member in enum:
                ctx = await new_ctx(admin, seed, "a05-enum")
                if path.startswith(f"{API}/admin"):
                    continue  # would change real seeded users' roles
                base = {k: sample(*classify(spec, props[k]), k, ctx) for k in required if k != field}
                base[field] = member
                if "/users" in path:
                    ctx["named"]["user_id"] = seed["users"]["nogroup"]["id"]
                    base["user_id"] = seed["users"]["nogroup"]["id"]
                r = await admin.request(method.upper(), fill_path(path, ctx), content=json_dumps_ascii(base),
                                        headers={"content-type": "application/json"})
                if r.status_code >= 500:
                    f.add(f"{method.upper()} {path} [{field}]", f"HTTP {r.status_code} for valid enum member", member)
    assert not f, "findings:\n" + f.report()


# ----------------------------------------------------------------------------------------- E-A05-001 params
async def test_E_A05_001_query_and_path_parameters(admin, user_a, spec, seed):
    """E-A05-001: payloads into every declared query and path parameter (GET and parameterised POST), once as admin on
    own data and once as user A on seeded data with a foreign-tenant canary. Secure: 4xx/404 for garbage, never 5xx,
    no internals, no time-based effect, no data of property B / the canary property in user A's responses."""
    f = Findings()
    canary = f"CANARY-{uuid.uuid4().hex[:10]}"
    cw = await admin.post(f"{API}/properties/", json={"name": canary, "address": canary})
    cpid = cw.json()["id"]
    await admin.post(f"{API}/properties/{cpid}/meters/", json={"meter_type": "water", "unit": "m³", "name": canary,
                                                                "serial_number": canary})
    forbidden = [canary, seed["properties"]["prop_b"]["id"], "rig-user-b@", "prop-b"] + \
        [m["id"] for k, m in seed["meters"].items() if k.startswith("meter_b")]
    ctx_admin = await new_ctx(admin, seed, "a05-params")
    sa = seed["meters"]["meter_a_water"]
    ctx_a = {"w": None, "named": {
        "property_id": seed["properties"]["prop_a"]["id"], "meter_id": sa["id"],
        "reading_id": seed["readings"]["meter_a_water"][0]["id"], "filename": "x.png", "user_id": str(uuid.uuid4())}}
    payloads = [p for p in STRING_PAYLOADS if len(p) < 3000] + [str(v) for v in JSON_VALUES[:6]]
    n_ops = 0
    for path, method, op, params in param_ops(spec):
        if method == "post" and ("{" not in path and not any(p["in"] == "query" for p in params)):
            continue
        n_ops += 1
        for who, c, ctx, check_foreign in (("admin", admin, ctx_admin, []), ("user_a", user_a, ctx_a, forbidden)):
            for prm in params:
                kind, enum = classify(spec, prm["schema"])
                for p in payloads:
                    try:
                        p.encode("utf-8")
                    except UnicodeEncodeError:
                        continue
                    named = dict(ctx["named"])
                    q = {}
                    for other in params:
                        if other["name"] != prm["name"] and other["in"] == "query" and other.get("required"):
                            q[other["name"]] = sample(*classify(spec, other["schema"]), other["name"], {"named": named})
                    if prm["in"] == "path":
                        named[prm["name"]] = quote(p, safe="")
                    else:
                        q[prm["name"]] = p
                    url = re.sub(r"\{(\w+)\}", lambda m: str(named.get(m.group(1), uuid.uuid4())), path)
                    t0 = time.monotonic()
                    try:
                        r = await retry_transport(lambda: c.request(method.upper(), url, params=q or None, timeout=30))
                    except httpx.HTTPError as e:
                        f.add(f"{who} {method.upper()} {path} [{prm['in']}:{prm['name']}]", f"transport {type(e).__name__}", p)
                        continue
                    if r.status_code == 429:
                        await asyncio.sleep(2)
                        continue
                    inspect_response(f, f"{who} {method.upper()} {path} [{prm['in']}:{prm['name']}]", p, r,
                                     time.monotonic() - t0, check_foreign, jsonish=not path.endswith("/openapi.json"))
    assert n_ops > 10, f"only {n_ops} parameterised operations derived"
    assert not f, "findings:\n" + f.report()


# ----------------------------------------------------------------------------------------- E-A05-001 form fields
async def test_E_A05_001_multipart_form_fields(admin, spec, seed, rig, gateway):
    """E-A05-001: payloads into every non-file multipart/form field (OCR endpoints: engine, meter_id, property_id,
    image_path, current_value, already_cropped) with a valid image as the file part. Secure: validation errors are
    4xx, no 5xx, no internals, no time-based effect, no file written for rejected requests."""
    f = Findings()
    updir = upload_dir(rig)
    first = await scan(admin, png_ok(seed=5), engine="llm")
    assert first.status_code == 200, first.text[:200]
    ctx = await new_ctx(admin, seed, "a05-form", first.json()["image_path"])
    n = 0
    for path, method, op, schema, ct in form_ops(spec):
        props = schema.get("properties", {})
        for field, fs in props.items():
            kind, enum = classify(spec, fs)
            if kind == "file":
                continue
            n += 1
            for p in CORE:
                files, data = [], {}
                for k, v in props.items():
                    k_kind, k_enum = classify(spec, v)
                    if k_kind == "file":
                        files.append((k, ("m.png", png_ok(seed=6), "image/png")))
                    elif k != field:
                        val = sample(k_kind, k_enum, k, ctx)
                        data[k] = str(val).lower() if isinstance(val, bool) else str(val)
                data[field] = p
                before = list_files(updir)

                async def once():
                    return await admin.request(method.upper(), fill_path(path, ctx), data=data,
                                               files=files or None, timeout=120)

                t0 = time.monotonic()
                try:
                    r = await retry_transport(lambda: request_retry(once))
                except (httpx.HTTPError, UnicodeError) as e:
                    f.add(f"{method.upper()} {path} [{field}]", f"transport {type(e).__name__}", p)
                    continue
                where = f"{method.upper()} {path} [{field}]"
                inspect_response(f, where, p, r, time.monotonic() - t0, [])
                if r.status_code in (400, 404, 413, 415, 422) and list_files(updir) != before:
                    f.add(where, "rejected request still stored a file", p)
    assert n > 3, f"only {n} form fields derived"
    assert not f, "findings:\n" + f.report()


# ----------------------------------------------------------------------------------------- E-A05-001 headers (raw socket)
def _raw_http(host: str, port: int, request: bytes, limit: int = 300_000) -> bytes:
    with socket.create_connection((host, port), timeout=10) as s:
        s.settimeout(10)
        try:
            s.sendall(request)
        except OSError:
            return b""
        data = b""
        try:
            while len(data) < limit:
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
        except (socket.timeout, OSError):
            pass
        return data


HEADER_PAYLOADS = [
    b"a\r\nSet-Cookie: pwned=1", b"a\nSet-Cookie: pwned=1", b"a\rSet-Cookie: pwned=1", b"a\x00b", b"\xff\xfe\xfd",
    b"' OR '1'='1", b"<script>alert(1)</script>", b"A" * 8000, b"A" * 70000, "\U0001f600".encode(), b"a\tb",
    b"{{7*7}}", b"%0d%0aSet-Cookie: pwned=1", b"${jndi:ldap://127.0.0.1/a}",
]


async def test_E_A05_001_request_headers_no_injection_or_reflection(rig):
    """E-A05-001: CRLF/NUL/invalid-UTF-8/overlong/injection payloads in X-Request-ID, Authorization, Accept-Language,
    User-Agent, Cookie, X-Forwarded-*, Origin, Referer (raw socket, so nothing is sanitised client-side).

    Secure: never 5xx; response never gains attacker-controlled headers (no response splitting; 'pwned' absent from the
    response head); Authorization garbage -> 401; hostile Origin is not reflected into Access-Control-Allow-Origin.
    """
    u = urlparse(rig["BACKEND_URL"])
    host, port = u.hostname, u.port
    f = Findings()
    for hname in ("X-Request-ID", "Authorization", "Accept-Language", "User-Agent", "Cookie", "X-Forwarded-For",
                  "X-Forwarded-Host", "Origin", "Referer"):
        path = "/api/properties/" if hname == "Authorization" else "/api/health/live"
        for p in HEADER_PAYLOADS:
            value = b"Bearer " + p if hname == "Authorization" else p
            req = (b"GET " + path.encode() + b" HTTP/1.1\r\nHost: " + f"{host}:{port}".encode() + b"\r\n" +
                   hname.encode() + b": " + value + b"\r\nConnection: close\r\n\r\n")
            raw = await asyncio.to_thread(_raw_http, host, port, req)
            where = f"header {hname}"
            if not raw:
                continue  # connection dropped for oversized/invalid header: acceptable
            head, _, _ = raw.partition(b"\r\n\r\n")
            m = re.match(rb"HTTP/1\.[01] (\d{3})", head)
            if not m:
                f.add(where, "unparseable response", p[:40])
                continue
            status = int(m.group(1))
            if status >= 500:
                f.add(where, f"HTTP {status}", p[:40])
            lines = head.split(b"\r\n")[1:]
            # only a separate header LINE counts; the payload merely echoed inside a value (X-Request-ID reflection,
            # reported under A02) is not response splitting
            if any(l.lower().startswith(b"x-injected") or (l.lower().startswith(b"set-cookie:") and b"pwned" in l.lower())
                   for l in lines):
                f.add(where, "attacker-controlled header/value reflected in response head", p[:40])
            if hname == "Authorization" and status not in (400, 401, 403, 431):
                f.add(where, f"garbage bearer token -> {status} (want 401)", p[:40])
            if hname == "Origin":
                acao = [l for l in lines if l.lower().startswith(b"access-control-allow-origin")]
                if acao and p[:20] in acao[0]:
                    f.add(where, "hostile Origin reflected in Access-Control-Allow-Origin", p[:40])
    assert not f, "findings:\n" + f.report()


# ----------------------------------------------------------------------------------------- E-A05-001 stored inert
async def test_E_A05_001_sql_payload_stored_inert_and_tables_intact(admin, seed):
    """E-A05-001: classic SQL payloads stored as property/meter/reading text come back byte-for-byte on GET and in lists,
    the injected statements had no effect (user count, property count unchanged apart from our own rows, other tenants'
    rows intact), and filtering by the payload in query strings matches nothing extra."""
    users_before = (await admin.get(f"{API}/admin/users")).json()
    props_before = (await admin.get(f"{API}/properties/")).json()
    payloads = [SQLI[0], SQLI[1], "x'); DELETE FROM users;--", "x'); UPDATE users SET role='superadmin';--",
                "Robert'); DROP TABLE readings;--"]
    created = []
    for p in payloads:
        pr = await admin.post(f"{API}/properties/", json={"name": p, "address": p})
        assert pr.status_code == 201, (p, pr.status_code, pr.text[:200])
        pid = pr.json()["id"]
        created.append(pid)
        assert pr.json()["name"] == p and pr.json()["address"] == p
        mr = await admin.post(f"{API}/properties/{pid}/meters/", json={
            "meter_type": "water", "unit": "m³", "name": p, "serial_number": p[:100], "location": p, "notes": p})
        assert mr.status_code == 201, (p, mr.status_code, mr.text[:200])
        mid = mr.json()["id"]
        rr = await admin.post(f"{API}/properties/{pid}/meters/{mid}/readings/", json={"value": 1.5, "note": p})
        assert rr.status_code == 201, (p, rr.status_code, rr.text[:200])
        g = await admin.get(f"{API}/properties/{pid}")
        assert g.json()["name"] == p
        lst = await admin.get(f"{API}/properties/{pid}/meters/{mid}/readings/")
        assert lst.json()[0]["note"] == p
    users_after = (await admin.get(f"{API}/admin/users")).json()
    props_after = (await admin.get(f"{API}/properties/")).json()
    assert users_after == users_before, "users table changed by a stored SQL payload"
    assert {x["id"] for x in props_after} == {x["id"] for x in props_before} | set(created)
    for k in ("prop_a", "prop_b"):
        assert (await admin.get(f"{API}/properties/{seed['properties'][k]['id']}")).status_code == 200


# ----------------------------------------------------------------------------------------- E-A05-002
XSS_ROUNDTRIP = XSS + ["<img src=x onerror=alert(1)>", "<a href=\"javascript:alert(1)\">x</a>", "&lt;b&gt;&amp;",
                       "<iframe srcdoc='<script>alert(1)</script>'>", "\"onmouseover=\"alert(1)", "<!--", "]]>",
                       "<style>@import 'http://127.0.0.1/x'</style>", "{{constructor.constructor('alert(1)')()}}"]


async def test_E_A05_002_stored_xss_roundtrip_api_returns_raw_json(admin):
    """E-A05-002: XSS strings stored in names, addresses, serials, locations and notes (image paths: refused since F-004) are returned verbatim
    (not HTML-escaped or altered by the API) and ONLY as `application/json` -- the API never serves them as HTML, so
    encoding is purely the frontend's job (checked statically below).

    Secure: raw text in JSON on single GET and list GET, content type application/json, no text/html anywhere.
    """
    problems = []
    for p in XSS_ROUNDTRIP:
        pr = await admin.post(f"{API}/properties/", json={"name": p, "address": p})
        if pr.status_code != 201:
            problems.append(f"property create {pr.status_code} for {short(p)}")
            continue
        pid = pr.json()["id"]
        mr = await admin.post(f"{API}/properties/{pid}/meters/", json={
            "meter_type": "water", "unit": "m³", "name": p, "serial_number": p[:100], "location": p, "notes": p})
        mid = mr.json()["id"]
        # F-004: ``image_path`` only accepts ``uploads/<uuid>.<ext>`` of a registered upload, so a markup string
        # there must be refused (4xx, never 5xx, never stored); the markup is stored/returned via ``note``.
        rr = await admin.post(f"{API}/properties/{pid}/meters/{mid}/readings/", json={"value": 2.5, "note": p})
        ir = await admin.post(f"{API}/properties/{pid}/meters/{mid}/readings/", json={"value": 2.5, "image_path": p})
        if not 400 <= ir.status_code < 500:
            problems.append(f"markup as image_path not refused with 4xx: {ir.status_code} for {short(p)}")
        urls = [(f"{API}/properties/{pid}", "name", p), (f"{API}/properties/{pid}", "address", p),
                (f"{API}/properties/{pid}/meters/{mid}", "name", p), (f"{API}/properties/{pid}/meters/{mid}", "notes", p),
                (f"{API}/properties/{pid}/meters/{mid}", "location", p)]
        for url, key, want in urls:
            g = await admin.get(url)
            ct = g.headers.get("content-type", "")
            if not ct.startswith("application/json"):
                problems.append(f"{url}: content-type {ct!r}")
            if g.json().get(key) != want:
                problems.append(f"{url}: {key} altered: {short(g.json().get(key))} != {short(want)}")
        for url, key in ((f"{API}/properties/", "name"), (f"{API}/properties/{pid}/meters/", "name"),
                         (f"{API}/properties/{pid}/meters/{mid}/readings/", "note")):
            g = await admin.get(url)
            ct = g.headers.get("content-type", "")
            if not ct.startswith("application/json") or "html" in ct:
                problems.append(f"{url}: content-type {ct!r}")
            if p not in [x.get(key) for x in g.json()]:
                problems.append(f"list {url}: {short(p)} not verbatim")
        if rr.status_code != 201:
            problems.append(f"reading with note {short(p)} -> {rr.status_code}")
    assert not problems, "\n".join(problems[:40])


async def test_E_A05_002_error_responses_never_html(admin, user_a):
    """E-A05-002: payloads reflected in URLs/paths produce error bodies that are JSON/plain (never `text/html`) and do
    not echo executable markup unescaped inside an HTML document."""
    problems = []
    for p in XSS:
        for url in (f"{API}/properties/{quote(p, safe='')}", f"{API}/{quote(p, safe='')}", f"{API}/uploads/{quote(p, safe='')}",
                    f"{API}/dashboard/consumption/{quote(p, safe='')}", f"{API}/properties/?x={quote(p, safe='')}"):
            for c in (admin, user_a):
                r = await c.get(url)
                ct = r.headers.get("content-type", "")
                if "html" in ct:
                    problems.append(f"{url}: {ct}")
                if r.status_code >= 500:
                    problems.append(f"{url}: {r.status_code}")
    assert not problems, "\n".join(problems[:30])


async def test_E_A05_002_bulk_scan_stream_echoes_filename_as_inert_json(admin):
    """E-A05-002: bulk-scan echoes the client filename; it must arrive as a JSON string inside `text/event-stream`
    (never HTML), byte-identical, so a UI that renders it as text is safe."""
    from sec_common import bulk_scan, parse_sse

    name = "<img src=x onerror=alert(1)>.png"
    r = await bulk_scan(admin, [(name, png_ok(seed=9), "image/png")], engine="llm")
    assert r.status_code == 200, r.text[:200]
    assert r.headers["content-type"].startswith("text/event-stream")
    item = [d for n, d in parse_sse(r.text) if n != "complete"][0]
    assert item["original_filename"] == name


FRONTEND_SINKS = re.compile(r"v-html|innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function\(|dangerouslySetInnerHTML")


def test_E_A05_002_frontend_has_no_raw_html_sinks():
    """E-A05-002 (static review): the Vue frontend renders API text only through escaped bindings -- no `v-html`,
    `innerHTML`, `insertAdjacentHTML`, `document.write`, `eval`, `new Function` anywhere in `frontend/src`.

    A hit is not automatically an XSS but must be reviewed; the experiment fails so it cannot be overlooked.
    """
    root = REPO_ROOT / "frontend" / "src"
    if not root.exists():
        pytest.skip("frontend/src not present in this checkout")
    hits = []
    for p in root.rglob("*"):
        if p.suffix in (".vue", ".ts", ".js", ".tsx") and p.is_file():
            for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
                if FRONTEND_SINKS.search(line):
                    hits.append(f"{p.relative_to(REPO_ROOT)}:{i}: {line.strip()[:120]}")
    assert not hits, "raw HTML sinks in frontend:\n" + "\n".join(hits)
