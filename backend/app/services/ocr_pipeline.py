"""
LLM / OCR-service meter-reading pipeline.

Pure processing logic (Ollama vision calls, OCR-service HTTP client, serial
matching) — no FastAPI or database dependencies, so it can be tested in
isolation.  HTTP orchestration lives in app.routers.ocr.

The EasyOCR engine itself no longer lives here — it moved to the standalone,
optional `ocr-service` container (see ocr-service/app/pipeline.py). This
module calls it over HTTP via _ocr_service_scan/_ocr_service_serial, the
same way it already calls the optional Ollama LLM service.
"""
import json
import logging
import re
from pathlib import Path

import httpx

from app.config import settings
from app.services import llm_client

logger = logging.getLogger(__name__)

# Allowed image MIME types (checked by magic bytes via `filetype` library)
_ALLOWED_IMAGE_MIMES = frozenset({
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/bmp",
    "image/tiff",
})

# Safe file extension derived from verified MIME type (never trust client-supplied extension)
_MIME_TO_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}

_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

# Outbound calls to the optional OCR service MUST be bounded (FR-011) so a
# stuck/unreachable service cannot degrade the rest of the application.
_OCR_SERVICE_TIMEOUT = 30.0


def _ocr_service_headers() -> dict[str, str] | None:
    if settings.ocr_api_key:
        return {"Authorization": f"Bearer {settings.ocr_api_key}"}
    return None


def _extract_exif_datetime(filepath: Path) -> str | None:
    """
    Extract the photo-taken datetime from EXIF metadata.

    Checks DateTimeOriginal (36867) and DateTimeDigitized (36868) in the
    Exif sub-IFD first, then falls back to DateTime (306) in the root IFD.

    Returns a string like ``"2025-12-24T14:30"`` (date + time) or
    ``"2025-12-24"`` (date only if the time portion is missing/short),
    or ``None`` when no EXIF date is found.
    """
    try:
        from PIL import Image as _PILImg
        with _PILImg.open(filepath) as _pimg:
            _exif = _pimg.getexif()
            _val = None
            # DateTimeOriginal / DateTimeDigitized live in the Exif sub-IFD
            _exif_ifd = _exif.get_ifd(0x8769)
            for _tag in (36867, 36868):  # DateTimeOriginal, DateTimeDigitized
                _val = _exif_ifd.get(_tag)
                if _val:
                    break
            # Fallback: DateTime in root IFD
            if not _val:
                _val = _exif.get(306)
            if _val:
                # EXIF format "YYYY:MM:DD HH:MM:SS" → "YYYY-MM-DDTHH:MM"
                if len(_val) >= 19:
                    return _val[:10].replace(':', '-') + 'T' + _val[11:16]
                return _val[:10].replace(':', '-')
    except Exception:  # nosec B110 - best-effort EXIF read; absence of a date must not fail the upload
        pass
    return None


def _format_hint_text(last_reading: float, meter_type: str | None) -> str:
    """Build a CONTEXT block for the LLM prompt based on the last known reading."""
    # (unit, display-pattern, decimal-digits-after-comma)
    _META: dict[str, tuple[str, str, int]] = {
        "electricity": ("kWh", "########,#", 1),
        "water":       ("m³",  "###,###",    3),
        "oil":         ("L",   "####",       0),
    }
    unit, pattern, dec_digits = _META.get(meter_type or "", ("", "", -1))

    # Show value exactly as it appears on the meter display (German comma notation)
    if dec_digits >= 0:
        display_val = f"{last_reading:.{dec_digits}f}".replace(".", ",")
    else:
        display_val = f"{last_reading:g}".replace(".", ",")

    lines = ["\nCONTEXT:"]
    val_line = f"- Previous reading as shown on display: {display_val}"
    if unit:
        val_line += f" {unit}"
    lines.append(val_line)
    if pattern:
        lines.append(f"- Display digit pattern: {pattern}  (# = one digit, comma = decimal separator)")
    if dec_digits > 0:
        lines.append(
            f"- The rightmost {dec_digits} digit(s) are the fractional part after the decimal comma."
            " Do NOT omit the comma."
        )
    elif dec_digits == 0:
        lines.append("- This meter has no decimal separator.")
    lines.append("- The new reading must be ≥ the previous reading.")
    return "\n".join(lines) + "\n"


