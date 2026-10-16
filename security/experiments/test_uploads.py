"""E-UP-001 (A01/A05): upload hardening of POST /api/ocr/scan and /api/ocr/bulk-scan.

Expected (secure) result for every experiment: invalid or dangerous input is rejected with a 4xx
BEFORE heavy processing and BEFORE anything is written, limits are enforced, nothing lands outside the
upload directory, stored names are server generated.

Assumes the stand-in gateway is in mode ``ok`` (the ``gateway`` fixture auto-resets).
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import time
import zipfile

import pytest

from sec_common import (API, bmp_huge_header, bulk_scan, get_client, gif_huge_header, jpeg_ok, list_files,
                        make_world, parse_sse, png_bomb, png_ok, request_retry, scan, upload_dir)

pytestmark = pytest.mark.security

REJECT = {400, 413, 415, 422}
STORED_NAME = re.compile(r"^uploads/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(_serial)?\.(jpg|png|webp|gif|bmp|tiff)$")
ISO_EXIF = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?$")
MB = 1024 * 1024


@pytest.fixture
async def admin(client):
    return await get_client(client, "admin")


@pytest.fixture
async def user_a(client):
    return await get_client(client, "user_a")


@pytest.fixture
async def user_b(client):
    return await get_client(client, "user_b")


@pytest.fixture
def updir(rig):
    return upload_dir(rig)


@pytest.fixture(autouse=True)
def _gateway_ok(gateway):
    """Pin the vision stand-in to ``ok`` for all upload experiments (fixture auto-resets afterwards)."""
    return gateway


def _raw_multipart(parts: list[tuple[bytes, bytes, bytes | None, bytes]]):
    """parts = (name, filename_or_None, content_type_or_None, payload) -> (body, content_type_header).

    Hand-built so that CR/LF, NUL, invalid UTF-8 and huge values reach the server unescaped.
    """
    boundary = b"----secboundary7MA4YWxkTrZu0gW"
    out = b""
    for name, filename, ctype, payload in parts:
        out += b"--" + boundary + b"\r\nContent-Disposition: form-data; name=\"" + name + b"\""
        if filename is not None:
            out += b"; filename=\"" + filename + b"\""
        out += b"\r\n"
        if ctype:
            out += b"Content-Type: " + ctype + b"\r\n"
        out += b"\r\n" + payload + b"\r\n"
    out += b"--" + boundary + b"--\r\n"
    return out, "multipart/form-data; boundary=" + boundary.decode()


async def _post_raw(c, path: str, parts, timeout: float = 120.0):
    body, ctype = _raw_multipart(parts)

    async def once():
        return await c.post(f"{API}{path}", content=body, headers={"content-type": ctype}, timeout=timeout)

    return await request_retry(once)


# ----------------------------------------------------------------------------------------- 1. type vs magic bytes
async def test_E_UP_001_magic_bytes_vs_declared_type(admin, updir):
    """E-UP-001: content that is not an image is rejected (4xx) whatever Content-Type/extension claims; nothing is stored.

    Secure: 400/415/422, upload dir unchanged, never 5xx.
    """
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(document.domain)</script></svg>'
    cases = [
        ("html_as_png", b"<html><script>alert(1)</script></html>", "a.png", "image/png"),
        ("svg_as_png", svg, "a.png", "image/png"),
        ("svg_as_svg", svg, "a.svg", "image/svg+xml"),
        ("elf_as_jpeg", b"\x7fELF" + bytes(200), "a.jpg", "image/jpeg"),
        ("zip_as_png", b"PK\x03\x04" + bytes(100), "a.png", "image/png"),
        ("pdf_as_png", b"%PDF-1.7\n%%EOF", "a.png", "image/png"),
        ("php_as_gif", b"<?php system($_GET['c']); ?>", "a.gif", "image/gif"),
        ("shell_as_webp", b"#!/bin/sh\nid\n", "a.webp", "image/webp"),
        ("empty_file", b"", "a.png", "image/png"),
        ("one_byte", b"\x89", "a.png", "image/png"),
        ("image_ct_missing_subtype", b"<html>", "a.png", "image/"),
    ]
    problems = []
    for label, data, fname, ctype in cases:
        before = list_files(updir)
        r = await scan(admin, data, filename=fname, content_type=ctype)
        after = list_files(updir)
        if r.status_code not in REJECT:
            problems.append(f"{label}: status {r.status_code} (want 4xx)")
        if after != before:
            problems.append(f"{label}: stored {sorted(after - before)}")
    assert not problems, "\n".join(problems)


async def test_E_UP_001_content_type_spoofing_reverse(admin, updir):
    """E-UP-001: a genuine PNG sent with a wrong/odd declared type is either rejected or stored with a server-chosen
    .png name -- never 5xx, never stored under the client's extension."""
    problems = []
    for ctype in ("text/plain", "application/octet-stream", "text/html", "image/png; charset=\"><script>", ""):
        before = list_files(updir)
        r = await scan(admin, png_ok(seed=1), filename="x.html", content_type=ctype or "application/x-empty")
        new = list_files(updir) - before
        if r.status_code >= 500:
            problems.append(f"{ctype!r}: 5xx {r.status_code}")
        elif r.status_code == 200:
            if len(new) != 1 or not STORED_NAME.match("uploads/" + next(iter(new))):
                problems.append(f"{ctype!r}: stored names {sorted(new)}")
        elif new:
            problems.append(f"{ctype!r}: rejected ({r.status_code}) but stored {sorted(new)}")
    assert not problems, "\n".join(problems)


