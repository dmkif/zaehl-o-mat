import asyncio
import base64
import io
import json
import logging
import re
import uuid
from pathlib import Path

import filetype
import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models import User, Meter, Property, PropertyUser, UserRole

router = APIRouter(prefix="/ocr", tags=["ocr"])

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

# EasyOCR reader is lazily initialized to avoid slow startup.
# A lock ensures only one thread initialises or uses the reader at a time.
_reader = None
_reader_lock = asyncio.Lock()
_easyocr_available: bool | None = None  # cached import check


def _is_easyocr_available() -> bool:
    """Return True if the easyocr package is importable (cached)."""
    global _easyocr_available
    if _easyocr_available is None:
        try:
            import easyocr  # type: ignore  # noqa: F401
            _easyocr_available = True
        except ImportError:
            _easyocr_available = False
    return _easyocr_available


def _get_reader():
    global _reader
    if _reader is None:
        if not _is_easyocr_available():
            return None
        import easyocr  # type: ignore
        _reader = easyocr.Reader(["de", "en"], gpu=True)
    return _reader


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
    except Exception:
        pass
    return None


def _preprocess_image(filepath: Path, already_cropped: bool = False) -> tuple:
    """
    Pre-process a utility-meter photo for EasyOCR.  Handles three common
    meter display types found in German households:

    - Red-LED backlit LCD (Holley DTS541 etc.): bright red background, dark segments
    - Natural-light reflective LCD (PROTEUS, ITron): bright gray background, dark digits
    - Dark-background LCD (HYDRUS/Diehl, Kamstrup): dark background, light digits

    Pipeline:
    1. Auto-detect the display strip using Canny edges on a blurred grayscale.
       Sigma ≈ 1.5 % of image height suppresses fine label text while preserving
       large (~5 % image height) LCD digit edges.
    2. Extract the red channel from the detected crop (maximises contrast for
       red-LED meters; for others it is an approximate grayscale and still works).
    3. Apply CLAHE (tight 3×3 tiles) to enhance local contrast.
    4. If the result is predominantly dark (dark-background display), invert it
       so that EasyOCR always receives dark text on a bright background.
    5. Denoise + 4× upscale + light sharpen.

    Returns (proc_path, (proc_width, proc_height)).
    """
    import cv2
    import numpy as np
    from PIL import Image

    img = cv2.imread(str(filepath))
    if img is None:
        raise ValueError(f"Cannot read image: {filepath}")

    h, w = img.shape[:2]

    # ── Step 1: Auto-detect display strip ───────────────────────────────────
    # Skipped when already_cropped=True: the user already selected the meter
    # display area with the crop UI, so we use the full image as-is.
    if already_cropped:
        display = img
        dh, dw = display.shape[:2]
    else:
        # Blur sigma ≈ 1.5 % of image height: large enough to erase fine print and
        # barcodes (< 0.5 % feature size) but small enough to keep LCD digit edges
        # (typically ~5 % of image height).
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        sigma = max(5.0, h * 0.015)
        blurred_detect = cv2.GaussianBlur(gray, (0, 0), sigma)
        edges = cv2.Canny(blurred_detect, 30, 100)

        strip_h = max(20, h // 20)   # ≈ 5 % of height per scanning strip
        stride  = max(10, h // 50)   # ≈ 2 % stride (50 % overlap between strips)
        best_score = -1.0
        best_y1 = int(h * 0.35)      # safe default

        # Search only in 10–70 % of image height (display is never at top/bottom)
        for y0 in range(int(h * 0.10), int(h * 0.70), stride):
            band = edges[y0:min(h, y0 + strip_h), int(w * 0.10):int(w * 0.90)]
            if band.size == 0:
                continue
            score = float(np.mean(band))  # mean edge density after de-noising blur
            if score > best_score:
                best_score, best_y1 = score, y0

        # Expand winning strip by ±1 strip to avoid clipping digit ascenders/descenders
        crop_y1 = max(0, best_y1 - strip_h)
        crop_y2 = min(h, best_y1 + 2 * strip_h)
        crop_x1 = int(w * 0.10)
        crop_x2 = int(w * 0.90)
        display = img[crop_y1:crop_y2, crop_x1:crop_x2]
        dh, dw = display.shape[:2]
        if dh < 10 or dw < 20:
            display = img   # fallback: use full image
            dh, dw = display.shape[:2]

    # ── Step 2: Red-channel CLAHE ────────────────────────────────────────────
    r_ch = display[:, :, 2]  # OpenCV BGR → index 2 = red
    clahe = cv2.createCLAHE(clipLimit=10.0, tileGridSize=(3, 3))
    enhanced = clahe.apply(r_ch)

    # ── Step 3: Invert dark-background displays ──────────────────────────────
    # HYDRUS / Diehl-type meters show bright white digits on a dark LCD.
    # After red-channel CLAHE those pixels are bright on dark (inverted for OCR).
    # Threshold: overall mean < 90 ≈ predominantly dark crop → invert.
    if float(np.mean(enhanced)) < 90:
        enhanced = cv2.bitwise_not(enhanced)

    # ── Step 4: Denoise + 4× upscale + light sharpen ────────────────────────
    denoised = cv2.fastNlMeansDenoising(enhanced, h=12)
    scale = 4
    upscaled = cv2.resize(denoised, (dw * scale, dh * scale), interpolation=cv2.INTER_CUBIC)
    k = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    sharp = cv2.filter2D(upscaled, -1, k)

    proc_path = filepath.with_suffix(".proc.jpg")
    Image.fromarray(sharp).save(proc_path, quality=95)
    proc_size = (sharp.shape[1], sharp.shape[0])  # (width, height) — PIL convention
    return proc_path, proc_size


# Transliteration table for common 7-segment LCD misreads.
# These arise because EasyOCR was not trained on 7-segment fonts.
_SEG7_SUBS = str.maketrans("JODIlBGT", "00011867")


def _fix_seven_segment(text: str) -> str:
    """
    Apply 7-segment LCD character substitutions and remove intra-digit spaces.

    Examples:
      "J309 735 Wn" → "0309735"   (J=0, spaces stripped, unit suffix dropped)
      "0305 735"    → "0305735"
    """
    text = text.translate(_SEG7_SUBS)
    # Remove spaces that EasyOCR inserts between consecutive digit characters
    # (7-segment display segments are often separated by a small gap)
    text = re.sub(r'(?<=\d) +(?=\d)', '', text)
    return text


def _extract_numeric(results: list, img_size: tuple = (0, 0)) -> str | None:
    """
    Pick the best meter-reading candidate from EasyOCR detail results.

    Each result is a (bbox, text, confidence) tuple.
    Scoring rules:
    - Base score  = OCR confidence (0..1)
    - +0.3 for 4-8 digit count      → typical meter display range
    - +0.2 for exactly one decimal   → rollover/drum display with decimal
    - -0.4 for >8 digits             → serial number territory
    - -0.5 for date-like d.d.dd      → e.g. installation date 25.7.18
    - +0.1/+0.4 bbox height bonus    → display digits are physically larger
                                       than label or serial-number text
    """

    img_h = img_size[1] if img_size else 0
    candidates = []

    for (bbox, text, conf) in results:
        # Keep only texts that are mostly numeric after stripping non-digit/. chars
        cleaned = re.sub(r"[^\d.,]", "", text)
        if not cleaned:
            continue
        # Require at least half the original text to be digits (avoids pure label debris)
        digits_in_orig = sum(c.isdigit() for c in text)
        if len(text) > 0 and digits_in_orig / len(text) < 0.4:
            continue
        cleaned = cleaned.replace(",", ".").strip(".")
        if not cleaned:
            continue

        dots = cleaned.count(".")
        if dots > 1:
            # Multiple dots → date-like pattern (e.g. 25.7.18) → discard
            continue

        digits = re.sub(r"\D", "", cleaned)
        if len(digits) < 3:
            continue

        score = conf

        if len(digits) == 3:
            score -= 0.2  # years (202x), 3-digit codes → unlikely meter reading
        elif 4 <= len(digits) <= 8:
            score += 0.3
        elif len(digits) > 8:
            score -= 0.4

        if dots == 1:
            score += 0.2

        # Bounding-box height bonus: display digits span a larger fraction of
        # the image height than handwritten labels or serial-number segments.
        # bbox is [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
        if img_h > 0 and bbox:
            try:
                ys = [p[1] for p in bbox]
                h_ratio = (max(ys) - min(ys)) / img_h
                if h_ratio > 0.06:    # very large text → almost certainly the display
                    score += 0.4
                elif h_ratio > 0.025: # medium text → possible display
                    score += 0.1
                # small text (labels, fine print) gets no bonus
            except (TypeError, IndexError):
                pass

        candidates.append((score, cleaned))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _llm_fallback(filepath: Path, already_cropped: bool = False) -> tuple[str | None, str | None]:
    """
    Ask a local Ollama vision model to read the meter display when EasyOCR fails.
    Model and URL are read from settings (config.py).
    Returns (reading, serial_number) — either value may be None on failure.
    """
    ollama_url = settings.ollama_url
    if not ollama_url:
        return None, None
    model = settings.ollama_model

    try:
        # For already-cropped display images, 800px is enough.
        # For full uncropped meter photos (bulk upload), use 1500px so the
        # meter display area remains large enough for the model to read.
        from PIL import Image
        img = Image.open(filepath)
        max_side = 800 if already_cropped else 1500
        if max(img.size) > max_side:
            scale = max_side / max(img.size)
            img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode()

        resp = httpx.post(
            f"{ollama_url}/api/generate",
            json={
                "model": model,
                "prompt": (
                    "Analyze the utility meter in this photo.\n"
                    "\n"
                    'Return ONLY valid JSON: {"reading": "VALUE", "serial": "VALUE"}\n'
                    "\n"
                    "READING:\n"
                    "- The main consumption counter on the digital display.\n"
                    "- Read ALL digit positions from left to right, including leading zeros.\n"
                    "- Include the decimal point if present.\n"
                    "- Do NOT skip any drums or windows, even if dim or partially rotated.\n"
                    "- Ignore handwritten numbers, stickers, or annotations.\n"
                    "- No spaces, no units, no thousands separators.\n"
                    "\n"
                    "SERIAL:\n"
                    "- The serial number or device ID printed on the meter label.\n"
                    "- Look for labels like Nr., S/N, Zähler-Nr., Eigentum, or similar.\n"
                    "- Include ALL leading zeros exactly as printed.\n"
                    "- Only alphanumeric characters, no spaces or punctuation.\n"
                    "\n"
                    "If a field is unreadable, use null."
                ),
                "images": [b64],
                "stream": False,
                "format": "json",
                "think": False,
                "options": {"temperature": 0, "num_ctx": 8192},
            },
            timeout=180.0,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "").strip()

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
            if len(re.sub(r"\D", "", cleaned)) >= 3:
                return cleaned
            return None

        def _clean_serial(v: object) -> str | None:
            if not v or not isinstance(v, str):
                return None
            s = re.sub(r'[\s\-]', '', v).strip()
            return None if not s or s.lower() == "null" else s

        return _clean_reading(data.get("reading")), _clean_serial(data.get("serial"))
    except Exception:
        logger.warning("LLM fallback failed for %s", filepath)
        return None, None


def _normalize_serial(s: str) -> str:
    """Strip whitespace, hyphens, dots, slashes; uppercase for comparison."""
    return re.sub(r'[\s\-\.\/]', '', s).upper()


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


def _meter_to_dict(meter) -> dict:
    return {
        "id": str(meter.id),
        "property_id": str(meter.property_id),
        "name": meter.name,
        "serial_number": meter.serial_number,
        "meter_type": meter.meter_type.value,
        "unit": meter.unit.value,
    }


def _extract_serial_sync(filepath: Path) -> str | None:
    """
    Run OCR on a user-cropped serial number area.

    No LCD-specific preprocessing — the user has already isolated the serial label.
    Pipeline: grayscale → CLAHE → upscale → sharpen.
    Returns the best alphanumeric candidate, or None.
    """
    import cv2
    import numpy as np
    from PIL import Image

    img = cv2.imread(str(filepath))
    if img is None:
        return None

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4))
    enhanced = clahe.apply(gray)

    h, w = enhanced.shape[:2]
    upscaled = cv2.resize(enhanced, (w * 3, h * 3), interpolation=cv2.INTER_CUBIC)
    k = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    sharpened = cv2.filter2D(upscaled, -1, k)

    proc_path = filepath.with_suffix(".serial_proc.jpg")
    Image.fromarray(sharpened).save(proc_path, quality=95)

    try:
        reader = _get_reader()
        results = reader.readtext(
            str(proc_path),
            detail=1,
            paragraph=False,
            low_text=0.3,
            text_threshold=0.4,
            width_ths=0.9,
        )
    except Exception:
        return None
    finally:
        proc_path.unlink(missing_ok=True)

    candidates = []
    for (_, text, conf) in results:
        cleaned = re.sub(r'\s+', '', text)
        alnum_ratio = sum(c.isalnum() for c in cleaned) / max(len(cleaned), 1)
        if len(cleaned) >= 4 and alnum_ratio >= 0.5:
            candidates.append((conf, cleaned))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _run_ocr_on_file_sync(filepath: Path, already_cropped: bool = False) -> dict:
    """Synchronous OCR (EasyOCR only) — called from asyncio.to_thread."""
    proc_path = None
    img_size = (0, 0)
    try:
        reader = _get_reader()
        proc_path, img_size = _preprocess_image(filepath, already_cropped=already_cropped)
        results = reader.readtext(
            str(proc_path),
            detail=1,
            paragraph=False,
            # Parameters tuned for 7-segment LCD meter displays:
            # low_text=0.3  → detect faint/partial segments
            # text_threshold=0.5 → reduce false positives without missing digits
            # link_threshold=0.2 → join adjacent digit segments into one text box
            # width_ths=0.9 → merge horizontally adjacent text boxes (full number)
            low_text=0.3,
            text_threshold=0.5,
            link_threshold=0.2,
            width_ths=0.9,
        )
    except Exception as exc:
        logger.error("OCR failed: %s", exc)
        raise RuntimeError(f"OCR processing failed: {exc}") from exc
    finally:
        if proc_path and proc_path.exists():
            proc_path.unlink(missing_ok=True)

    # Apply 7-segment substitutions before scoring; keep raw text in response
    corrected_results = [
        (bbox, _fix_seven_segment(text), conf) for (bbox, text, conf) in results
    ]
    raw_texts = [
        {"text": text, "conf": round(float(conf), 3)}
        for (_, text, conf) in corrected_results
    ]
    detected = _extract_numeric(corrected_results, img_size=img_size)
    return {"raw_texts": raw_texts, "detected_value": detected, "detected_serial": None, "detection_method": "ocr"}