_GATEWAY_JSON_LINE = '\nAnswer ONLY as JSON: {"reading": string|null, "serial": string|null}'
_GATEWAY_SERIAL_JSON_LINE = '\nAnswer ONLY as JSON: {"serial": string|null}'

# Ollama structured-output schemas (the gateway path sends no schema: with
# thinking off it returns empty answers; the text is parsed instead).
_READING_SCHEMA = {
    "type": "object",
    "properties": {
        "reading": {"type": ["string", "null"]},
        "serial": {"type": ["string", "null"]},
    },
    "required": ["reading", "serial"],
}
_SERIAL_SCHEMA = {
    "type": "object",
    "properties": {"serial": {"type": ["string", "null"]}},
    "required": ["serial"],
}


def _llm_fallback(
    filepath: Path,
    already_cropped: bool = False,
    last_reading: float | None = None,
    meter_type: str | None = None,
) -> tuple[str | None, str | None]:
    """
    Ask a local vision model (Ollama or the KI-Gateway, see llm_client) to read
    the meter display. Transport, model profile and prompt come from settings.
    Returns (reading, serial_number) — either value may be None on failure.
    On the gateway path llm_client.LocalModelUnavailable / GatewayConfigError
    propagate to the caller instead of being reported as "nothing read".
    """
    if not llm_client.llm_enabled():
        return None, None
    profile = llm_client.get_profile()

    try:
        # For already-cropped display images a small image is enough; full
        # uncropped photos need the profile's edge length so the display
        # (and tiny LCD decimal points) stay readable.
        # EXIF transpose: phone photos carry Orientation=6 (verified
        # 2026-07-16) — without it the model sees the meter sideways.
        max_side = llm_client.CROPPED_MAX_SIDE if already_cropped else profile.max_side
        b64 = llm_client.encode_image(llm_client.load_image(filepath), max_side)

        # Best Practice #4: image content is referenced first, then instructions
        prompt = llm_client.load_prompt(profile)
        if last_reading is not None:
            prompt += _format_hint_text(last_reading, meter_type)
        if llm_client.using_gateway():
            prompt += _GATEWAY_JSON_LINE
        text = llm_client.complete(b64, prompt, _READING_SCHEMA, profile, 180.0)

        # Parse JSON response from LLM
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r'\{[^}]+\}', text, re.DOTALL)
            try:
                data = json.loads(m.group()) if m else {}
            except json.JSONDecodeError:
                data = {}

        def _clean_reading(v: object) -> str | None:
            if not v or not isinstance(v, str):
                return None
            cleaned = re.sub(r"[^\d.,]", "", v).replace(",", ".").strip(".")
            # If the model returned multiple periods (e.g. "03.1009.9"),
            # only the last period can be the decimal separator — merge the rest.
            parts = cleaned.split(".")
            if len(parts) > 2:
                cleaned = "".join(parts[:-1]) + "." + parts[-1]
            # Separator-less output although the meter type prescribes
            # fractional digits: 7-segment decimal points are often invisible
            # on photos (verified 2026-07-16: e4b reads all 7 Holley drums
            # correctly but omits the invisible point). The display layout is
            # fixed per meter type, so insert the separator at the pattern
            # position. Only active on the hint-retry path (meter_type set).
            _EXPECTED_DEC = {"electricity": 1, "water": 3, "oil": 0}
            dec = _EXPECTED_DEC.get(meter_type or "")
            digits = re.sub(r"\D", "", cleaned)
            if "." not in cleaned and dec and len(digits) > dec:
                cleaned = digits[:-dec] + "." + digits[-dec:]
            # Separator present but at the wrong position (e.g. Holley read
            # as "03246.46" although kWh displays always show exactly 1
            # fractional digit): the drum layout is fixed per meter type, so
            # reposition deterministically. Digits are kept verbatim.
            elif "." in cleaned and dec is not None and len(digits) > dec:
                frac = len(cleaned.split(".", 1)[1])
                if frac != dec:
                    cleaned = (digits[:-dec] + "." + digits[-dec:]) if dec else digits
            if len(re.sub(r"\D", "", cleaned)) >= 3:
                return cleaned
            return None

        reading = _clean_reading(data.get("reading"))
        serial = _clean_serial_value(data.get("serial"))
        # Guard against the observed roller-meter failure mode: the model
        # returns the printed serial as the reading (verified 2026-07-16 on
        # Optiline: reading=24519788 = its serial). Better no value (client
        # asks the user / retry paths kick in) than a wrong stored reading.
        if reading and serial:
            r_digits = re.sub(r"\D", "", reading)
            s_digits = re.sub(r"\D", "", serial)
            if len(r_digits) >= 6 and r_digits == s_digits:
                logger.warning("LLM reading equals serial (%s) — discarding reading", serial)
                reading = None
        return reading, serial
    except llm_client.LLMError:
        # Gateway path: "model unavailable" / "bad config" must reach the
        # caller, never degrade into "nothing recognised".
        raise
    except Exception:
        logger.warning("LLM fallback failed for %s", filepath)
        return None, None