async def test_E_UP_001_bulk_scan_rejects_non_images(admin, updir):
    """E-UP-001: bulk-scan validates each file by magic bytes; non-images yield an item error, no stored temp file,
    and the stream stays well formed."""
    before = list_files(updir)
    items = [
        ("evil.png", b"<html><script>alert(1)</script></html>", "image/png"),
        ("empty.png", b"", "image/png"),
        ("php.jpg", b"<?php echo 1;", "image/jpeg"),
        ("svg.png", b"<svg onload=alert(1)>", "image/png"),
    ]
    r = await bulk_scan(admin, items)
    assert r.status_code < 500, r.text[:300]
    if r.status_code == 200:
        ev = parse_sse(r.text)
        data = [d for n, d in ev if n != "complete"]
        assert len(data) == len(items)
        for d in data:
            assert d["temp_image_path"] is None and d["error"], d
    else:
        assert r.status_code in REJECT
    assert list_files(updir) == before, "non-image content was written to disk"


# ----------------------------------------------------------------------------------------- 2. polyglots
def _zip_bytes() -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("evil.php", "<?php system($_GET[0]);")
    return b.getvalue()


async def test_E_UP_001_polyglot_files_served_inert(admin, updir):
    """E-UP-001: image/HTML, image/PHP, image/ZIP polyglots are rejected, or stored under a generated image name and
    served back ONLY with an image/* content type (never text/html), as attachment or image."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (32, 16)).save(buf, "JPEG", comment=b"<script>alert(1)</script><?php system(1);?>")
    cases = {
        "gif_html": b"GIF89a/*" + bytes(10) + b"*/=1;alert(1);//<html><script>alert(document.domain)</script></html>",
        "png_php_trailer": png_ok(seed=2) + b"<?php system($_GET['c']); ?>",
        "jpeg_zip_trailer": jpeg_ok() + _zip_bytes(),
        "jpeg_script_comment": buf.getvalue(),
        "png_html_trailer": png_ok(seed=3) + b"<html><body onload=alert(1)>",
    }
    problems = []
    for label, data in cases.items():
        before = list_files(updir)
        r = await scan(admin, data, filename=f"{label}.png.php", content_type="image/png")
        new = list_files(updir) - before
        if r.status_code >= 500:
            problems.append(f"{label}: 5xx {r.status_code}")
            continue
        if r.status_code != 200:
            if new:
                problems.append(f"{label}: rejected but stored {sorted(new)}")
            continue
        ip = r.json()["image_path"]
        if not STORED_NAME.match(ip):
            problems.append(f"{label}: unsafe stored name {ip}")
            continue
        g = await admin.get(f"{API}/{ip}")
        ctype = g.headers.get("content-type", "")
        if g.status_code != 200 or not ctype.startswith("image/"):
            problems.append(f"{label}: served as {g.status_code} {ctype!r}")
    assert not problems, "\n".join(problems)


# ----------------------------------------------------------------------------------------- 3. limits
async def test_E_UP_001_size_limit_enforced(admin, updir):
    """E-UP-001: > 10 MB (limit + 1 byte, and 30 MB) -> 413 without storing; same for the serial crop and bulk-scan items;
    more than 50 bulk files -> 400."""
    head = png_ok(seed=4)
    limit = 10 * MB
    problems = []

    before = list_files(updir)
    r = await scan(admin, head + bytes(limit + 1 - len(head)))
    if r.status_code != 413:
        problems.append(f"limit+1: {r.status_code}")
    t0 = time.monotonic()
    r = await scan(admin, head + bytes(30 * MB))
    if r.status_code != 413:
        problems.append(f"30MB: {r.status_code}")
    if time.monotonic() - t0 > 30:
        problems.append("30MB rejection took > 30 s")
    r = await scan(admin, png_ok(seed=5), serial=(head + bytes(limit), "s.png", "image/png"))
    if r.status_code != 413:
        problems.append(f"serial over limit: {r.status_code}")
    if list_files(updir) != before:
        problems.append(f"oversize request left files: {sorted(list_files(updir) - before)}")

    r = await bulk_scan(admin, [("big.png", head + bytes(limit), "image/png")])
    if r.status_code == 200:
        d = [x for n, x in parse_sse(r.text) if n != "complete"][0]
        if not d["error"] or d["temp_image_path"]:
            problems.append(f"bulk oversize accepted: {d}")
    elif r.status_code not in REJECT:
        problems.append(f"bulk oversize: {r.status_code}")
    if list_files(updir) != before:
        problems.append("bulk oversize stored a file")

    r = await bulk_scan(admin, [(f"{i}.png", head, "image/png") for i in range(51)])
    if r.status_code != 400:
        problems.append(f"51 bulk files: {r.status_code} (want 400)")
    assert list_files(updir) == before, "51-file batch stored files"
    assert not problems, "\n".join(problems)


async def test_E_UP_001_decompression_bomb_and_huge_dimensions(admin, updir):
    """E-UP-001: images that are tiny on the wire but huge in pixels (PNG 20000x20000 and 9500x9500, GIF 65535x65535,
    BMP 40000x40000) are rejected quickly with 4xx and not stored; the backend stays responsive.

    Secure: dimension/pixel cap checked from the header before any decode or write.
    """
    cases = {
        "png_20000x20000": png_bomb(20000, 20000),
        "png_9500x9500_just_above_pil_warn": png_bomb(9500, 9500),
        "gif_65535x65535": gif_huge_header(),
        "bmp_40000x40000": bmp_huge_header(),
    }
    problems = []
    for label, data in cases.items():
        assert len(data) < 10 * MB, f"{label} bomb fixture itself exceeds the size limit ({len(data)})"
        before = list_files(updir)
        t0 = time.monotonic()
        r = await scan(admin, data, content_type="image/png")
        dt = time.monotonic() - t0
        new = list_files(updir) - before
        if r.status_code not in REJECT:
            problems.append(f"{label}: status {r.status_code} (want 4xx), {len(data)} bytes accepted")
        if new:
            problems.append(f"{label}: stored {sorted(new)}")
        if dt > 10:
            problems.append(f"{label}: took {dt:.1f}s")
        live = await admin.get(f"{API}/health/live", timeout=5)
        if live.status_code != 200:
            problems.append(f"{label}: backend not live afterwards ({live.status_code})")
    # same via bulk-scan (decode attempted for the EXIF date)
    before = list_files(updir)
    r = await bulk_scan(admin, [("bomb.png", cases["png_20000x20000"], "image/png")])
    if r.status_code == 200:
        d = [x for n, x in parse_sse(r.text) if n != "complete"][0]
        if d["temp_image_path"] is not None:
            problems.append("bulk-scan stored the decompression bomb")
    if list_files(updir) != before:
        problems.append("bulk-scan wrote the bomb to disk")
    assert not problems, "\n".join(problems)


async def test_E_UP_001_truncated_and_broken_images(admin, updir):
    """E-UP-001: files with a valid magic number but undecodable body (truncated JPEG/PNG, signature only) are rejected
    (4xx) and not stored."""
    j, p = jpeg_ok(400, 300), png_ok(400, 300)
    cases = {
        "jpeg_half": j[: len(j) // 2],
        "png_half": p[: len(p) // 2],
        "png_signature_only": b"\x89PNG\r\n\x1a\n",
        "jpeg_soi_only": b"\xff\xd8\xff",
        "png_corrupt_idat": p[:40] + bytes(len(p) - 40),
    }
    problems = []
    for label, data in cases.items():
        before = list_files(updir)
        r = await scan(admin, data)
        new = list_files(updir) - before
        if r.status_code not in REJECT:
            problems.append(f"{label}: status {r.status_code} (want 4xx)")
        if new:
            problems.append(f"{label}: stored {sorted(new)}")
    assert not problems, "\n".join(problems)


# ----------------------------------------------------------------------------------------- 4. EXIF
def _exif(gps: bool = True, date: str | None = None):
    from PIL import Image

    ex = Image.Exif()
    if gps:
        ex[0x8825] = {1: "N", 2: (52.0, 31.0, 12.0), 3: "E", 4: (13.0, 24.0, 36.0)}
    if date is not None:
        ex[0x8769] = {36867: date}
    return ex


async def test_E_UP_001_exif_gps_not_retained(admin, updir):
    """E-UP-001: GPS coordinates in the EXIF block of an uploaded photo are not served back to other users
    (privacy). Secure: stored copy has no GPS IFD (stripped/re-encoded) or the upload is rejected."""
    from PIL import Image

    data = jpeg_ok(64, 64, exif=_exif(gps=True))
    assert dict(Image.open(io.BytesIO(data)).getexif().get_ifd(0x8825)), "fixture broken: no GPS in test image"
    r = await scan(admin, data, filename="gps.jpg", content_type="image/jpeg")
    if r.status_code in REJECT:
        return
    assert r.status_code == 200, r.text[:200]
    name = r.json()["image_path"].split("/", 1)[1]
    stored = Image.open(updir / name).getexif()
    assert not dict(stored.get_ifd(0x8825)), f"GPS EXIF kept in stored upload: {dict(stored.get_ifd(0x8825))}"


async def test_E_UP_001_exif_date_abuse_and_oversize_exif(admin, updir):
    """E-UP-001: hostile EXIF DateTimeOriginal never reaches the client as free text: bulk-scan `exif_date` is a
    well-formed ISO date(-time) or null; a 60 KB EXIF block causes no 5xx."""
    evil = "<svg/onloa d=alert(1)>"  # >= 19 chars: the app slices [:10] and [11:16] out of it
    items = [
        ("evil-date.jpg", jpeg_ok(64, 64, exif=_exif(gps=False, date=evil)), "image/jpeg"),
        ("sql-date.jpg", jpeg_ok(64, 64, exif=_exif(gps=False, date="'; DROP TABLE r;-- 1234567890")), "image/jpeg"),
        ("short-date.jpg", jpeg_ok(64, 64, exif=_exif(gps=False, date="2025:01:02")), "image/jpeg"),
        ("big-exif.jpg", jpeg_ok(64, 64, exif=_exif(gps=False, date="2025:01:02 03:04:05" + "A" * 60000)), "image/jpeg"),
    ]
    r = await bulk_scan(admin, items)
    assert r.status_code < 500, r.text[:300]
    if r.status_code != 200:
        return
    bad = []
    for n, d in parse_sse(r.text):
        if n == "complete":
            continue
        v = d.get("exif_date")
        if v is not None and not ISO_EXIF.match(v):
            bad.append(f"{d['original_filename']}: exif_date={v!r}")
    assert not bad, "free-text EXIF content returned as exif_date:\n" + "\n".join(bad)


# ----------------------------------------------------------------------------------------- 5. names and paths
EVIL_NAMES: list[tuple[str, bytes]] = [
    ("traversal", b"../../../../tmp/zom-sec-evil.png"),
    ("absolute", b"/etc/cron.d/zom-sec-evil.png"),
    ("backslash", b"..\\..\\zom-sec-evil.png"),
    ("null_byte_ext", b"zom-sec-evil.png\x00.php"),
    ("crlf_header_injection", b"a.png\r\nContent-Type: text/html\r\n\r\n<script>alert(1)</script>"),
    ("overlong_5000", b"A" * 5000 + b".png"),
    ("overlong_70000", b"B" * 70000 + b".png"),
    ("unicode_rtl_override", "‮gnp.exe-ünïcödé-\U0001f600".encode()),
    ("invalid_utf8", b"\xff\xfe-bad-utf8.png"),
    ("windows_reserved", b"CON.png"),
    ("empty", b""),
    ("dotfile", b".htaccess"),
    ("quote_breakout", b'a.png"; name="file'),
    ("percent_encoded", b"%2e%2e%2fzom-sec-evil.png"),
    ("double_ext_php", b"shell.php.png"),
]


def _outside_snapshot(updir):
    parent = updir.parent
    return {p.name for p in parent.iterdir()} if parent.exists() else set()


async def test_E_UP_001_filenames_in_scan_never_used_for_storage(admin, updir):
    """E-UP-001: client filenames (traversal, absolute, backslash, NUL, CRLF, overlong, unicode, invalid UTF-8) never
    influence the stored path: the response is 200 with a generated `uploads/<uuid>.<ext>` or a 4xx -- never 5xx --
    and nothing is created outside the upload directory or with a non-generated name."""
    png = png_ok(seed=6)
    outside_before = _outside_snapshot(updir)
    before = list_files(updir)
    problems = []
    for label, fname in EVIL_NAMES:
        r = await _post_raw(admin, "/ocr/scan", [
            (b"file", fname, b"image/png", png), (b"engine", None, None, b"llm")])
        if r.status_code >= 500:
            problems.append(f"{label}: 5xx {r.status_code}")
        elif r.status_code == 200:
            ip = r.json()["image_path"]
            if not STORED_NAME.match(ip):
                problems.append(f"{label}: stored as {ip}")
            if fname and len(fname) > 8 and fname.decode("utf-8", "ignore")[:12] in r.text:
                problems.append(f"{label}: filename reflected in response")
    new = list_files(updir) - before
    bad_names = [n for n in new if not STORED_NAME.match("uploads/" + n) or "/" in n]
    if bad_names:
        problems.append(f"non-generated files in upload dir: {bad_names}")
    outside_new = _outside_snapshot(updir) - outside_before
    if outside_new:
        problems.append(f"entries created next to upload dir: {sorted(outside_new)}")
    for p in ("/tmp/zom-sec-evil.png", "/etc/cron.d/zom-sec-evil.png"):
        if os.path.exists(p):
            problems.append(f"file written to {p}")
    assert not problems, "\n".join(problems)


async def test_E_UP_001_filenames_in_bulk_scan_inert_in_stream(admin, updir):
    """E-UP-001: hostile filenames in bulk-scan are echoed only as JSON string values inside well-formed SSE (no event
    injection through CR/LF), stored names are generated, files stay in the upload dir."""
    png_items = [(f, png_ok(seed=10 + i)) for i, (_, f) in enumerate(EVIL_NAMES) if len(f) < 10000]
    outside_before = _outside_snapshot(updir)
    before = list_files(updir)
    parts = [(b"files", f, b"image/png", d) for f, d in png_items] + [(b"engine", None, None, b"llm")]
    r = await _post_raw(admin, "/ocr/bulk-scan", parts, timeout=300)
    assert r.status_code < 500, r.text[:300]
    if r.status_code == 200:
        ev = parse_sse(r.text)  # raises on injected lines
        names = [n for n, _ in ev]
        assert names.count("complete") == 1 and names[-1] == "complete", names[-3:]
        items = [d for n, d in ev if n != "complete"]
        assert len(items) == len(png_items), f"{len(items)} items for {len(png_items)} files (injected/lost events)"
        for d in items:
            if d["temp_image_path"] is not None:
                assert STORED_NAME.match(d["temp_image_path"]), d["temp_image_path"]
    new = list_files(updir) - before
    assert all(STORED_NAME.match("uploads/" + n) and "/" not in n for n in new), sorted(new)
    assert not (_outside_snapshot(updir) - outside_before)


# ----------------------------------------------------------------------------------------- 6. duplicates
async def test_E_UP_001_duplicate_uploads_and_client_hash(admin, updir):
    """E-UP-001: the same image uploaded repeatedly gets distinct generated names and one identical server SHA-256; a
    client-supplied image_hash is ignored (server-computed), the second reading with the same image is a defined 409,
    and bulk-scan flags in-batch duplicates."""
    img = png_ok(seed=21)
    digest = hashlib.sha256(img).hexdigest()
    w = await make_world(admin, "up-dup")
    paths = []
    for _ in range(3):
        r = await scan(admin, img)
        assert r.status_code == 200, r.text[:300]
        j = r.json()
        assert j["image_hash"] == digest
        paths.append(j["image_path"])
    assert len(set(paths)) == 3, f"duplicate uploads overwrote/reused a name: {paths}"

    rr = await bulk_scan(admin, [("a.png", img, "image/png"), ("b.png", img, "image/png")])
    assert rr.status_code == 200
    items = [d for n, d in parse_sse(rr.text) if n != "complete"]
    assert items[1]["duplicate_reading"] and items[1]["duplicate_reading"]["source"] == "session"

    base = f"{API}/properties/{w['property_id']}/meters/{w['meter_id']}/readings/"
    c1 = await admin.post(base, json={"value": "101.5", "image_path": paths[0], "image_hash": "f" * 64})
    assert c1.status_code == 201, c1.text
    assert c1.json()["image_hash"] == digest, "client-supplied image_hash was stored"
    c2 = await admin.post(base, json={"value": "102.5", "image_path": paths[1]})
    assert c2.status_code == 409, f"same image twice -> {c2.status_code} {c2.text[:200]}"
    assert c2.json()["detail"]["code"] == "duplicate_image"


async def test_E_UP_001_duplicate_check_is_no_cross_tenant_oracle(user_b, seed, updir):
    """E-UP-001: user B uploading the exact bytes of one of A's stored images learns nothing about A's reading
    (no duplicate_reading from the database, no value/date). Secure: duplicate lookup scoped to accessible meters."""
    rd = seed["readings"]["meter_a_water"][0]
    data = (updir / rd["filename"]).read_bytes()
    r = await bulk_scan(user_b, [("probe.png", data, "image/png")])
    assert r.status_code == 200, r.text[:300]
    item = [d for n, d in parse_sse(r.text) if n != "complete"][0]
    assert item["duplicate_reading"] is None, f"cross-tenant duplicate oracle: {item['duplicate_reading']}"
    s = await scan(user_b, data)
    assert s.status_code == 200
    assert set(s.json()) <= {"image_path", "serial_image_path", "image_hash", "raw_texts", "detected_value",
                             "detected_serial", "detection_method"}, f"scan response has extra data: {sorted(s.json())}"


async def test_E_UP_001_reading_cannot_adopt_foreign_upload(admin, user_a, user_b, seed, updir):
    """E-UP-001 (A01): a writer cannot attach an upload they did not make to their own reading. Otherwise they could read
    (GET /api/uploads/<name>) and delete (DELETE reading) another user's file by knowing only its filename.

    Secure: POST reading with a foreign `image_path` -> 4xx; at minimum the file stays unreadable and undeleted.
    """
    w = await make_world(admin, "up-adopt")
    add = await admin.post(f"{API}/properties/{w['property_id']}/users",
                           json={"user_id": seed["users"]["user_b"]["id"], "role": "manager"})
    assert add.status_code in (200, 201), add.text
    victim = await scan(user_a, png_ok(seed=31))
    assert victim.status_code == 200, victim.text[:200]
    ip = victim.json()["image_path"]
    fname = ip.split("/", 1)[1]

    base = f"{API}/properties/{w['property_id']}/meters/{w['meter_id']}/readings/"
    c = await user_b.post(base, json={"value": "5.0", "image_path": ip})
    problems = []
    if c.status_code == 201:
        problems.append("user B attached user A's upload to their own reading (201)")
        g = await user_b.get(f"{API}/{ip}")
        if g.status_code == 200:
            problems.append("... and can now download it via /api/uploads")
        await user_b.delete(f"{base}{c.json()['id']}")
        if not (updir / fname).exists():
            problems.append("... and deleting that reading deleted user A's file from disk")
    elif c.status_code >= 500:
        problems.append(f"5xx {c.status_code}")
    assert not problems, "\n".join(problems)


# ----------------------------------------------------------------------------------------- 7. malformed requests
async def test_E_UP_001_malformed_multipart_and_params(admin, user_b, seed, updir):
    """E-UP-001: malformed multipart / parameters on the upload endpoints produce 4xx (never 5xx) and store nothing:
    file as text field, missing boundary, truncated body, invalid engine/meter_id/bool, wrong serial type, foreign meter."""
    png = png_ok(seed=41)
    before = list_files(updir)
    problems = []

    def chk(label, r, allowed):
        if r.status_code not in allowed:
            problems.append(f"{label}: {r.status_code} (want {sorted(allowed)}) {r.text[:120]!r}")

    r = await request_retry(lambda: admin.post(f"{API}/ocr/scan", data={"file": "not-a-file"}))
    chk("file_as_text_field", r, {400, 422})
    r = await request_retry(lambda: admin.post(
        f"{API}/ocr/scan", content=b"--x\r\n", headers={"content-type": "multipart/form-data"}))
    chk("no_boundary", r, {400, 422})
    body, ctype = _raw_multipart([(b"file", b"a.png", b"image/png", png)])
    truncated = body[: len(body) // 2]
    r = await request_retry(lambda: admin.post(f"{API}/ocr/scan", content=truncated, headers={"content-type": ctype}))
    chk("truncated_body", r, {400, 422})
    for label, form in (("bad_engine", {"engine": "x; DROP TABLE"}), ("bad_meter_id", {"meter_id": "not-a-uuid"}),
                        ("bad_bool", {"already_cropped": "banana"})):
        r = await scan(admin, png, form=form)
        chk(label, r, {400, 422})
    r = await scan(admin, png, serial=(b"<html>", "s.png", "image/png"))
    chk("serial_not_image", r, {400})
    r = await scan(admin, png, serial=(b"<html>", "s.txt", "text/plain"))
    chk("serial_wrong_ct", r, {400})
    foreign = seed["meters"]["meter_a_water"]["id"]
    r = await scan(user_b, png, meter_id=foreign)
    chk("foreign_meter", r, {403, 404})
    assert list_files(updir) == before, f"rejected requests stored files: {sorted(list_files(updir) - before)}"
    two = [(b"file", b"1.png", b"image/png", png), (b"file", b"2.png", b"image/png", png)]
    r = await _post_raw(admin, "/ocr/scan", two)
    if r.status_code >= 500:
        problems.append(f"two file parts: {r.status_code}")
    assert not problems, "\n".join(problems)
