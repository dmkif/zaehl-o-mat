"""Shared helpers for the A05 / A08 / upload experiments (feature 004, T016/T019/T022).

Only helpers that these three files need. Assumptions about the conftest fixtures
(see CONTRACT.md "Experiment fixtures"):

* ``client(role)`` returns (or is awaitable and returns) an ``httpx.AsyncClient`` whose
  base_url is ``BACKEND_URL``; role keys are the seed keys: superadmin, admin, manager,
  user_a, user_b, nogroup. Paths used here therefore start with ``/api``.
* ``gateway`` / ``ocr`` are control handles for the stand-ins. Either an object with an
  httpx-like ``post`` (called with ``/_control/mode``) or an object offering ``set_mode``.
* ``rig`` is the parsed rig.env dict, ``seed`` the parsed seed.json.
"""
from __future__ import annotations

import asyncio
import inspect
import io
import json
import re
import struct
import time
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API = "/api"

SSE_EVENT_SPLIT = re.compile(r"\r?\n\r?\n")


async def maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def get_client(client, role: str):
    return await maybe_await(client(role))


async def set_mode(ctrl, mode: str, **params) -> None:
    """Switch a stand-in (gateway / ocr) into ``mode`` via the control API."""
    if hasattr(ctrl, "set_mode"):
        await maybe_await(ctrl.set_mode(mode, **params))
        return
    resp = await maybe_await(ctrl.post("/_control/mode", json={"mode": mode, **params}))
    assert resp.status_code == 200, f"stand-in refused mode {mode}: {resp.status_code} {resp.text[:200]}"


def upload_dir(rig) -> Path:
    p = Path(rig["UPLOAD_DIR"])
    return p if p.is_absolute() else (REPO_ROOT / p)


def list_files(root: Path) -> set[str]:
    if not root.exists():
        return set()
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


# --------------------------------------------------------------------------- images
def png_ok(w: int = 64, h: int = 32, seed: int = 0) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (w, h), (230 - seed % 50, 230, 220))
    ImageDraw.Draw(img).text((4, h // 3), f"RIG {seed}", fill=(10, 10, 10))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def jpeg_ok(w: int = 64, h: int = 32, exif=None) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (w, h), (200, 210, 220))
    buf = io.BytesIO()
    if exif is not None:
        img.save(buf, "JPEG", exif=exif)
    else:
        img.save(buf, "JPEG")
    return buf.getvalue()


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png_bomb(w: int, h: int) -> bytes:
    """Valid 8-bit grayscale PNG of w*h pixels that compresses to ~1/1000 (decompression bomb)."""
    comp = zlib.compressobj(9)
    raw_len = (w + 1) * h
    chunk = bytes(1 << 20)
    out = bytearray()
    sent = 0
    while sent < raw_len:
        n = min(len(chunk), raw_len - sent)
        out += comp.compress(chunk[:n])
        sent += n
    out += comp.flush()
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", bytes(out)) + _png_chunk(b"IEND", b"")


def gif_huge_header(w: int = 65535, h: int = 65535) -> bytes:
    """GIF whose logical screen / frame claim 65535x65535 pixels with (almost) no data."""
    return (b"GIF89a" + struct.pack("<HHBBB", w, h, 0x80, 0, 0) + b"\x00\x00\x00\xff\xff\xff"
            + b"," + struct.pack("<HHHHB", 0, 0, w, h, 0) + b"\x02\x02\x44\x01\x00;")


def bmp_huge_header(w: int = 40000, h: int = 40000) -> bytes:
    info = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, 0, 2835, 2835, 0, 0)
    return b"BM" + struct.pack("<IHHI", 14 + len(info), 0, 0, 14 + len(info)) + info


# --------------------------------------------------------------------------- requests
async def request_retry(fn, *, max_wait: float = 70.0):
    """Call ``fn()`` (returns a response); on HTTP 429 wait for the rate-limit window and retry once or twice."""
    waited = 0.0
    while True:
        resp = await fn()
        if resp.status_code != 429 or waited >= max_wait:
            return resp
        delay = 15.0
        try:
            delay = min(float(resp.headers.get("retry-after", delay)) + 1.0, 62.0)
        except ValueError:
            pass
        await asyncio.sleep(delay)
        waited += delay


async def scan(c, data: bytes | None, *, filename: str = "meter.png", content_type: str = "image/png",
               engine: str = "llm", meter_id: str | None = None, serial: tuple | None = None,
               form: dict | None = None, timeout: float = 120.0):
    """POST /api/ocr/scan with retries on the rate limit. ``serial`` = (bytes, filename, content_type)."""
    async def once():
        files = {}
        if data is not None:
            files["file"] = (filename, data, content_type)
        if serial is not None:
            files["serial_file"] = (serial[1], serial[0], serial[2])
        body = {"engine": engine}
        if meter_id:
            body["meter_id"] = meter_id
        body.update(form or {})
        return await c.post(f"{API}/ocr/scan", files=files or None, data=body, timeout=timeout)

    return await request_retry(once)


async def bulk_scan(c, items: list[tuple[str, bytes, str]], *, engine: str = "llm", property_id: str | None = None,
                    timeout: float = 300.0):
    async def once():
        files = [("files", (n, d, ct)) for n, d, ct in items]
        body = {"engine": engine}
        if property_id:
            body["property_id"] = property_id
        return await c.post(f"{API}/ocr/bulk-scan", files=files, data=body, timeout=timeout)

    return await request_retry(once)


def parse_sse(text: str) -> list[tuple[str, object]]:
    """Strict SSE parse -> [(event_name, parsed_json)]. Raises on malformed blocks (that is a finding)."""
    events = []
    for block in SSE_EVENT_SPLIT.split(text):
        if not block.strip():
            continue
        name, data_lines = "message", []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip(" "))
            else:
                raise AssertionError(f"unexpected SSE line (stream injection?): {line[:120]!r}")
        events.append((name, json.loads("\n".join(data_lines))))
    return events