async def _run_ocr_on_file(
    filepath: Path,
    engine: str = "auto",
    already_cropped: bool = False,
) -> dict:
    """
    Run OCR/LLM pipeline on an existing image file.

    engine:
      "auto"  — try LLM first (if OLLAMA_URL is set), fall back to EasyOCR
      "ocr"   — EasyOCR only, skip LLM entirely
      "llm"   — Ollama only; raises HTTP 503 when OLLAMA_URL is not configured
    already_cropped:
      When True, skips the auto-strip-detection step in _preprocess_image
      (the user already isolated the display area via the crop UI).
    """
    if engine == "llm":
        if not settings.ollama_url:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Ollama engine requested but OLLAMA_URL is not configured",
            )
        detected, detected_serial = await asyncio.to_thread(_llm_fallback, filepath, already_cropped)
        return {"raw_texts": [], "detected_value": detected, "detected_serial": detected_serial, "detection_method": "llm"}

    if engine == "ocr":
        if not _is_easyocr_available():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="EasyOCR engine requested but easyocr is not installed",
            )
        async with _reader_lock:
            try:
                return await asyncio.to_thread(_run_ocr_on_file_sync, filepath, already_cropped)
            except RuntimeError as exc:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    # engine == "auto": LLM first (if available), fall back to EasyOCR
    detected = None
    detected_serial = None
    if settings.ollama_url:
        detected, detected_serial = await asyncio.to_thread(_llm_fallback, filepath, already_cropped)

    if detected is None and _is_easyocr_available():
        async with _reader_lock:
            try:
                return await asyncio.to_thread(_run_ocr_on_file_sync, filepath, already_cropped)
            except RuntimeError as exc:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return {"raw_texts": [], "detected_value": detected, "detected_serial": detected_serial, "detection_method": "llm" if detected else None}


