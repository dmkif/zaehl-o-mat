"""A01 Broken access control (E-A01-001 .. E-A01-006).

Rig facts used (see security/rig/CONTRACT.md and rig/seed.py):

* principals (client roles): superadmin, admin (global role admin), manager (global role manager,
  property role *manager* on prop-a), user_a (property role user on prop-a), user_b (property role
  user on prop-b), nogroup (global role user, no property membership)
* properties prop_a / prop_b with two meters each (``meter_<a|b>_<water|electricity>``), three
  readings per meter with images in UPLOAD_DIR.

Experiments never trust the app's own answers about state: before/after comparisons are read with
the admin account.
"""
import ast
import asyncio
import inspect
import json
import re
import shutil
import socket
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

REPO = Path(__file__).resolve().parents[2]
BACKEND_APP = REPO / "backend" / "app"
RND = "00000000-0000-4000-8000-0000000000ee"  # well-formed UUID that exists nowhere

ROLE_KEYS = ["superadmin", "admin", "manager", "user_a", "user_b", "nogroup"]
DENIED = (403, 404)  # secure outcomes for "you may not touch this" (404 hides existence)


# --------------------------------------------------------------------------- fixtures / helpers
@pytest.fixture
async def actors(rig, client):
    """One AsyncClient per principal, bound to BACKEND_URL with that principal's bearer token."""
    made: dict[str, httpx.AsyncClient] = {}
    for key in ROLE_KEYS:
        c = client(key)
        if inspect.isawaitable(c):
            c = await c
        auth = c.headers.get("authorization")
        if auth:
            made[key] = httpx.AsyncClient(
                base_url=rig["BACKEND_URL"], headers={"Authorization": auth}, timeout=120, follow_redirects=False,
                transport=httpx.AsyncHTTPTransport(retries=2, limits=httpx.Limits(max_keepalive_connections=0)),
            )
        else:  # fixture client authenticates in some other way: use it as is
            made[key] = c
    yield made
    for c in made.values():
        await c.aclose()


@pytest.fixture
async def anon(rig):
    async with httpx.AsyncClient(base_url=rig["BACKEND_URL"], timeout=30, follow_redirects=False) as c:
        yield c


async def send(c: httpx.AsyncClient, method: str, path: str, **kw) -> httpx.Response:
    """Request that waits out the app's per-IP rate limit (429) instead of reporting it as a result."""
    for _ in range(8):
        r = await c.request(method, path, **kw)
        if r.status_code != 429:
            return r
        await asyncio.sleep(10)
    return r


def upload_dir(rig) -> Path:
    p = Path(rig["UPLOAD_DIR"])
    return p if p.is_absolute() else (REPO / p)


