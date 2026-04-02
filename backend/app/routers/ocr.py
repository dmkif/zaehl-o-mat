import asyncio
import base64
import io
import json
import logging
import os
import re
import uuid
from pathlib import Path

import filetype
import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
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


def _get_reader():
    global _reader
    if _reader is None:
        import easyocr  # type: ignore
        _reader = easyocr.Reader(["de", "en"], gpu=False)
    return _reader


def _preprocess_image(filepath: Path) -> tuple:
    """
    Pre-process image for better OCR accuracy.
    Returns (proc_path, (width, height)) of the processed image.

    Key steps:
    - Resize to max 2000px (speeds up EasyOCR)
    - Weighted grayscale: suppress red channel to reduce LED-glow artefacts
      (Holley and similar meters have a bright red blinking LED)
    - Autocontrast: stretches histogram to full 0-255 range
    - Unsharp mask: enhances edges for digit separation
    """
    import numpy as np
    from PIL import Image, ImageFilter

    img = Image.open(filepath)

    # Resize if too large; EasyOCR is accurate enough at 2000px
    max_dim = 2000
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    orig_size = img.size  # (width, height) – used later for bbox scoring

    # Weighted grayscale: reduce red weight (0.10 vs. standard 0.299)
    # so a bright red LED in the center doesn't overexpose surrounding digits.
    if img.mode in ("RGB", "RGBA"):
        rgb = img.convert("RGB")
        r_arr = np.array(rgb.split()[0], dtype=np.float32)
        g_arr = np.array(rgb.split()[1], dtype=np.float32)
        b_arr = np.array(rgb.split()[2], dtype=np.float32)
        gray = (0.10 * r_arr + 0.70 * g_arr + 0.20 * b_arr).clip(0, 255).astype(np.uint8)
        img = Image.fromarray(gray, mode="L")
    else:
        img = img.convert("L")

    # CLAHE (Contrast Limited Adaptive Histogram Equalization) applies local
    # contrast enhancement tile-by-tile instead of globally — this brightens
    # dark display areas (LCD/LED) relative to the bright paper background.
    import cv2
    img_array = np.array(img) if img.mode == "L" else np.array(img.convert("L"))
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    img_array = clahe.apply(img_array)
    img = Image.fromarray(img_array, mode="L")
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))

    proc_path = filepath.with_suffix(".proc.jpg")
    img.save(proc_path, quality=95)
    return proc_path, orig_size


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


def _llm_fallback(filepath: Path) -> tuple[str | None, str | None]:
    """
    Ask a local Ollama vision model to read the meter display when EasyOCR fails.
    Model and URL are read from OLLAMA_MODEL / OLLAMA_URL environment variables.
    Returns (reading, serial_number) — either value may be None on failure.
    """
    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    model = os.environ.get("OLLAMA_MODEL", "qwen2.5vl:7b")

    try:
        # Resize to max 800px before sending to LLM to limit visual-token count
        # and avoid OOM on systems with limited RAM (6–8 GB).
        from PIL import Image
        img = Image.open(filepath)
        max_side = 800
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
                    "You are analyzing a photo of a utility meter (electricity, water, or oil).\n"
                    'Reply ONLY with valid JSON in this exact format: {"reading": "VALUE", "serial": "VALUE"}\n'
                    "- reading: ONLY the main consumption counter digits on the large display, "
                    "with optional decimal point, no units, no spaces.\n"
                    "- serial: the serial number or device ID printed on the meter label "
                    "(typically 6-12 digits, often labeled Nr., S/N, or Zähler-Nr.).\n"
                    "If you cannot read a field, use null."
                ),
                "images": [b64],
                "stream": False,
                "format": "json",
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
            s = v.strip()
            return None if not s or s.lower() == "null" else s

        return _clean_reading(data.get("reading")), _clean_serial(data.get("serial"))
    except Exception:
        logger.warning("LLM fallback failed for %s", filepath)
        return None, None


def _run_ocr_on_file_sync(filepath: Path) -> dict:
    """Synchronous OCR (EasyOCR only) — called from asyncio.to_thread."""
    raw_texts: list = []
    detected = None

    proc_path = None
    img_size = (0, 0)
    try:
        reader = _get_reader()
        proc_path, img_size = _preprocess_image(filepath)
        results = reader.readtext(str(proc_path), detail=1)
    except Exception as exc:
        logger.error("OCR failed: %s", exc)
        raise RuntimeError(f"OCR processing failed: {exc}") from exc
    finally:
        if proc_path and proc_path.exists():
            proc_path.unlink(missing_ok=True)

    raw_texts = [
        {"text": text, "conf": round(float(conf), 3)}
        for (_, text, conf) in results
    ]
    detected = _extract_numeric(results, img_size=img_size)
    return {"raw_texts": raw_texts, "detected_value": detected, "detected_serial": None}


async def _run_ocr_on_file(filepath: Path) -> dict:
    """Run the full OCR/LLM pipeline on an existing file and return detection results."""
    detected = None
    detected_serial = None

    # LLM runs synchronously via httpx (blocking I/O) — offload to thread
    if os.environ.get("OLLAMA_URL"):
        detected, detected_serial = await asyncio.to_thread(_llm_fallback, filepath)

    if detected is None:
        # EasyOCR is CPU-bound — run in thread pool behind the reader lock
        async with _reader_lock:
            try:
                result = await asyncio.to_thread(_run_ocr_on_file_sync, filepath)
            except RuntimeError as exc:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
        return result

    return {"raw_texts": [], "detected_value": detected, "detected_serial": detected_serial}


@router.post("/rescan/{reading_id}")
async def rescan_reading(
    reading_id: int,
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

    result = await _run_ocr_on_file(filepath)
    return {"image_path": reading.image_path, **result}


@router.post("/scan")
async def scan_meter(
    file: UploadFile = File(...),
    meter_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Upload a meter photo and extract the reading via OCR.
    Returns the detected numeric value (string) for the client to confirm before saving.
    The image is also saved so it can be referenced when creating a Reading.
    """
    # Validate file type
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must be an image")

    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:  # 10 MB limit
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Image too large (max 10 MB)")

    # Magic-bytes validation: distrust the client-supplied Content-Type
    detected_mime = filetype.guess_mime(contents)
    if detected_mime not in _ALLOWED_IMAGE_MIMES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must be a supported image (JPEG, PNG, WEBP, GIF, BMP, TIFF)")

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

    # Save to disk
    upload_dir = Path(settings.upload_path)
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4()}{Path(file.filename or 'img.jpg').suffix.lower() or '.jpg'}"
    filepath = upload_dir / filename
    filepath.write_bytes(contents)

    result = await _run_ocr_on_file(filepath)
    return {"image_path": f"uploads/{filename}", **result}
