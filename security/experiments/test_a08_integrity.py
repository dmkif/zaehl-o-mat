"""E-A08-001 (A08 Software/Data integrity): hostile or broken gateway / OCR-service responses.

The vision gateway (``gateway`` stand-in) and the OCR service (``ocr`` stand-in) are untrusted upstreams. Expected
(secure) result: the backend validates what it gets, bounds what it returns, never stores model output blindly, maps
upstream failure to the designed 502/503 with fixed texts, and does not leave orphaned data behind.

``engine=llm`` isolates the gateway path, ``engine=ocr`` the OCR-service path.
"""
from __future__ import annotations

import asyncio
import re
import time

import pytest

from sec_common import (API, bulk_scan, count_readings, get_client, list_files, make_world, parse_sse, png_ok, scan,
                        set_mode, upload_dir)

pytestmark = pytest.mark.security

VALUE_RE = re.compile(r"^\d{1,12}([.,]\d{1,6})?$")
SERIAL_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")
ALLOWED_KEYS = {"image_path", "serial_image_path", "image_hash", "raw_texts", "detected_value", "detected_serial",
                "detection_method"}
MAX_BODY = 64 * 1024
MSG_UNAVAILABLE = "Lokales Modell nicht erreichbar, bitte später erneut versuchen"
MSG_CONFIG = "Konfigurationsfehler beim KI-Gateway"


@pytest.fixture
async def admin(client):
    return await get_client(client, "admin")


@pytest.fixture
def updir(rig):
    return upload_dir(rig)


def validate_result(j: dict) -> list[str]:
    """Shape/size validation every scan response must satisfy no matter what the upstream sent."""
    p = []
    if not isinstance(j, dict):
        return [f"response is {type(j).__name__}, not an object"]
    if set(j) - ALLOWED_KEYS:
        p.append(f"unexpected keys {sorted(set(j) - ALLOWED_KEYS)}")
    v, s = j.get("detected_value"), j.get("detected_serial")
    if v is not None and not (isinstance(v, str) and VALUE_RE.match(v)):
        p.append(f"detected_value not a bounded number: {str(v)[:60]!r} (len {len(str(v))})")
    if s is not None and not (isinstance(s, str) and SERIAL_RE.match(s)):
        p.append(f"detected_serial not a bounded alphanumeric: {str(s)[:60]!r} (len {len(str(s))})")
    if j.get("detection_method") not in (None, "ocr", "llm"):
        p.append(f"detection_method {str(j.get('detection_method'))[:60]!r}")
    rt = j.get("raw_texts", [])
    if not isinstance(rt, list) or len(rt) > 50 or any(not isinstance(x, str) or len(x) > 1000 for x in rt):
        p.append(f"raw_texts unbounded/wrong type: {type(rt).__name__} len={len(rt) if hasattr(rt, '__len__') else '?'}")
    return p


def check_bounded(resp, label: str) -> list[str]:
    p = []
    if len(resp.content) > MAX_BODY:
        p.append(f"{label}: response body {len(resp.content)} bytes (> {MAX_BODY}) -- upstream size passed through")
    return p


def digits(x) -> str:
    return re.sub(r"\D", "", str(x) if x is not None else "")


# ----------------------------------------------------------------------------------------- gateway path
HOSTILE_GATEWAY = ["hostile_huge", "hostile_nested", "hostile_types", "reading_equals_serial", "garbage"]


async def test_E_A08_001_gateway_ok_control(admin, gateway):
    """E-A08-001 control: with a well-formed gateway answer the pipeline returns exactly the model's reading/serial
    (proves the hostile cases below are not failing for unrelated reasons)."""
    await set_mode(gateway, "ok")
    r = await scan(admin, png_ok(seed=51), engine="llm")
    assert r.status_code == 200, r.text[:300]
    j = r.json()
    assert j["detected_value"] == "12345.6" and j["detected_serial"] == "1234567", j
    assert validate_result(j) == []