def png_bytes(label: str = "x") -> bytes:
    import io

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (120, 60), (240, 240, 230))
    ImageDraw.Draw(img).text((5, 20), f"RIG {label} {uuid.uuid4().hex[:6]}", fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


async def snapshot(admin: httpx.AsyncClient, seed) -> dict:
    """Everything an attacker could alter, as seen by the admin: properties, members, meters, readings."""
    snap: dict = {}
    snap["properties"] = (await admin.get("/api/properties/")).json()
    snap["users"] = (await admin.get("/api/admin/users")).json()
    for pkey, p in seed["properties"].items():
        pid = p["id"]
        snap[f"members:{pkey}"] = (await admin.get(f"/api/properties/{pid}/users")).json()
        meters = (await admin.get(f"/api/properties/{pid}/meters/")).json()
        snap[f"meters:{pkey}"] = meters
        for m in meters:
            snap[f"readings:{m['id']}"] = (
                await admin.get(f"/api/properties/{pid}/meters/{m['id']}/readings/", params={"limit": 10000})
            ).json()
    return snap


def sort_json(x):
    return json.dumps(x, sort_keys=True, default=str)


def prop_id(seed, key):
    return seed["properties"][key]["id"]


def meters_of(seed, pkey):
    return {k: v for k, v in seed["meters"].items() if v["property"] == pkey}


# =========================================================================== E-A01-001
PUBLIC = {  # (method, path) that are public by design; everything else needs a token
    ("GET", "/api/health"),
    ("GET", "/api/health/live"),
    ("GET", "/api/auth/login"),
    ("GET", "/api/auth/callback"),
    ("POST", "/api/auth/superadmin-login"),
    ("POST", "/api/auth/logout"),
}
# probing these would burn the login rate limit / start a login flow; they are exercised by A06/A07
SKIP_PROBE = {("GET", "/api/auth/login"), ("GET", "/api/auth/callback"), ("POST", "/api/auth/superadmin-login")}


def _fetch_openapi(rig) -> dict:
    last = None
    for path in ("/api/openapi.json", "/openapi.json"):
        r = httpx.get(rig["BACKEND_URL"] + path, timeout=30)
        last = r
        if r.status_code == 200:
            return r.json()
    pytest.fail(f"cannot enumerate routes: OpenAPI not reachable (last {last.status_code}); "
                "if docs were switched off in production the rig must expose them for the review")


def _concrete(path: str, params: list[dict]) -> str:
    out = path
    for p in params:
        if p.get("in") != "path":
            continue
        sch = p.get("schema", {})
        val = "1" if sch.get("type") == "integer" else (RND if sch.get("format") == "uuid" else "x")
        out = out.replace("{" + p["name"] + "}", val)
    return out


def enumerate_operations(spec: dict) -> list[tuple[str, str, str, bool]]:
    ops = []
    for path, item in spec["paths"].items():
        shared = item.get("parameters", [])
        for method, op in item.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            body = op.get("requestBody", {}).get("content", {})
            is_json = "application/json" in body
            ops.append((method.upper(), path, _concrete(path, shared + op.get("parameters", [])), is_json))
    return ops


@pytest.mark.security
async def test_E_A01_001_every_endpoint_requires_authentication(rig, anon):
    """E-A01-001 every route of the OpenAPI document, no token (and again with a garbage token).

    Expected (secure): 401 for every route that is not on the explicit public allow-list; the
    public list must also be exactly the routes that really answer without credentials (a new
    anonymous route fails the experiment). Login/callback are not probed (rate limit / IdP flow).
    """
    ops = enumerate_operations(_fetch_openapi(rig))
    assert len(ops) >= 30, f"only {len(ops)} operations enumerated, OpenAPI incomplete?"

    found_public = {(m, p) for m, p, _, _ in ops} & PUBLIC
    unexpected_missing = PUBLIC - {(m, p) for m, p, _, _ in ops}
    assert not unexpected_missing, f"allow-listed routes not in OpenAPI: {sorted(unexpected_missing)}"

    failures = []
    for method, tmpl, path, is_json in ops:
        if (method, tmpl) in PUBLIC:
            continue
        for headers in ({}, {"Authorization": "Bearer garbage.token.value"}):
            kw = {"json": {}} if is_json else {}
            r = await send(anon, method, path, headers=headers, **kw)
            if r.status_code != 401:
                failures.append((method, tmpl, "garbage" if headers else "none", r.status_code))
    assert not failures, f"routes answering something other than 401 without valid credentials: {failures}"

    # public routes that are safe to call: must not require/accept anything dangerous
    for m, p in sorted(found_public - SKIP_PROBE):
        r = await anon.request(m, p)
        assert r.status_code < 500, (m, p, r.status_code)


# =========================================================================== E-A01-002
ATTACKS = [  # (attacker, victim property key, attacker's own property key or None)
    ("user_b", "prop_a", "prop_b"),
    ("user_a", "prop_b", "prop_a"),
    ("manager", "prop_b", "prop_a"),
    ("nogroup", "prop_a", None),
    ("nogroup", "prop_b", None),
]


def _victim_requests(seed, attacker_id: str, vkey: str, own_pkey: str | None, png: bytes):
    """(label, method, path, kwargs) that touch the victim's data directly or through a mixed path."""
    pid = prop_id(seed, vkey)
    vm = meters_of(seed, vkey)
    mkey, m = next(iter(vm.items()))
    mid = m["id"]
    reading = seed["readings"][mkey][0]
    rid = reading["id"]
    victim_member = seed["users"][seed["property_access"][vkey][0]]["id"]
    base = f"/api/properties/{pid}"
    reqs = [
        ("get property", "GET", base, {}),
        ("patch property", "PATCH", base, {"json": {"name": "pwned-by-" + attacker_id[:6]}}),
        ("delete property", "DELETE", base, {}),
        ("list members", "GET", f"{base}/users", {}),
        ("add self as admin", "POST", f"{base}/users", {"json": {"user_id": attacker_id, "role": "admin"}}),
        ("remove member", "DELETE", f"{base}/users/{victim_member}", {}),
        ("list meters", "GET", f"{base}/meters/", {}),
        ("create meter", "POST", f"{base}/meters/", {"json": {"meter_type": "water", "unit": "m³", "name": "pwn"}}),
        ("get meter", "GET", f"{base}/meters/{mid}", {}),
        ("patch meter", "PATCH", f"{base}/meters/{mid}", {"json": {"name": "pwned", "serial_number": "PWNED"}}),
        ("delete meter", "DELETE", f"{base}/meters/{mid}", {}),
        ("list readings", "GET", f"{base}/meters/{mid}/readings/", {}),
        ("create reading", "POST", f"{base}/meters/{mid}/readings/", {"json": {"value": "999999"}}),
        ("patch reading", "PATCH", f"{base}/meters/{mid}/readings/{rid}", {"json": {"value": "1", "note": "pwned"}}),
        ("delete reading", "DELETE", f"{base}/meters/{mid}/readings/{rid}", {}),
        ("dashboard consumption", "GET", f"/api/dashboard/consumption/{mid}", {}),
        ("dashboard cost forecast", "GET", f"/api/dashboard/cost-forecast/{mid}", {}),
        ("dashboard type aggregates", "GET", f"/api/dashboard/property/{pid}/type-aggregates", {}),
        ("ocr rescan", "POST", f"/api/ocr/rescan/{rid}", {}),
        ("ocr scan for victim meter", "POST", "/api/ocr/scan",
         {"files": {"file": ("a.png", png, "image/png")}, "data": {"meter_id": mid}}),
        ("ocr hint-rescan victim meter", "POST", "/api/ocr/hint-rescan",
         {"data": {"image_path": reading["image_path"], "meter_id": mid, "current_value": "x"}}),
        ("ocr bulk-scan victim property", "POST", "/api/ocr/bulk-scan",
         {"files": {"files": ("a.png", png, "image/png")}, "data": {"property_id": pid}}),
        ("download image", "GET", f"/api/uploads/{reading['filename']}", {}),
    ]
    if own_pkey:
        opid = prop_id(seed, own_pkey)
        reqs += [  # mixed paths: attacker's own property id, victim's meter/reading ids
            ("mixed: get victim meter via own property", "GET", f"/api/properties/{opid}/meters/{mid}", {}),
            ("mixed: victim readings via own property", "GET", f"/api/properties/{opid}/meters/{mid}/readings/", {}),
            ("mixed: patch victim reading via own property", "PATCH", f"/api/properties/{opid}/meters/{mid}/readings/{rid}",
             {"json": {"value": "1"}}),
            ("mixed: delete victim reading via own property", "DELETE", f"/api/properties/{opid}/meters/{mid}/readings/{rid}", {}),
        ]
        own_meter = next(iter(meters_of(seed, own_pkey).values()))["id"]
        reqs += [  # attacker's own meter, victim's image (use own authorisation to reach victim data)
            ("hint-rescan own meter, victim image", "POST", "/api/ocr/hint-rescan",
             {"data": {"image_path": reading["image_path"], "meter_id": own_meter, "current_value": "x"}}),
        ]
    return reqs


async def _gateway_request_count(rig) -> int:
    base = rig["GATEWAY_URL"].rstrip("/")
    base = base[:-3] if base.endswith("/v1") else base
    async with httpx.AsyncClient(timeout=10) as c:
        return len((await c.get(base + "/_control/requests")).json())


@pytest.mark.security
@pytest.mark.parametrize("attacker,vkey,own", ATTACKS, ids=[f"{a}-vs-{v}" for a, v, _ in ATTACKS])
async def test_E_A01_002_cross_tenant_access_by_id_denied(rig, seed, actors, attacker, vkey, own):
    """E-A01-002 IDOR: ``attacker`` operates on the other property's objects by id, directly and through
    mixed paths (own property id + foreign meter/reading id), including OCR, dashboard and file routes.

    Expected (secure): every request answers 403/404, no victim data in any body, no call to the
    AI gateway on the victim's behalf, and the admin's before/after snapshot is identical.
    """
    admin = actors["admin"]
    me = actors[attacker]
    my_id = seed["users"][attacker]["id"]
    before = sort_json(await snapshot(admin, seed))
    gw_before = await _gateway_request_count(rig)

    victim_markers = [seed["properties"][vkey]["name"]] + [
        v["serial_number"] for v in meters_of(seed, vkey).values()
    ]
    problems = []
    for label, method, path, kw in _victim_requests(seed, my_id, vkey, own, png_bytes(attacker)):
        r = await send(me, method, path, **kw)
        if r.status_code not in DENIED:
            problems.append((label, r.status_code, r.text[:120]))
            continue
        if any(mk in r.text for mk in victim_markers):
            problems.append((label, "victim data in error body", r.text[:120]))

    assert not problems, problems
    assert sort_json(await snapshot(admin, seed)) == before, "state changed by a request that was refused"
    assert await _gateway_request_count(rig) == gw_before, "AI gateway was called for a refused request"


@pytest.mark.security
async def test_E_A01_002_listings_contain_only_own_objects(seed, actors):
    """E-A01-002 list endpoints must be filtered per caller.

    Expected (secure): property list, dashboard summary and bulk meter lists of a member of one
    property never include the other property's ids, names or meters; a user without membership
    sees nothing.
    """
    for who, own, other in (("user_b", "prop_b", "prop_a"), ("user_a", "prop_a", "prop_b"), ("nogroup", None, "prop_a")):
        r = await actors[who].get("/api/properties/")
        assert r.status_code == 200
        ids = {p["id"] for p in r.json()}
        assert prop_id(seed, other) not in ids, f"{who} sees {other}"
        if own is None:
            assert not ids, f"{who} without membership sees {ids}"
        s = await actors[who].get("/api/dashboard/summary")
        assert s.status_code == 200
        txt = s.text
        assert prop_id(seed, other) not in txt
        for m in meters_of(seed, other).values():
            assert m["id"] not in txt and m["serial_number"] not in txt, f"{who} dashboard leaks {other} meter"


@pytest.mark.security
async def test_E_A01_002_streaming_bulk_scan_only_leaks_own_meters(rig, seed, actors):
    """E-A01-002 streaming endpoint (SSE ``/api/ocr/bulk-scan``) without property filter.

    The stream (per-image events and the final ``complete`` event with the "accessible meter list") is
    read to the end. Expected (secure): it contains none of the other property's meter ids, serial
    numbers, reading ids or values, even when the uploaded image is byte-identical to a victim's
    stored image (duplicate detection must not cross tenants).
    """
    victim_img = upload_dir(rig) / seed["readings"]["meter_a_water"][0]["filename"]
    payload = victim_img.read_bytes()
    victim_markers = [m["id"] for m in meters_of(seed, "prop_a").values()] + \
                     [m["serial_number"] for m in meters_of(seed, "prop_a").values()]
    for _ in range(8):
        async with actors["user_b"].stream(
            "POST", "/api/ocr/bulk-scan",
            files=[("files", ("dup.png", payload, "image/png")), ("files", ("new.png", png_bytes("b"), "image/png"))],
        ) as r:
            status = r.status_code
            body = (await r.aread()).decode(errors="replace")
        if status != 429:
            break
        await asyncio.sleep(10)
    assert status == 200, body[:200]
    for mk in victim_markers:
        assert mk not in body, f"bulk-scan stream leaks {mk}"
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ") and line[6:].startswith("{")]
    for ev in events:
        assert not ev.get("duplicate_reading"), f"cross-tenant duplicate disclosed: {ev.get('duplicate_reading')}"


@pytest.mark.security
async def test_E_A01_002_image_filename_guessing_and_reference_attack(rig, seed, actors):
    """E-A01-002 file serving by filename: reference attack on the ownership check.

    ``/api/uploads/<name>`` decides by looking up *a* reading that references the file. A caller with
    write access to his own property can create a reading whose ``image_path`` names someone else's
    image. Expected (secure): the foreign image is still refused to him (403/404) and removing his
    reading does not delete the original file. (Suspected weakness: ownership is resolved with an
    unordered ``.first()`` over all referencing readings, and ``delete_reading`` unlinks
    ``Path(image_path).name`` without checking who owns the file.)
    """
    ud = upload_dir(rig)
    victim_name = f"victim-{uuid.uuid4()}.png"
    (ud / victim_name).write_bytes(png_bytes("victim"))
    admin = actors["admin"]
    pb = prop_id(seed, "prop_b")
    mb = next(iter(meters_of(seed, "prop_b").values()))["id"]
    pa = prop_id(seed, "prop_a")
    ma = next(iter(meters_of(seed, "prop_a").values()))["id"]
    created = []
    try:
        # victim: reading in prop-b referencing the file (only prop-b members and admins may read it)
        r = await admin.post(f"/api/properties/{pb}/meters/{mb}/readings/",
                             json={"value": "424242", "image_path": f"uploads/{victim_name}"})
        assert r.status_code == 201, r.text
        created.append((pb, mb, r.json()["id"]))
        assert (await actors["user_b"].get(f"/api/uploads/{victim_name}")).status_code == 200, "control: owner must read"
        assert (await actors["manager"].get(f"/api/uploads/{victim_name}")).status_code in DENIED, \
            "control: unrelated tenant reads the file before any attack"

        # attacker: manager (write on prop-a) references the victim's file from his own meter
        r = await actors["manager"].post(f"/api/properties/{pa}/meters/{ma}/readings/",
                                         json={"value": "1", "image_path": f"uploads/{victim_name}"})
        if r.status_code == 201:
            created.append((pa, ma, r.json()["id"]))
        else:
            assert r.status_code in (400, 403, 404, 409, 422), f"unexpected {r.status_code} {r.text[:150]}"
            return  # reference refused outright: secure

        got = await actors["manager"].get(f"/api/uploads/{victim_name}")
        assert got.status_code in DENIED, "attacker can read a foreign image by referencing it from his own reading"

        # removing the attacker's reading must not destroy the victim's file
        pa_r, ma_r, rid = created[-1]
        assert (await admin.delete(f"/api/properties/{pa_r}/meters/{ma_r}/readings/{rid}")).status_code == 204
        created.pop()
        assert (ud / victim_name).exists(), "deleting a reading that merely references a foreign file removed the file"
    finally:
        for p, m, rid in created:
            await admin.delete(f"/api/properties/{p}/meters/{m}/readings/{rid}")
        (ud / victim_name).unlink(missing_ok=True)


@pytest.mark.security
async def test_E_A01_002_unattached_scan_upload_not_readable_by_other_user(rig, seed, actors):
    """E-A01-002 fresh scan images (not yet attached to a reading).

    ``/api/ocr/scan`` stores the photo under ``uploads/<uuid>.<ext>`` before any reading exists; the
    file endpoint then has no tenant to check against and serves it to every authenticated user.
    Expected (secure): another user's request for it answers 403/404. (Suspected weakness, accepted as
    "unguessable UUID" in the code comment -- the experiment measures what that means in practice.)
    """
    r = await send(actors["user_a"], "POST", "/api/ocr/scan", files={"file": ("a.png", png_bytes("orphan"), "image/png")})
    if r.status_code != 200:
        pytest.skip(f"scan not usable in this rig state: {r.status_code} {r.text[:100]}")
    name = r.json()["image_path"].split("/", 1)[1]
    try:
        assert (await actors["user_a"].get(f"/api/uploads/{name}")).status_code == 200, "control: uploader reads own scan"
        other = await actors["user_b"].get(f"/api/uploads/{name}")
        assert other.status_code in DENIED, f"user_b reads user_a's unattached scan ({other.status_code})"
    finally:
        (upload_dir(rig) / name).unlink(missing_ok=True)


@pytest.mark.security
async def test_E_A01_002_hint_rescan_does_not_process_foreign_images(rig, seed, actors):
    """E-A01-002 OCR oracle: ``/api/ocr/hint-rescan`` takes an arbitrary stored file name plus the caller's OWN meter.

    Expected (secure): a file that belongs to another property's reading is refused (403/404) and is not
    forwarded to the AI gateway (otherwise the recognised value of a foreign photo is disclosed).
    """
    own_meter = next(iter(meters_of(seed, "prop_b").values()))["id"]
    foreign = seed["readings"]["meter_a_water"][0]["image_path"]
    before = await _gateway_request_count(rig)
    r = await send(
        actors["user_b"], "POST", "/api/ocr/hint-rescan", data={"image_path": foreign, "meter_id": own_meter, "current_value": "not-a-number"}
    )
    assert r.status_code in DENIED, f"{r.status_code} {r.text[:150]}"
    assert await _gateway_request_count(rig) == before, "foreign image was sent to the AI gateway"


# =========================================================================== E-A01-003
GLOBAL_ADMIN = {"superadmin", "admin"}
# property-level read/write per principal on each property (from seed.py): None = no membership
PROP_ROLE = {
    ("prop_a", "user_a"): "user", ("prop_a", "manager"): "manager", ("prop_b", "user_b"): "user",
}


def _allowed(who: str, pkey: str, level: str) -> bool:
    if who in GLOBAL_ADMIN:
        return True
    role = PROP_ROLE.get((pkey, who))
    return {"read": role in ("user", "manager", "admin"),
            "write": role in ("manager", "admin"),
            "delete": role == "admin"}[level]


@pytest.mark.security
@pytest.mark.parametrize("who", ROLE_KEYS)
async def test_E_A01_003_role_matrix_admin_endpoints(seed, actors, who):
    """E-A01-003 global roles on admin-level routes.

    Matrix: ``GET /admin/users`` and ``DELETE /admin/users/<unknown id>``, ``PATCH
    /admin/users/<id>/role``, ``POST /properties/``, ``DELETE /properties/<unknown id>`` -- permitted only for
    global admin and superadmin (others 403). For permitted roles the probe ends in 404 (non-existing
    target) so nothing is changed.
    """
    c = actors[who]
    ok = who in GLOBAL_ADMIN
    r = await c.get("/api/admin/users")
    assert (r.status_code == 200) if ok else (r.status_code == 403), (who, "GET /admin/users", r.status_code)
    r = await c.delete(f"/api/admin/users/{RND}")
    assert (r.status_code == 404) if ok else (r.status_code == 403), (who, "DELETE user", r.status_code)
    r = await c.patch(f"/api/admin/users/{RND}/role", json={"role": "user"})
    assert (r.status_code == 404) if ok else (r.status_code == 403), (who, "PATCH role", r.status_code)
    r = await c.delete(f"/api/properties/{RND}")
    assert (r.status_code == 404) if ok else (r.status_code == 403), (who, "DELETE property", r.status_code)
    r = await c.post("/api/properties/", json={"name": f"rig-matrix-{who}"})
    if ok:
        assert r.status_code == 201, (who, r.status_code, r.text)
        assert (await actors["admin"].delete(f"/api/properties/{r.json()['id']}")).status_code == 204
    else:
        assert r.status_code == 403, (who, "POST /properties", r.status_code)


@pytest.mark.security
@pytest.mark.parametrize("who", ROLE_KEYS)
@pytest.mark.parametrize("pkey", ["prop_a", "prop_b"])
async def test_E_A01_003_role_matrix_property_levels(seed, actors, who, pkey):
    """E-A01-003 property-level roles: read / write / delete per principal and property.

    Read: ``GET /properties/<id>`` (200/403). Write: a no-op ``PATCH`` of the property and ``PATCH``
    / ``POST`` on an unknown meter or reading (403 when not permitted, 404 when permitted). Delete:
    ``DELETE`` of an unknown meter / reading (needs property *admin* or global admin). Expected
    (secure): exactly the permissions of the model in permissions.py (user = read, manager = read+write,
    property admin / global admin = everything; no membership = nothing).
    """
    c = actors[who]
    pid = prop_id(seed, pkey)
    mid = next(iter(meters_of(seed, pkey).values()))["id"]
    base = f"/api/properties/{pid}"

    r = await c.get(base)
    assert (r.status_code == 200) == _allowed(who, pkey, "read"), (who, pkey, "read", r.status_code)
    r = await c.get(f"{base}/meters/{mid}/readings/")
    assert (r.status_code == 200) == _allowed(who, pkey, "read"), (who, pkey, "list readings", r.status_code)

    write_ok = _allowed(who, pkey, "write")
    r = await c.patch(base, json={})
    assert (r.status_code == 200) == write_ok, (who, pkey, "patch property", r.status_code)
    r = await c.patch(f"{base}/meters/{RND}", json={"name": "x"})
    assert r.status_code == (404 if write_ok else 403), (who, pkey, "patch unknown meter", r.status_code)
    r = await c.patch(f"{base}/meters/{mid}/readings/999999999", json={"note": "x"})
    assert r.status_code == (404 if write_ok else 403), (who, pkey, "patch unknown reading", r.status_code)

    del_ok = _allowed(who, pkey, "delete")
    r = await c.delete(f"{base}/meters/{RND}")
    assert r.status_code == (404 if del_ok else 403), (who, pkey, "delete unknown meter", r.status_code)
    r = await c.delete(f"{base}/meters/{mid}/readings/999999999")
    assert r.status_code == (404 if del_ok else 403), (who, pkey, "delete unknown reading", r.status_code)


@pytest.mark.security
async def test_E_A01_003_property_manager_cannot_grant_or_escalate_roles(seed, actors):
    """E-A01-003 vertical escalation inside a property.

    ``manager`` has property role *manager* (write, no delete) on prop-a. Expected (secure): he
    cannot (a) raise his own property role to admin, (b) grant property role admin to someone else,
    (c) change his own global role or another user's via the admin API. The pre-state is restored
    by the admin whatever happens.
    """
    admin, mgr = actors["admin"], actors["manager"]
    pa = prop_id(seed, "prop_a")
    mgr_id = seed["users"]["manager"]["id"]
    ua_id = seed["users"]["user_a"]["id"]

    async def members():
        return {m["user_id"]: m["role"] for m in (await admin.get(f"/api/properties/{pa}/users")).json()}

    before = await members()
    try:
        r = await mgr.post(f"/api/properties/{pa}/users", json={"user_id": mgr_id, "role": "admin"})
        assert r.status_code in DENIED, f"manager promoted himself to property admin ({r.status_code})"
        r = await mgr.post(f"/api/properties/{pa}/users", json={"user_id": ua_id, "role": "admin"})
        assert r.status_code in DENIED, f"manager granted property admin to another user ({r.status_code})"
        r = await mgr.delete(f"/api/properties/{pa}/users/{ua_id}")
        assert r.status_code in DENIED, f"manager removed a member ({r.status_code})"
        r = await mgr.patch(f"/api/admin/users/{mgr_id}/role", json={"role": "admin"})
        assert r.status_code == 403
        assert await members() == before
    finally:
        for uid, role in before.items():
            await admin.post(f"/api/properties/{pa}/users", json={"user_id": uid, "role": role})
        for uid in set(await members()) - set(before):
            await admin.delete(f"/api/properties/{pa}/users/{uid}")


@pytest.mark.security
async def test_E_A01_003_admin_cannot_touch_superadmin(rig, seed, actors):
    """E-A01-003 global admin vs. superadmin.

    Expected (secure): a global admin can neither demote nor delete the superadmin, and cannot grant
    superadmin. (Suspected weakness: ``set_user_role`` only blocks *granting* superadmin; it does not
    protect an existing superadmin from being demoted. The superadmin is restored by the experiment.)
    """
    admin, sup = actors["admin"], actors["superadmin"]
    sid = seed["users"]["superadmin"]["id"]
    aid = seed["users"]["admin"]["id"]
    try:
        r = await admin.patch(f"/api/admin/users/{aid}/role", json={"role": "superadmin"})
        assert r.status_code == 403, f"admin escalated himself to superadmin ({r.status_code})"
        r = await admin.delete(f"/api/admin/users/{sid}")
        assert r.status_code == 403, r.status_code
        r = await admin.patch(f"/api/admin/users/{sid}/role", json={"role": "user"})
        assert r.status_code in DENIED, f"admin demoted the superadmin ({r.status_code})"
    finally:
        # restore by SQL: if the app let the admin demote the superadmin, the superadmin API client is a plain user now
        from sec_common import restore_seed_roles

        restore_seed_roles(rig, seed)
    users = {u["id"]: u for u in (await sup.get("/api/admin/users")).json()}
    assert users[sid]["role"] == "superadmin" and users[aid]["role"] == "admin"


# =========================================================================== E-A01-004
def raw_get(rig, path: str, token: str | None, timeout=15) -> tuple[int, bytes]:
    """HTTP GET with the request target sent byte-for-byte (no client-side normalisation of ``..``/%2e)."""
    u = urlsplit(rig["BACKEND_URL"])
    s = socket.create_connection((u.hostname, u.port), timeout=timeout)
    try:
        req = f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nConnection: close\r\nAccept: */*\r\n"
        if token:
            req += f"Authorization: {token}\r\n"
        s.sendall((req + "\r\n").encode("latin-1"))
        data = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    finally:
        s.close()
    head, _, body = data.partition(b"\r\n\r\n")
    try:
        status = int(head.split(b" ", 2)[1])
    except (IndexError, ValueError):
        status = 0
    return status, body


TRAVERSAL = [
    "../{canary}", "..%2f{canary}", "%2e%2e/{canary}", "%2e%2e%2f{canary}", "..%5c{canary}", "..\\{canary}",
    "%252e%252e%252f{canary}", "....//{canary}", "..;/{canary}", "..%00/{canary}", "..%c0%af{canary}",
    "..%ef%bc%8f{canary}", "./../{canary}", "x/../../{canary}", "%2e%2e/%2e%2e/{canary}",
    "..//{canary}", "/../{canary}", "%2fetc%2fpasswd", "/etc/passwd", "..%2f..%2f..%2f..%2fetc%2fpasswd",
    "../../../../../../etc/passwd", "....//....//....//etc/passwd", "%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    "file:///etc/passwd", "{canary}", "..", ".", "%2e%2e",
]


@pytest.mark.security
async def test_E_A01_004_upload_path_traversal_variants_refused(rig, seed, actors):
    """E-A01-004 path traversal / absolute path / encoded variants on ``GET /api/uploads/<name>``.

    A canary file with unique content is placed next to (outside) the upload directory and, as a second
    target, `/etc/passwd` is probed. The request target is sent raw so client-side normalisation does not
    hide a variant. Expected (secure): 400/404 for every variant (a plain 200 for ``{canary}`` inside
    UPLOAD_DIR is the only legitimate hit, used as control), never the canary / passwd content, never 5xx.
    """
    ud = upload_dir(rig)
    secret = f"CANARY-{uuid.uuid4()}"
    outside = ud.parent / f"canary-{uuid.uuid4().hex[:8]}.txt"
    inside = ud / f"inside-{uuid.uuid4().hex[:8]}.txt"
    sibling_dir = ud.parent / (ud.name + "-evil")  # name that merely starts with the upload dir name
    sibling_dir.mkdir(exist_ok=True)
    sibling = sibling_dir / "s.txt"
    outside.write_text(secret)
    inside.write_text("inside-ok")
    sibling.write_text(secret)
    token = actors["admin"].headers["Authorization"]
    try:
        st, body = raw_get(rig, f"/api/uploads/{inside.name}", token)
        assert st == 200 and b"inside-ok" in body, f"control: legitimate file not served ({st})"

        targets = [outside.name, f"{ud.name}-evil/s.txt"]
        bad = []
        for tpl in TRAVERSAL:
            for t in targets if "{canary}" in tpl else [""]:
                path = "/api/uploads/" + tpl.replace("{canary}", t)
                if tpl == "{canary}":
                    path = "/api/uploads/" + t  # just the name: file does not exist in UPLOAD_DIR
                st, body = raw_get(rig, path, token)
                if st >= 500:
                    bad.append((path, st, "5xx"))
                elif secret.encode() in body or b"root:" in body:
                    bad.append((path, st, "content leaked"))
                elif st == 200:
                    bad.append((path, st, "200"))
        assert not bad, bad
    finally:
        for p in (outside, inside, sibling):
            p.unlink(missing_ok=True)
        sibling_dir.rmdir()


@pytest.mark.security
async def test_E_A01_004_symlink_in_upload_dir_not_followed_outside(rig, actors):
    """E-A01-004 a symlink inside UPLOAD_DIR pointing outside (e.g. planted via a bug elsewhere).

    Expected (secure): the file endpoint resolves symlinks and refuses targets outside the upload root.
    """
    ud = upload_dir(rig)
    secret = f"CANARY-{uuid.uuid4()}"
    target = ud.parent / f"symtarget-{uuid.uuid4().hex[:8]}.txt"
    link = ud / f"link-{uuid.uuid4().hex[:8]}.txt"
    target.write_text(secret)
    link.symlink_to(target)
    try:
        r = await actors["admin"].get(f"/api/uploads/{link.name}")
        assert r.status_code in (400, 403, 404) and secret not in r.text, f"symlink followed ({r.status_code})"
    finally:
        link.unlink(missing_ok=True)
        target.unlink(missing_ok=True)


@pytest.mark.security
async def test_E_A01_004_stored_image_path_cannot_reach_other_files(rig, seed, actors):
    """E-A01-004 traversal via *stored* fields: ``image_path`` in a reading and ``image_path`` of hint-rescan.

    Expected (secure): a reading created with ``image_path=../../x`` neither makes the app read/hash/serve nor
    delete anything outside UPLOAD_DIR; hint-rescan with traversal answers 404. Outside canary stays untouched.
    """
    ud = upload_dir(rig)
    outside = ud.parent / f"canary-{uuid.uuid4().hex[:8]}.png"
    outside.write_bytes(png_bytes("outside"))
    admin = actors["admin"]
    pid = prop_id(seed, "prop_a")
    mid = next(iter(meters_of(seed, "prop_a").values()))["id"]
    own_meter = mid
    rid = None
    try:
        for sp in (f"../{outside.name}", f"/{outside}", f"..%2f{outside.name}", f"uploads/../../{outside.name}"):
            r = await admin.post(f"/api/properties/{pid}/meters/{mid}/readings/", json={"value": "5", "image_path": sp})
            if r.status_code == 201:
                rid = r.json()["id"]
                assert r.json().get("image_hash") in (None, ""), "app hashed a file outside the upload directory"
                assert (await admin.delete(f"/api/properties/{pid}/meters/{mid}/readings/{rid}")).status_code == 204
                rid = None
            assert outside.exists(), f"image_path {sp!r} led to deletion of a file outside UPLOAD_DIR"
            r = await send(admin, "POST", "/api/ocr/hint-rescan", data={"image_path": sp, "meter_id": own_meter, "current_value": "x"})
            assert r.status_code in (400, 404), (sp, r.status_code)
    finally:
        if rid:
            await admin.delete(f"/api/properties/{pid}/meters/{mid}/readings/{rid}")
        outside.unlink(missing_ok=True)


# =========================================================================== E-A01-005
@pytest.mark.security
async def test_E_A01_005_patch_extra_fields_ignored_or_rejected(rig, seed, actors):
    """E-A01-005 mass assignment on PATCH/POST bodies (property, meter, reading, admin role).

    Sent: ids, ownership/foreign keys (``property_id``, ``meter_id``, ``owner_id``), privilege fields
    (``role``, ``is_active``), hash/image fields. Expected (secure): unknown fields are rejected (422) or
    ignored: the object keeps its id, property, owner, hash and server-controlled fields, and nothing
    new shows in the response. Works on throw-away objects created by the experiment.
    """
    admin = actors["admin"]
    pb = prop_id(seed, "prop_b")
    mb = next(iter(meters_of(seed, "prop_b").values()))["id"]
    created_prop = None
    try:
        r = await admin.post("/api/properties/", json={"name": "rig-massassign"})
        assert r.status_code == 201
        prop = r.json()
        created_prop = prop["id"]
        pid = created_prop

        # --- property
        other_id = str(uuid.uuid4())
        r = await admin.patch(f"/api/properties/{pid}", json={
            "name": "rig-massassign-2", "id": other_id, "role": "admin", "owner_id": seed["users"]["user_b"]["id"],
            "created_at": "2000-01-01T00:00:00Z", "is_active": False})
        assert r.status_code in (200, 422), r.text
        cur = (await admin.get(f"/api/properties/{pid}")).json()
        assert cur["id"] == pid and set(cur) == set(prop), cur
        assert (await admin.get(f"/api/properties/{other_id}")).status_code == 404

        # --- meter: cannot be moved to another property, id fixed
        r = await admin.post(f"/api/properties/{pid}/meters/", json={"meter_type": "water", "unit": "m³", "name": "rig-m"})
        assert r.status_code == 201
        meter = r.json()
        mid = meter["id"]
        new_id = str(uuid.uuid4())
        r = await admin.patch(f"/api/properties/{pid}/meters/{mid}",
                              json={"name": "rig-m2", "property_id": pb, "id": new_id, "meter_type": "electricity"})
        assert r.status_code in (200, 422), r.text
        cur = (await admin.get(f"/api/properties/{pid}/meters/{mid}")).json()
        assert cur["id"] == mid and cur["property_id"] == pid and cur["meter_type"] == "water", cur
        assert (await admin.get(f"/api/properties/{pb}/meters/{mid}")).status_code == 404

        # --- reading: create + patch with foreign meter, hash, image, id
        rpath = f"/api/properties/{pid}/meters/{mid}/readings/"
        r = await admin.post(rpath, json={"value": "10", "meter_id": mb, "image_hash": "deadbeef" * 8, "id": 4242424})
        assert r.status_code in (201, 422), r.text
        if r.status_code == 201:
            rd = r.json()
            assert rd["meter_id"] == mid and rd["id"] != 4242424 and rd["image_hash"] != "deadbeef" * 8, rd
            rid = rd["id"]
            r = await admin.patch(f"{rpath}{rid}", json={
                "note": "n", "meter_id": mb, "image_path": "uploads/x.png", "serial_image_path": "uploads/y.png",
                "image_hash": "feedface" * 8, "source": "ocr", "id": 1})
            assert r.status_code in (200, 422), r.text
            cur = (await admin.get(rpath)).json()
            assert len(cur) == 1 and cur[0]["meter_id"] == mid and cur[0]["id"] == rid and cur[0]["image_path"] is None \
                and cur[0]["image_hash"] != "feedface" * 8 and cur[0]["source"] == rd["source"], cur

        # --- admin role endpoint: extra fields must not change other attributes
        uid = seed["users"]["nogroup"]["id"]
        users_before = {u["id"]: u for u in (await admin.get("/api/admin/users")).json()}
        r = await admin.patch(f"/api/admin/users/{uid}/role",
                              json={"role": users_before[uid]["role"], "is_active": False, "email": "evil@rig.invalid",
                                    "username": "evil", "id": str(uuid.uuid4()), "oidc_sub": "x"})
        assert r.status_code in (200, 422), r.text
        users_after = {u["id"]: u for u in (await admin.get("/api/admin/users")).json()}
        assert users_after == users_before, "extra fields changed a user record"
    finally:
        if created_prop:
            await admin.delete(f"/api/properties/{created_prop}")


@pytest.mark.security
async def test_E_A01_005_meter_patch_cannot_reference_foreign_property_meter(seed, actors):
    """E-A01-005 foreign key supplied by a writer: ``replaced_by_id`` pointing to another tenant's meter.

    ``manager`` (write on prop-a) patches a throw-away meter of prop-a with ``replaced_by_id`` = a meter
    of prop-b. Expected (secure): rejected (400/403/404/422); a link to a meter outside the caller's
    reach must not be storable (it would later leak or mislead). (Suspected weakness: ``MeterUpdate``
    accepts any UUID and the router never validates it.)
    """
    admin, mgr = actors["admin"], actors["manager"]
    pa = prop_id(seed, "prop_a")
    foreign = next(iter(meters_of(seed, "prop_b").values()))["id"]
    r = await admin.post(f"/api/properties/{pa}/meters/", json={"meter_type": "water", "unit": "m³", "name": "rig-link"})
    assert r.status_code == 201
    mid = r.json()["id"]
    try:
        r = await mgr.patch(f"/api/properties/{pa}/meters/{mid}", json={"replaced_by_id": foreign})
        assert r.status_code in (400, 403, 404, 409, 422), f"foreign meter reference stored ({r.status_code}): {r.text[:120]}"
        cur = (await admin.get(f"/api/properties/{pa}/meters/{mid}")).json()
        assert cur["replaced_by_id"] != foreign
    finally:
        await admin.patch(f"/api/properties/{pa}/meters/{mid}", json={"replaced_at": None})
        await admin.delete(f"/api/properties/{pa}/meters/{mid}")


@pytest.mark.security
async def test_E_A01_005_privileged_fields_not_settable_on_own_account(seed, actors):
    """E-A01-005 self-service: the low-privilege user tries to change himself through every reachable PATCH/POST.

    Expected (secure): ``user_a`` cannot change his global role or property role (admin API 403, property
    member API 403); ``/auth/me`` stays ``user``.
    """
    ua = actors["user_a"]
    uid = seed["users"]["user_a"]["id"]
    pa = prop_id(seed, "prop_a")
    for method, path, body in (
        ("PATCH", f"/api/admin/users/{uid}/role", {"role": "admin"}),
        ("POST", f"/api/properties/{pa}/users", {"user_id": uid, "role": "admin"}),
        ("PATCH", "/api/auth/me", {"role": "admin"}),
        ("PUT", "/api/auth/me", {"role": "admin"}),
    ):
        r = await ua.request(method, path, json=body)
        assert r.status_code in (403, 404, 405), (method, path, r.status_code)
    me = (await ua.get("/api/auth/me")).json()
    assert me["role"] == "user"


# =========================================================================== E-A01-006
# identifiers that carry request/user-controlled data
TAINTED = {"body", "request", "form", "file", "files", "filename", "image_path", "query", "q", "current_user",
           "user", "integration_config", "payload", "data", "params", "headers", "meter", "reading", "serial_image_path"}
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "request", "stream", "head", "options", "send"}


def _names(node: ast.AST) -> set[str]:
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
    return out


def _outbound_calls():
    """(file, line, function, url-expression-source, identifiers) for every httpx/client call in the app."""
    for f in sorted(BACKEND_APP.rglob("*.py")):
        src = f.read_text()
        tree = ast.parse(src)
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            params = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
            for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
                func = call.func
                if not (isinstance(func, ast.Attribute) and func.attr in HTTP_METHODS):
                    continue
                root = func.value
                root_names = _names(root)
                if not ({"httpx", "client", "_httpx"} & root_names):
                    continue
                if not call.args and not any(k.arg == "url" for k in call.keywords):
                    continue
                url = call.args[0] if func.attr not in ("request", "stream") and call.args else (
                    call.args[1] if len(call.args) > 1 else next((k.value for k in call.keywords if k.arg == "url"), call.args[0]))
                yield f, call.lineno, fn.name, ast.unparse(url), _names(url), params


@pytest.mark.security
def test_E_A01_006_outbound_urls_are_never_request_controlled():
    """E-A01-006 static analysis: where do server-side HTTP requests get their URL from?

    Walks the application source for every ``httpx`` / ``client`` call and inspects the URL expression.
    Expected (secure): URLs are built only from settings, module constants or the (operator-configured)
    OIDC discovery document -- no identifier that carries request data (body, form, file name, stored
    ``integration_config``, ``image_path`` ...) appears in a URL, and at least the known outbound calls
    (OIDC discovery/token/userinfo, gateway/Ollama, OCR service, oil price) were seen.
    """
    calls = list(_outbound_calls())
    assert len(calls) >= 6, f"analysis found only {len(calls)} outbound calls: {[(c[0].name, c[1]) for c in calls]}"
    tainted = []
    for f, line, fn, expr, idents, params in calls:
        bad = (idents & TAINTED) | (idents & params - {"self", "client", "cfg", "path", "url"})
        if bad:
            tainted.append((f"{f.relative_to(REPO)}:{line}", fn, expr, sorted(bad)))
    assert not tainted, tainted


@pytest.mark.security
async def test_E_A01_006_stored_integration_config_never_triggers_requests(rig, seed, actors):
    """E-A01-006 dynamic: ``integration_config`` / ``integration_type`` (shelly, homeassistant ...) are user-writable.

    A meter is given an ``integration_config`` pointing at the OCR stand-in (loopback) and at the cloud
    metadata address, with integration type ``shelly``/``homeassistant``; every meter-related route is
    then exercised. Expected (secure): no stand-in request carries the marker path and the stored value is
    never echoed (the response only says that a config exists). If the backend ever starts fetching these
    URLs the stand-in log shows it.
    """
    admin = actors["admin"]
    pa = prop_id(seed, "prop_a")
    marker = f"/ssrf-{uuid.uuid4().hex[:8]}"
    cfg = {"url": f"{rig['OCR_URL']}{marker}", "host": "169.254.169.254", "base_url": f"http://169.254.169.254{marker}",
           "token": "t0k3n-should-not-echo"}
    r = await admin.post(f"/api/properties/{pa}/meters/", json={
        "meter_type": "electricity", "unit": "kWh", "name": "rig-ssrf", "integration_type": "shelly", "integration_config": cfg})
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    try:
        assert "t0k3n" not in r.text and "169.254" not in r.text, "integration_config echoed in the response"
        base = f"/api/properties/{pa}/meters/{mid}"
        await admin.patch(base, json={"integration_type": "homeassistant"})
        await admin.get(base)
        await admin.get(f"{base}/readings/")
        await admin.get(f"/api/dashboard/consumption/{mid}")
        await admin.get(f"/api/dashboard/cost-forecast/{mid}")
        await admin.get("/api/dashboard/summary")
        await admin.get("/api/health")
        txt = (await admin.get(base)).text
        assert "t0k3n" not in txt and "169.254" not in txt
        async with httpx.AsyncClient(timeout=10) as c:
            seen = (await c.get(f"{rig['OCR_URL']}/_control/requests")).json()
        assert not [x for x in seen if marker in x.get("path", "")], "backend requested a URL taken from integration_config"
    finally:
        await admin.delete(f"/api/properties/{pa}/meters/{mid}")


@pytest.mark.security
def test_E_A01_006_configured_urls_not_settable_via_api():
    """E-A01-006 configured URLs (``OIDC_DISCOVERY_URL``, ``GATEWAY_BASE_URL``, ``OCR_URL``, ``OLLAMA_URL``,
    ``OIL_PRICE_API_URL``) point where the operator says -- including loopback / cluster-internal addresses by design.

    Expected (secure): these values are operator-only (environment / secret), never writable through the
    API, and the OIDC endpoints taken from the discovery document are either validated against the issuer
    or the discovery URL is trusted configuration. The experiment asserts the first part (no route of the
    OpenAPI schema accepts a field named like a URL setting) and records the second in the report.
    """
    schema_text = ""
    for f in (BACKEND_APP / "schemas").glob("*.py"):
        schema_text += f.read_text()
    for field in ("oidc_discovery_url", "gateway_base_url", "ocr_url", "ollama_url", "oil_price_api_url", "base_url", "callback_url", "webhook"):
        assert not re.search(rf"^\s*{field}\s*:", schema_text, re.M | re.I), f"API schema accepts a URL-valued field {field}"