def _clean_serial_value(v: object) -> str | None:
    """Strip whitespace/hyphens from an LLM serial answer; None for null-ish."""
    if not v or not isinstance(v, str):
        return None
    s = re.sub(r'[\s\-]', '', v).strip()
    return None if not s or s.lower() == "null" else s


_SERIAL_ZOOM_PROMPT = (
    "This image is a cropped section of a utility meter.\n"
    "Find the serial number label (starting with Nr., S/N, Zähler-Nr., MSN,"
    " or printed next to a barcode) and transcribe it.\n"
    "- Include ALL leading zeros exactly as printed.\n"
    "- Only alphanumeric characters (A–Z, 0–9) — strip spaces and punctuation.\n"
    "- Ignore handwritten numbers and type/model designations.\n"
    "- If no serial label is visible in this crop, use null."
)


def _llm_serial_zoom(filepath: Path) -> list[str]:
    """
    Zoom-crop fallback pass for serial detection (CropVLM principle: global
    view first, high-resolution crops second).

    The full-photo pass downscales to 1500px, leaving label characters too
    small for reliable transcription. This pass crops overlapping
    native-resolution horizontal bands (top 45 %, middle 30–75 %, bottom
    55–100 %) so label characters retain ~2.4× more pixels, then runs a
    serial-only LLM query per band.

    Returns a deduplicated candidate list in band order. Bands without a
    label yield hallucination-prone answers (approval numbers, handwritten
    notes) — callers MUST only accept a candidate that matches a known
    meter serial, never store one blindly.
    """
    if not llm_client.llm_enabled():
        return []
    profile = llm_client.get_profile()
    try:
        img = llm_client.load_image(filepath)
        w, h = img.size
        bands = [
            img.crop((0, 0, w, int(h * 0.45))),
            img.crop((0, int(h * 0.30), w, int(h * 0.75))),
            img.crop((0, int(h * 0.55), w, h)),
        ]
        prompt = _SERIAL_ZOOM_PROMPT + (_GATEWAY_SERIAL_JSON_LINE if llm_client.using_gateway() else "")
        candidates: list[str] = []
        for band in bands:
            b64 = llm_client.encode_image(band, profile.max_side)
            raw = llm_client.complete(b64, prompt, _SERIAL_SCHEMA, profile, 90.0)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            s = _clean_serial_value(data.get("serial"))
            if s and s not in candidates:
                candidates.append(s)
        return candidates
    except llm_client.LLMError:
        raise
    except Exception:
        logger.warning("Serial zoom pass failed for %s", filepath)
        return []


def _normalize_serial(s: str) -> str:
    """Strip whitespace, hyphens, dots, slashes; uppercase for comparison."""
    return re.sub(r'[\s\-\.\/]', '', s).upper()