@pytest.mark.parametrize("mode", HOSTILE_GATEWAY)
async def test_E_A08_001_gateway_hostile_response_validated(admin, gateway, mode):
    """E-A08-001: gateway answers `hostile_huge` (5 MB digit strings), `hostile_nested` (5000-deep JSON),
    `hostile_types` (dict/int where strings are expected), `reading_equals_serial`, `garbage` (non-JSON).

    Secure: 200 with validated, bounded fields (number-like value <= 12 digits, alphanumeric serial <= 64) or the
    designed 502/503; never 500; response <= 64 KB; reading never equal to serial; completes in bounded time.
    """
    await set_mode(gateway, mode)
    t0 = time.monotonic()
    r = await scan(admin, png_ok(seed=52), engine="llm")
    dt = time.monotonic() - t0
    problems = []
    if r.status_code not in (200, 502, 503):
        problems.append(f"status {r.status_code}: {r.text[:200]!r}")
    problems += check_bounded(r, mode)
    if r.status_code == 200:
        j = r.json()
        problems += validate_result(j)
        if mode == "reading_equals_serial":
            if j.get("detected_value") and j.get("detected_serial") and digits(j["detected_value"]) == digits(j["detected_serial"]):
                problems.append("reading equals serial -- serial returned as the reading")
            if j.get("detected_value") is not None:
                problems.append(f"reading kept although equal to the serial: {j['detected_value']!r}")
        if mode == "hostile_types" and (j.get("detected_value") is not None):
            problems.append(f"dict-typed reading produced value {j['detected_value']!r}")
        if mode == "garbage" and "garbage" in r.text.lower():
            problems.append("upstream garbage echoed to the client")
    if dt > 60:
        problems.append(f"took {dt:.0f}s")
    assert not problems, "\n".join(problems)


async def test_E_A08_001_gateway_unavailable_maps_to_503(admin, gateway):
    """E-A08-001: gateway HTTP 503 -> client gets 503 with the fixed user-safe text, no upstream detail."""
    await set_mode(gateway, "down")
    r = await scan(admin, png_ok(seed=53), engine="llm")
    assert r.status_code == 503, r.text[:300]
    assert r.json()["detail"] == MSG_UNAVAILABLE
    assert "service unavailable" not in r.text.lower() and "127.0.0.1" not in r.text


async def test_E_A08_001_gateway_unauthorized_maps_to_502(admin, gateway):
    """E-A08-001: gateway 401 (bad API key) -> 502 with the fixed config-error text; the key and upstream body are
    not leaked to the client."""
    await set_mode(gateway, "unauthorized")
    r = await scan(admin, png_ok(seed=54), engine="llm")
    assert r.status_code == 502, r.text[:300]
    assert r.json()["detail"] == MSG_CONFIG
    assert "bearer" not in r.text.lower() and "unauthorized" not in r.text.lower() and "127.0.0.1" not in r.text


async def test_E_A08_001_gateway_slow_does_not_block_server(admin, gateway):
    """E-A08-001: a slow (8 s) gateway answer is waited for within the configured bound, and meanwhile the backend keeps
    answering other requests quickly (blocking call must not stall the event loop)."""
    await set_mode(gateway, "slow", delay_s=8)
    task = asyncio.create_task(scan(admin, png_ok(seed=55), engine="llm", timeout=120))
    await asyncio.sleep(1.5)
    lat = []
    while not task.done():
        t0 = time.monotonic()
        h = await admin.get(f"{API}/health/live", timeout=10)
        lat.append(time.monotonic() - t0)
        assert h.status_code == 200
        await asyncio.sleep(1.0)
    r = await task
    assert r.status_code == 200, r.text[:300]
    assert max(lat) < 2.0, f"liveness blocked up to {max(lat):.1f}s while a scan waited for the gateway"


# ----------------------------------------------------------------------------------------- OCR-service path
HOSTILE_OCR = ["hostile_huge", "hostile_nested", "hostile_types", "reading_equals_serial", "garbage"]


async def test_E_A08_001_ocr_ok_control(admin, ocr):
    """E-A08-001 control: well-formed OCR-service answer is passed through unchanged."""
    await set_mode(ocr, "ok")
    r = await scan(admin, png_ok(seed=61), engine="ocr")
    assert r.status_code == 200, r.text[:300]
    j = r.json()
    assert j["detected_value"] == "12345.6" and j["detected_serial"] == "1234567", j