@router.post("/rescan/{reading_id}")
async def rescan_reading(
    reading_id: int,
    engine: str = Query("auto", pattern="^(auto|ocr|llm)$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Re-run OCR/LLM on the image already stored for an existing reading.
    The reading record is not modified — the client decides whether to update the value.
    """
    from app.models import Reading, Meter, PropertyUser
    reading = db.query(Reading).filter(Reading.id == reading_id).first()
    if not reading:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reading not found")

    # Verify the caller has access to the meter this reading belongs to
    meter = db.query(Meter).filter(Meter.id == reading.meter_id).first()
    if not meter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meter not found")

    if current_user.role not in (UserRole.superadmin, UserRole.admin):
        assoc = (
            db.query(PropertyUser)
            .filter(
                PropertyUser.property_id == meter.property_id,
                PropertyUser.user_id == current_user.id,
            )
            .first()
        )
        if not assoc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    if not reading.image_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reading has no associated image")

    filepath = Path(settings.upload_path) / Path(reading.image_path).name
    if not filepath.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image file not found on disk")

    result = await _run_ocr_on_file(filepath, engine=engine, already_cropped=True)

    # If a serial crop is stored and OCR mode is used, also detect the serial number
    if result.get("detected_serial") is None and reading.serial_image_path and engine in ("ocr", "auto"):
        serial_filepath = Path(settings.upload_path) / Path(reading.serial_image_path).name
        if serial_filepath.exists():
            async with _reader_lock:
                detected_serial = await asyncio.to_thread(_extract_serial_sync, serial_filepath)
            result["detected_serial"] = detected_serial

    return {"image_path": reading.image_path, **result}


@router.post("/scan")
async def scan_meter(
    file: UploadFile = File(...),
    serial_file: UploadFile | None = File(None),
    meter_id: uuid.UUID | None = None,
    engine: str = Form("auto", pattern="^(auto|ocr|llm)$"),
    already_cropped: bool = Form(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Upload a meter photo and extract the reading via OCR.
    Optionally also upload a second crop of the serial number area (serial_file)
    for dedicated serial number OCR.
    Returns the detected numeric value (string) for the client to confirm before saving.
    The image is also saved so it can be referenced when creating a Reading.
    """
    # Validate file type
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must be an image")

    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:  # 10 MB limit
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Image too large (max 10 MB)")

    # Magic-bytes validation: distrust the client-supplied Content-Type
    detected_mime = filetype.guess_mime(contents)
    if detected_mime not in _ALLOWED_IMAGE_MIMES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must be a supported image (JPEG, PNG, WEBP, GIF, BMP, TIFF)")

    # Validate optional serial file
    serial_contents: bytes | None = None
    if serial_file is not None:
        if not serial_file.content_type or not serial_file.content_type.startswith("image/"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Serial file must be an image")
        serial_contents = await serial_file.read()
        if len(serial_contents) > 10 * 1024 * 1024:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Serial image too large (max 10 MB)")
        serial_detected_mime = filetype.guess_mime(serial_contents)
        if serial_detected_mime not in _ALLOWED_IMAGE_MIMES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Serial file must be a supported image")

    # Validate that the requesting user has access to the given meter
    if meter_id is not None:
        meter = db.query(Meter).filter(Meter.id == meter_id).first()
        if not meter:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meter not found")
        if current_user.role not in (UserRole.superadmin, UserRole.admin):
            assoc = (
                db.query(PropertyUser)
                .filter(
                    PropertyUser.property_id == meter.property_id,
                    PropertyUser.user_id == current_user.id,
                )
                .first()
            )
            if not assoc:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    # Save display crop to disk
    upload_dir = Path(settings.upload_path)
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4()}{Path(file.filename or 'img.jpg').suffix.lower() or '.jpg'}"
    filepath = upload_dir / filename
    filepath.write_bytes(contents)

    result = await _run_ocr_on_file(filepath, engine=engine, already_cropped=already_cropped)

    # If serial file provided and OCR didn't already detect serial (LLM may have), run dedicated serial OCR
    serial_image_path: str | None = None
    if serial_contents is not None and result.get("detected_serial") is None:
        serial_filename = f"{uuid.uuid4()}_serial{Path(serial_file.filename or 'serial.jpg').suffix.lower() or '.jpg'}"
        serial_filepath = upload_dir / serial_filename
        serial_filepath.write_bytes(serial_contents)
        serial_image_path = f"uploads/{serial_filename}"
        async with _reader_lock:
            detected_serial = await asyncio.to_thread(_extract_serial_sync, serial_filepath)
        result["detected_serial"] = detected_serial
    elif serial_contents is not None:
        # LLM already found serial — still save the crop for future rescan
        serial_filename = f"{uuid.uuid4()}_serial{Path(serial_file.filename or 'serial.jpg').suffix.lower() or '.jpg'}"
        serial_filepath = upload_dir / serial_filename
        serial_filepath.write_bytes(serial_contents)
        serial_image_path = f"uploads/{serial_filename}"

    return {"image_path": f"uploads/{filename}", "serial_image_path": serial_image_path, **result}


@router.post("/bulk-scan")
async def bulk_scan_meters(
    files: list[UploadFile] = File(...),
    property_id: uuid.UUID | None = Form(None),
    engine: str = Form("auto", pattern="^(auto|ocr|llm)$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Upload multiple meter images at once for batch OCR processing.
    Each image is scanned for a reading value and serial number. The detected
    serial is matched against the user's accessible meters.
    Streams results as Server-Sent Events (text/event-stream):
      - One unnamed "data:" event per processed image
      - A final "event: complete" carrying the full accessible meter list
    """
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No files provided")
    if len(files) > 50:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Maximum 50 files per batch")

    # ── Build accessible meter list ───────────────────────────────────────────
    if current_user.role in (UserRole.superadmin, UserRole.admin):
        if property_id:
            meters = db.query(Meter).filter(
                Meter.property_id == property_id,
                Meter.replaced_at.is_(None),
            ).all()
        else:
            meters = db.query(Meter).filter(Meter.replaced_at.is_(None)).all()
    else:
        accessible_props = (
            db.query(PropertyUser.property_id)
            .filter(PropertyUser.user_id == current_user.id)
            .subquery()
        )
        if property_id:
            has_access = (
                db.query(PropertyUser)
                .filter(
                    PropertyUser.property_id == property_id,
                    PropertyUser.user_id == current_user.id,
                )
                .first()
            )
            if not has_access:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
            meters = db.query(Meter).filter(
                Meter.property_id == property_id,
                Meter.replaced_at.is_(None),
            ).all()
        else:
            meters = db.query(Meter).filter(
                Meter.property_id.in_(accessible_props),
                Meter.replaced_at.is_(None),
            ).all()

    # ── Read all file contents upfront (UploadFile is only readable during the request scope) ──
    upload_dir = Path(settings.upload_path)
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_payloads: list[tuple[str, bytes]] = []
    for f in files:
        file_payloads.append((f.filename or "image.jpg", await f.read()))

    all_meters_dicts = [_meter_to_dict(m) for m in meters]

    async def generate():
        for original_filename, contents in file_payloads:
            item: dict = {
                "original_filename": original_filename,
                "temp_image_path": None,
                "exif_date": None,
                "detected_value": None,
                "detected_serial": None,
                "detection_method": None,
                "matched_meter": None,
                "match_confidence": "none",
                "candidate_meters": [],
                "error": None,
            }
            try:
                if len(contents) > 10 * 1024 * 1024:
                    item["error"] = "File too large (max 10 MB)"
                    yield f"data: {json.dumps(item)}\n\n"
                    continue

                detected_mime = filetype.guess_mime(contents)
                if detected_mime not in _ALLOWED_IMAGE_MIMES:
                    item["error"] = "Not a supported image format (JPEG, PNG, WEBP, BMP, TIFF)"
                    yield f"data: {json.dumps(item)}\n\n"
                    continue

                ext = Path(original_filename).suffix.lower() or ".jpg"
                filename = f"{uuid.uuid4()}{ext}"
                filepath = upload_dir / filename
                filepath.write_bytes(contents)
                item["temp_image_path"] = f"uploads/{filename}"

                # Extract EXIF date + time
                item["exif_date"] = _extract_exif_datetime(filepath)

                ocr_result = await _run_ocr_on_file(filepath, engine=engine, already_cropped=False)
                item["detected_value"] = ocr_result.get("detected_value")
                item["detected_serial"] = ocr_result.get("detected_serial")
                item["detection_method"] = ocr_result.get("detection_method")

                # Serial → meter matching
                serial = item["detected_serial"]
                if serial:
                    matched, confidence, candidates = _match_serial_to_meters(serial, meters)
                    item["match_confidence"] = confidence
                    if matched:
                        item["matched_meter"] = _meter_to_dict(matched)
                    item["candidate_meters"] = [_meter_to_dict(m) for m in candidates]

            except Exception as exc:
                logger.error("Bulk scan error for %s: %s", original_filename, exc)
                item["error"] = f"OCR failed: {exc}"

            yield f"data: {json.dumps(item)}\n\n"

        # Final event carries the full meter list for the review UI
        yield f"event: complete\ndata: {json.dumps({'meters': all_meters_dicts})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