def _levenshtein(a: str, b: str) -> int:
    """Edit distance, O(len(a)*len(b)) DP with two rows."""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _match_serial_to_meters(serial: str, meters: list) -> tuple:
    """
    Match a detected serial string against a list of Meter objects.

    Returns (best_match_or_None, confidence_str, candidate_list)
    confidence: "exact" | "partial" | "none"
    """
    if not serial:
        return None, "none", []

    norm_detected = _normalize_serial(serial)
    exact: list = []
    partial: list = []

    for meter in meters:
        if not meter.serial_number:
            continue
        norm_meter = _normalize_serial(meter.serial_number)
        if norm_detected == norm_meter:
            exact.append(meter)
        elif norm_detected in norm_meter or norm_meter in norm_detected:
            partial.append(meter)
        else:
            # Fuzzy fallback: VLMs reliably drop single characters in long
            # zero-runs (verified 2026-07-16: e4b reads Holley label
            # "1HLY020026991" instead of "1HLY0200026991"). Allow edit
            # distance 1 for serials >= 6 chars, 2 for >= 10 chars.
            min_len = min(len(norm_detected), len(norm_meter))
            max_d = 2 if min_len >= 10 else (1 if min_len >= 6 else 0)
            if max_d and abs(len(norm_detected) - len(norm_meter)) <= max_d \
                    and _levenshtein(norm_detected, norm_meter) <= max_d:
                partial.append(meter)

    if len(exact) == 1:
        return exact[0], "exact", []
    elif exact:
        return exact[0], "partial", exact
    elif len(partial) == 1:
        return partial[0], "partial", []
    elif partial:
        return partial[0], "partial", partial
    else:
        return None, "none", []


def _matches_format(value_str: str | None, meter_type: str | None) -> bool:
    """
    Return True if the detected reading has the expected number of decimal digits
    for the given meter type.

    Expected decimal digit counts:
      electricity → 1  (e.g. 12345678.9 kWh)
      water       → 3  (e.g. 123.456 m³)
      oil         → 0  (e.g. 1234 L)
    """
    if not value_str or not meter_type:
        return True  # no info → cannot validate → don’t trigger retry
    _EXPECTED_DEC: dict[str, int] = {"electricity": 1, "water": 3, "oil": 0}
    expected = _EXPECTED_DEC.get(meter_type)
    if expected is None:
        return True
    normalized = value_str.replace(",", ".")
    if "." in normalized:
        frac = normalized.split(".", 1)[1]
        actual = len(frac)
    else:
        actual = 0
    return actual == expected


def _ocr_service_scan(filepath: Path, already_cropped: bool = False) -> dict:
    """
    Call the optional OCR service's POST /scan endpoint.

    Bounded to a 30s timeout (FR-011). Raises httpx.HTTPError (connect
    error, timeout, or a non-2xx status via raise_for_status()) on any
    failure — callers MUST NOT silently substitute a different engine
    (FR-005); they should surface a clear error to the caller instead.
    """
    with open(filepath, "rb") as f:
        resp = httpx.post(
            f"{settings.ocr_url}/scan",
            files={"file": (filepath.name, f, "application/octet-stream")},
            data={"already_cropped": "true" if already_cropped else "false"},
            headers=_ocr_service_headers(),
            timeout=_OCR_SERVICE_TIMEOUT,
        )
    resp.raise_for_status()
    body = resp.json()
    return {
        "raw_texts": body.get("raw_texts", []),
        "detected_value": body.get("detected_value"),
        "detected_serial": body.get("detected_serial"),
        "detection_method": body.get("detection_method", "ocr"),
    }


def _ocr_service_serial(filepath: Path) -> str | None:
    """
    Call the optional OCR service's POST /serial endpoint.

    Bounded to a 30s timeout (FR-011). Raises httpx.HTTPError on failure —
    same no-silent-fallback contract as _ocr_service_scan.
    """
    with open(filepath, "rb") as f:
        resp = httpx.post(
            f"{settings.ocr_url}/serial",
            files={"file": (filepath.name, f, "application/octet-stream")},
            headers=_ocr_service_headers(),
            timeout=_OCR_SERVICE_TIMEOUT,
        )
    resp.raise_for_status()
    return resp.json().get("detected_serial")