@pytest.mark.parametrize("mode", HOSTILE_OCR)
async def test_E_A08_001_ocr_hostile_response_validated(admin, ocr, mode):
    """E-A08-001: OCR service answers hostile payloads (5 MB strings x3 in raw_texts/value/serial, 5000-deep JSON,
    wrong types, reading == serial, non-JSON 200).

    Secure: validated + bounded 200, or designed 502/503; never an unhandled 500; response <= 64 KB; no reading equal
    to the serial (same guard as on the LLM path).
    """
    await set_mode(ocr, mode)
    t0 = time.monotonic()
    r = await scan(admin, png_ok(seed=62), engine="ocr")
    dt = time.monotonic() - t0
    problems = []
    if r.status_code not in (200, 502, 503):
        problems.append(f"status {r.status_code}: {r.text[:160]!r}")
    problems += check_bounded(r, mode)
    if r.status_code == 200:
        j = r.json()
        problems += validate_result(j)
        if j.get("detected_value") and j.get("detected_serial") and digits(j["detected_value"]) == digits(j["detected_serial"]):
            problems.append("OCR path returned reading == serial (the LLM-path guard is missing here)")
    if dt > 60:
        problems.append(f"took {dt:.0f}s")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("mode", ["down", "unauthorized"])
async def test_E_A08_001_ocr_unavailable_maps_to_503(admin, ocr, mode):
    """E-A08-001: OCR service down (503) / rejecting our key (401) -> 503 'OCR service is unreachable' (explicit
    engine=ocr never falls back silently); no upstream body or URL in the answer."""
    await set_mode(ocr, mode)
    r = await scan(admin, png_ok(seed=63), engine="ocr")
    assert r.status_code == 503, r.text[:300]
    assert r.json()["detail"] == "OCR service is unreachable"
    assert "127.0.0.1" not in r.text and "unauthorized" not in r.text.lower()


async def test_E_A08_001_ocr_slow_bounded_wait(admin, ocr):
    """E-A08-001: OCR service that answers after 38 s (client timeout 30 s) -> 503 within ~40 s, not an unbounded wait."""
    await set_mode(ocr, "slow", delay_s=38)
    t0 = time.monotonic()
    r = await scan(admin, png_ok(seed=64), engine="ocr", timeout=120)
    dt = time.monotonic() - t0
    assert r.status_code == 503, r.text[:300]
    assert dt < 42, f"waited {dt:.0f}s for a 30s-bounded upstream"


# ----------------------------------------------------------------------------------------- storage / side effects
async def test_E_A08_001_hostile_output_never_stored(admin, gateway, ocr, updir):
    """E-A08-001: after meter-bound scans against hostile gateway and OCR service (auto engine) nothing from the
    model/OCR output is written to readings or meters, the meter data is unchanged and only the uploaded images exist
    on disk."""
    w = await make_world(admin, "a08-store")
    meter_url = f"{API}/properties/{w['property_id']}/meters/{w['meter_id']}"
    meter_before = (await admin.get(meter_url)).json()
    readings_before = await count_readings(admin, w)
    files_before = list_files(updir)
    ok_scans = 0
    for gmode, omode in (("hostile_huge", "hostile_huge"), ("hostile_types", "hostile_nested"),
                         ("reading_equals_serial", "reading_equals_serial")):
        await set_mode(gateway, gmode)
        await set_mode(ocr, omode)
        r = await scan(admin, png_ok(seed=70), engine="auto", meter_id=w["meter_id"])
        assert r.status_code in (200, 502, 503), f"{gmode}/{omode}: {r.status_code} {r.text[:200]!r}"
        ok_scans += r.status_code == 200
    assert await count_readings(admin, w) == readings_before, "a reading was created from scan output"
    assert (await admin.get(meter_url)).json() == meter_before, "meter changed by scan output"
    new = list_files(updir) - files_before
    assert len(new) <= 3, f"more files than uploads: {sorted(new)}"
    blob = (await admin.get(f"{meter_url}/readings/")).text + (await admin.get(meter_url)).text
    assert "9999999" not in blob and len(blob) < 4000