# --------------------------------------------------------------------------- domain setup
async def make_world(admin, tag: str = "sec"):
    """Create a throw-away property + water meter + one reading as admin. Returns dict of ids."""
    pr = await admin.post(f"{API}/properties/", json={"name": f"{tag}-prop", "address": "synthetic"})
    assert pr.status_code == 201, pr.text
    pid = pr.json()["id"]
    mr = await admin.post(f"{API}/properties/{pid}/meters/",
                          json={"meter_type": "water", "unit": "m³", "name": f"{tag}-meter",
                                "serial_number": f"{tag.upper()}-0001"})
    assert mr.status_code == 201, mr.text
    mid = mr.json()["id"]
    rr = await admin.post(f"{API}/properties/{pid}/meters/{mid}/readings/", json={"value": "100.000", "note": tag})
    assert rr.status_code == 201, rr.text
    return {"property_id": pid, "meter_id": mid, "reading_id": rr.json()["id"], "serial": f"{tag.upper()}-0001"}


async def count_readings(admin, w) -> int:
    r = await admin.get(f"{API}/properties/{w['property_id']}/meters/{w['meter_id']}/readings/",
                        params={"limit": 10000})
    assert r.status_code == 200, r.text
    return len(r.json())


def restore_seed_roles(rig, seed) -> int:
    """Reset the seed users' role/is_active straight in the DB (no API: a demoted superadmin cannot restore itself).
    Returns the number of rows that had drifted."""
    from sqlalchemy import create_engine, text

    eng = create_engine(rig["DATABASE_URL"])
    fixed = 0
    try:
        with eng.begin() as c:
            for u in seed["users"].values():
                r = c.execute(
                    text("UPDATE users SET role = :r, is_active = true WHERE id = :i AND (role::text <> :r OR NOT is_active)"),
                    {"r": u["role"], "i": u["id"]},
                )
                fixed += r.rowcount
    finally:
        eng.dispose()
    return fixed


def register_upload_row(rig, filename: str, property_id: str, uploaded_by: str | None = None) -> None:
    """Register a hand-written upload file in ``upload_files`` (F-004 registry) so it may be referenced.

    The row belongs to ``property_id`` (uploader NULL unless given), exactly like a bulk-scan upload."""
    from sqlalchemy import create_engine, text

    eng = create_engine(rig["DATABASE_URL"])
    try:
        with eng.begin() as c:
            c.execute(
                text("INSERT INTO upload_files (filename, property_id, uploaded_by_id, created_at) "
                     "VALUES (:f, :p, :u, now()) ON CONFLICT (filename) DO NOTHING"),
                {"f": filename, "p": property_id, "u": uploaded_by},
            )
    finally:
        eng.dispose()


def unregister_upload_row(rig, filename: str) -> None:
    from sqlalchemy import create_engine, text

    eng = create_engine(rig["DATABASE_URL"])
    try:
        with eng.begin() as c:
            c.execute(text("DELETE FROM upload_files WHERE filename = :f"), {"f": filename})
    finally:
        eng.dispose()


def timed():
    return time.monotonic()