@pytest.mark.parametrize("which", ["gateway_down", "gateway_unauthorized"])
async def test_E_A08_001_failed_scan_leaves_no_orphan_file(admin, gateway, updir, which):
    """E-A08-001 (A10): a scan that fails because the upstream is down/misconfigured (503/502) must not leave the
    uploaded image behind -- otherwise repeated failing scans fill the disk (up to 10 MB each, 20/min)."""
    await set_mode(gateway, "down" if which == "gateway_down" else "unauthorized")
    before = list_files(updir)
    r = await scan(admin, png_ok(seed=71), engine="llm")
    assert r.status_code in (502, 503), r.text[:200]
    assert list_files(updir) == before, f"orphaned upload after failed scan: {sorted(list_files(updir) - before)}"


async def test_E_A08_001_bulk_scan_hostile_stream_wellformed(admin, gateway, ocr):
    """E-A08-001: bulk-scan (SSE) with a hostile gateway (huge/nested) and a hostile OCR service keeps the stream
    well formed (n items + `complete`), each item's detected fields validated and bounded, total stream bounded."""
    cases = [("llm", "gateway", "hostile_huge"), ("llm", "gateway", "hostile_nested"),
             ("ocr", "ocr", "hostile_types"), ("ocr", "ocr", "hostile_huge")]
    problems = []
    for engine, which, mode in cases:
        await set_mode(gateway if which == "gateway" else ocr, mode)
        r = await bulk_scan(admin, [("a.png", png_ok(seed=80), "image/png"), ("b.png", png_ok(seed=81), "image/png")],
                            engine=engine)
        if r.status_code != 200:
            problems.append(f"{which}/{mode}: status {r.status_code}")
            continue
        if len(r.content) > 256 * 1024:
            problems.append(f"{which}/{mode}: stream {len(r.content)} bytes")
        try:
            ev = parse_sse(r.text)
        except Exception as e:  # malformed stream is a finding
            problems.append(f"{which}/{mode}: malformed SSE: {e}")
            continue
        items = [d for n, d in ev if n != "complete"]
        if len(items) != 2 or ev[-1][0] != "complete":
            problems.append(f"{which}/{mode}: {len(items)} items, last event {ev[-1][0]}")
        for d in items:
            for k in ("detected_value", "detected_serial"):
                v = d.get(k)
                rx = VALUE_RE if k == "detected_value" else SERIAL_RE
                if v is not None and not (isinstance(v, str) and rx.match(v)):
                    problems.append(f"{which}/{mode}: {k}={str(v)[:50]!r} (len {len(str(v))})")
    assert not problems, "\n".join(problems)


async def test_E_A08_001_model_serial_cannot_reach_foreign_meters(client, seed, gateway):
    """E-A08-001 (A01): the serial returned by the model is only matched against meters the caller may read.
    Control: A's own serial -> A's meter matched. Attack: the model 'reads' B's serial -> nothing of B (ids, names)
    appears in matches, candidates or the final meter list of user A."""
    user_a = await get_client(client, "user_a")
    a_meter = seed["meters"]["meter_a_water"]
    b_ids = [m["id"] for k, m in seed["meters"].items() if k.startswith("meter_b")]

    await set_mode(gateway, "ok", serial=a_meter["serial_number"], reading="100.123")
    r = await bulk_scan(user_a, [("ctl.png", png_ok(seed=90), "image/png")], engine="llm")
    assert r.status_code == 200, r.text[:300]
    item = [d for n, d in parse_sse(r.text) if n != "complete"][0]
    assert item["matched_meter"] and item["matched_meter"]["id"] == a_meter["id"], f"control failed: {item}"

    await set_mode(gateway, "ok", serial=seed["meters"]["meter_b_water"]["serial_number"], reading="100.123")
    r = await bulk_scan(user_a, [("atk.png", png_ok(seed=91), "image/png")], engine="llm")
    assert r.status_code == 200, r.text[:300]
    leaked = [i for i in b_ids if i in r.text]
    assert not leaked, f"foreign meter ids in user A's scan result: {leaked}"
    assert "rig water b" not in r.text.lower() and "rig water2 b" not in r.text.lower()
    item = [d for n, d in parse_sse(r.text) if n != "complete"][0]
    a_ids = [m["id"] for k, m in seed["meters"].items() if k.startswith("meter_a")]
    if item["matched_meter"] is not None:
        assert item["matched_meter"]["id"] in a_ids, f"matched a meter user A cannot access: {item['matched_meter']}"
