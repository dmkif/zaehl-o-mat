"""
OCR endpoints — upload, scan, rescan and bulk import of meter photos.

The actual image/LLM pipeline lives in app.services.ocr_pipeline; this module
only handles HTTP concerns (validation, access control, persistence, SSE).
"""
import asyncio
import hashlib
import json
import logging
import uuid
from pathlib import Path

import filetype
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.limiter import limiter
from app.models import User, Meter, Reading
from app.permissions import accessible_property_ids, is_global_admin, require_property_access
# Re-exported pipeline internals: tests and app.main import these names from
# app.routers.ocr, and monkeypatching them on this module must keep working.
from app.services.ocr_pipeline import (  # noqa: F401
    _ALLOWED_IMAGE_MIMES,
    _MAX_UPLOAD_BYTES,
    _MIME_TO_EXT,
    _extract_exif_datetime,
    _extract_numeric,
    _extract_serial_sync,
    _fix_seven_segment,
    _format_hint_text,
    _is_easyocr_available,
    _llm_fallback,
    _llm_serial_zoom,
    _match_serial_to_meters,
    _matches_format,
    _normalize_serial,
    _reader_lock,
    _run_ocr_on_file_sync,
)

router = APIRouter(prefix="/ocr", tags=["ocr"])

logger = logging.getLogger(__name__)


def _meter_to_dict(meter) -> dict:
    return {
        "id": str(meter.id),
        "property_id": str(meter.property_id),
        "name": meter.name,
        "serial_number": meter.serial_number,
        "meter_type": meter.meter_type.value,
        "unit": meter.unit.value,
    }


async def _run_ocr_on_file(
    filepath: Path,
    engine: str = "auto",
    already_cropped: bool = False,
    last_reading: float | None = None,
    meter_type: str | None = None,
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
    last_reading / meter_type:
      When provided, the previous known reading value and meter type are used for
      format validation and, on mismatch, injected into a second LLM call as a hint.
    """

    def _hint_retry_needed(value: str | None) -> bool:
        """True when a hint-retry is warranted: value missing or wrong digit pattern."""
        if last_reading is None or meter_type is None:
            return False
        matches = _matches_format(value, meter_type)
        logger.debug(
            "format-check: value=%r meter_type=%s last_reading=%s → matches=%s retry=%s",
            value, meter_type, last_reading, matches, not matches,
        )
        return not matches

    async def _retry_with_hint(value: str | None, serial: str | None) -> tuple[str | None, str | None]:
        """Re-run LLM with the last-reading hint; keep original values if retry yields nothing."""
        logger.info(
            "hint-retry triggered: first-pass value=%r meter_type=%s last_reading=%s",
            value, meter_type, last_reading,
        )
        detected2, serial2 = await asyncio.to_thread(
            _llm_fallback, filepath, already_cropped, last_reading, meter_type
        )
        logger.info("hint-retry result: value=%r serial=%r", detected2, serial2)
        return (
            detected2 if detected2 is not None else value,
            serial2 or serial,
        )

    if engine == "llm":
        if not settings.ollama_url:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Ollama engine requested but OLLAMA_URL is not configured",
            )
        detected, detected_serial = await asyncio.to_thread(_llm_fallback, filepath, already_cropped)
        if _hint_retry_needed(detected):
            detected, detected_serial = await _retry_with_hint(detected, detected_serial)
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
        if _hint_retry_needed(detected):
            detected, detected_serial = await _retry_with_hint(detected, detected_serial)

    if detected is None and _is_easyocr_available():
        async with _reader_lock:
            try:
                return await asyncio.to_thread(_run_ocr_on_file_sync, filepath, already_cropped)
            except RuntimeError as exc:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return {"raw_texts": [], "detected_value": detected, "detected_serial": detected_serial, "detection_method": "llm" if detected else None}


@router.post("/rescan/{reading_id}")
@limiter.limit("20/minute")
async def rescan_reading(
    request: Request,
    reading_id: int,
    engine: str = Query("auto", pattern="^(auto|ocr|llm)$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Re-run OCR/LLM on the image already stored for an existing reading.
    The reading record is not modified — the client decides whether to update the value.
    """
    reading = db.query(Reading).filter(Reading.id == reading_id).first()
    if not reading:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reading not found")

    # Verify the caller has access to the meter this reading belongs to
    meter = db.query(Meter).filter(Meter.id == reading.meter_id).first()
    if not meter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meter not found")

    require_property_access(db, meter.property_id, current_user, level="read")

    if not reading.image_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reading has no associated image")

    filepath = Path(settings.upload_path) / Path(reading.image_path).name
    if not filepath.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image file not found on disk")

    # Look up previous reading (newest before/at this reading) for LLM context hint
    last_reading_value: float | None = None
    last_reading_obj = (
        db.query(Reading)
        .filter(
            Reading.meter_id == meter.id,
            Reading.id != reading_id,
        )
        .order_by(Reading.read_at.desc())
        .first()
    )
    if last_reading_obj is not None:
        last_reading_value = float(last_reading_obj.value)

    result = await _run_ocr_on_file(filepath, engine=engine, already_cropped=True, last_reading=last_reading_value, meter_type=meter.meter_type.value)

    # If a serial crop is stored and OCR mode is used, also detect the serial number
    if result.get("detected_serial") is None and reading.serial_image_path and engine in ("ocr", "auto"):
        serial_filepath = Path(settings.upload_path) / Path(reading.serial_image_path).name
        if serial_filepath.exists():
            async with _reader_lock:
                detected_serial = await asyncio.to_thread(_extract_serial_sync, serial_filepath)
            result["detected_serial"] = detected_serial

    return {"image_path": reading.image_path, **result}


@router.post("/scan")
@limiter.limit("20/minute")
async def scan_meter(
    request: Request,
    file: UploadFile = File(...),
    serial_file: UploadFile | None = File(None),
    meter_id: uuid.UUID | None = Form(None),
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

    contents = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(contents) > _MAX_UPLOAD_BYTES:
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
        serial_contents = await serial_file.read(_MAX_UPLOAD_BYTES + 1)
        if len(serial_contents) > _MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Serial image too large (max 10 MB)")
        serial_detected_mime = filetype.guess_mime(serial_contents)
        if serial_detected_mime not in _ALLOWED_IMAGE_MIMES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Serial file must be a supported image")

    # Validate that the requesting user has access to the given meter
    meter: Meter | None = None
    if meter_id is not None:
        meter = db.query(Meter).filter(Meter.id == meter_id).first()
        if not meter:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meter not found")
        require_property_access(db, meter.property_id, current_user, level="read")

    # Look up last reading for this meter to use as LLM context hint
    last_reading_value: float | None = None
    meter_type_str: str | None = None
    if meter is not None:
        meter_type_str = meter.meter_type.value
        last_reading_obj = (
            db.query(Reading)
            .filter(Reading.meter_id == meter_id)
            .order_by(Reading.read_at.desc())
            .first()
        )
        if last_reading_obj is not None:
            last_reading_value = float(last_reading_obj.value)

    # Save display crop to disk
    upload_dir = Path(settings.upload_path)
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4()}{_MIME_TO_EXT.get(detected_mime, '.jpg')}"
    filepath = upload_dir / filename
    filepath.write_bytes(contents)

    # Compute SHA-256 so the client can pass it to create_reading for duplicate detection
    image_hash = hashlib.sha256(contents).hexdigest()

    result = await _run_ocr_on_file(filepath, engine=engine, already_cropped=already_cropped, last_reading=last_reading_value, meter_type=meter_type_str)

    # If serial file provided and OCR didn't already detect serial (LLM may have), run dedicated serial OCR
    serial_image_path: str | None = None
    if serial_contents is not None and result.get("detected_serial") is None:
        serial_filename = f"{uuid.uuid4()}_serial{_MIME_TO_EXT.get(serial_detected_mime, '.jpg')}"
        serial_filepath = upload_dir / serial_filename
        serial_filepath.write_bytes(serial_contents)
        serial_image_path = f"uploads/{serial_filename}"
        async with _reader_lock:
            detected_serial = await asyncio.to_thread(_extract_serial_sync, serial_filepath)
        result["detected_serial"] = detected_serial
    elif serial_contents is not None:
        # LLM already found serial — still save the crop for future rescan
        serial_filename = f"{uuid.uuid4()}_serial{_MIME_TO_EXT.get(serial_detected_mime, '.jpg')}"
        serial_filepath = upload_dir / serial_filename
        serial_filepath.write_bytes(serial_contents)
        serial_image_path = f"uploads/{serial_filename}"

    return {"image_path": f"uploads/{filename}", "serial_image_path": serial_image_path, "image_hash": image_hash, **result}


@router.post("/hint-rescan")
@limiter.limit("30/minute")
async def hint_rescan_for_meter(
    request: Request,
    image_path: str = Form(...),
    meter_id: uuid.UUID = Form(...),
    current_value: str | None = Form(None),
    engine: str = Form("auto", pattern="^(auto|ocr|llm)$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Re-check a bulk-scan image after the user manually assigns a meter in the review UI.

    Efficient two-step logic:
      1. If current_value already matches the expected decimal format for the meter type
         → return it unchanged (no LLM call).
      2. Otherwise run one LLM call with the last-reading context hint injected.

    This avoids a full re-scan when the first-pass result was already correct.
    """
    # Load meter + access check
    meter = db.query(Meter).filter(Meter.id == meter_id, Meter.replaced_at.is_(None)).first()
    if not meter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meter not found")
    require_property_access(db, meter.property_id, current_user, level="read")

    # Validate and resolve image path — prevent path traversal
    upload_dir = Path(settings.upload_path).resolve()
    clean_name = Path(image_path.lstrip("/")).name
    filepath = (upload_dir / clean_name).resolve()
    if not filepath.is_relative_to(upload_dir) or not filepath.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")

    meter_type = meter.meter_type.value

    # Step 1: format already correct → return without any LLM call
    if _matches_format(current_value, meter_type):
        logger.debug(
            "hint-rescan: meter=%s meter_type=%s value=%r → format OK, no retry",
            meter.id, meter_type, current_value,
        )
        return {"detected_value": current_value, "detection_method": None, "hint_applied": False}

    # Step 2: format mismatch — hint-retry via LLM
    if engine == "ocr" or not settings.ollama_url:
        logger.debug(
            "hint-rescan: meter=%s meter_type=%s value=%r → format mismatch but no LLM available",
            meter.id, meter_type, current_value,
        )
        return {"detected_value": current_value, "detection_method": None, "hint_applied": False}

    last_reading_obj = (
        db.query(Reading)
        .filter(Reading.meter_id == meter.id)
        .order_by(Reading.read_at.desc())
        .first()
    )
    last_reading_value: float | None = float(last_reading_obj.value) if last_reading_obj else None

    logger.info(
        "hint-rescan: meter=%s meter_type=%s current_value=%r last_reading=%s → calling LLM with hint",
        meter.id, meter_type, current_value, last_reading_value,
    )
    detected, _ = await asyncio.to_thread(
        _llm_fallback, filepath, False, last_reading_value, meter_type
    )
    logger.info("hint-rescan result: detected=%r (was %r)", detected, current_value)

    return {
        "detected_value": detected if detected is not None else current_value,
        "detection_method": "llm" if detected is not None else None,
        "hint_applied": detected is not None,
    }


@router.post("/bulk-scan")
@limiter.limit("4/minute")
async def bulk_scan_meters(
    request: Request,
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
    if is_global_admin(current_user):
        if property_id:
            meters = db.query(Meter).filter(
                Meter.property_id == property_id,
                Meter.replaced_at.is_(None),
            ).all()
        else:
            meters = db.query(Meter).filter(Meter.replaced_at.is_(None)).all()
    else:
        if property_id:
            require_property_access(db, property_id, current_user, level="read")
            meters = db.query(Meter).filter(
                Meter.property_id == property_id,
                Meter.replaced_at.is_(None),
            ).all()
        else:
            meters = db.query(Meter).filter(
                Meter.property_id.in_(accessible_property_ids(current_user)),
                Meter.replaced_at.is_(None),
            ).all()

    # ── Read all file contents upfront (UploadFile is only readable during the request scope) ──
    upload_dir = Path(settings.upload_path)
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_payloads: list[tuple[str, bytes]] = []
    for f in files:
        file_payloads.append((f.filename or "image.jpg", await f.read()))

    all_meters_dicts = [_meter_to_dict(m) for m in meters]
    accessible_meter_ids = [m.id for m in meters]

    async def generate():
        # Track hashes seen within this batch to catch in-session duplicates
        seen_hashes: dict[str, str] = {}  # hash → original_filename

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
                "image_hash": None,
                "duplicate_reading": None,
            }
            try:
                if len(contents) > _MAX_UPLOAD_BYTES:
                    item["error"] = "File too large (max 10 MB)"
                    yield f"data: {json.dumps(item)}\n\n"
                    continue

                detected_mime = filetype.guess_mime(contents)
                if detected_mime not in _ALLOWED_IMAGE_MIMES:
                    item["error"] = "Not a supported image format (JPEG, PNG, WEBP, BMP, TIFF)"
                    yield f"data: {json.dumps(item)}\n\n"
                    continue

                # ── SHA-256 hash for duplicate detection ──────────────────────
                image_hash = hashlib.sha256(contents).hexdigest()
                item["image_hash"] = image_hash

                # Check for duplicates: (1) already seen in this batch
                if image_hash in seen_hashes:
                    item["duplicate_reading"] = {
                        "source": "session",
                        "original_filename": seen_hashes[image_hash],
                    }
                else:
                    seen_hashes[image_hash] = original_filename
                    # (2) existing reading with this hash — scoped to the caller's
                    # accessible meters so no foreign tenant data leaks into the response
                    existing = (
                        db.query(Reading)
                        .filter(
                            Reading.image_hash == image_hash,
                            Reading.meter_id.in_(accessible_meter_ids),
                        )
                        .join(Reading.meter)
                        .first()
                    )
                    if existing:
                        item["duplicate_reading"] = {
                            "source": "database",
                            "reading_id": existing.id,
                            "meter_name": existing.meter.name if existing.meter else None,
                            "value": str(existing.value),
                            "read_at": existing.read_at.isoformat(),
                        }

                ext = _MIME_TO_EXT.get(detected_mime, ".jpg")
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
                matched = None
                if serial:
                    matched, confidence, candidates = _match_serial_to_meters(serial, meters)
                    item["match_confidence"] = confidence
                    if matched:
                        item["matched_meter"] = _meter_to_dict(matched)
                    item["candidate_meters"] = [_meter_to_dict(m) for m in candidates]

                # Zoom-crop fallback pass: full-photo downscaling leaves label
                # characters too small — retry with native-resolution bands.
                # A zoom candidate is only accepted when it matches a known
                # meter (bands without a label hallucinate approval numbers
                # or handwritten notes — never store those blindly).
                if matched is None and engine != "ocr" and settings.ollama_url:
                    zoom_candidates = await asyncio.to_thread(_llm_serial_zoom, filepath)
                    logger.info(
                        "zoom-serial pass: file=%s first-pass serial=%r candidates=%r",
                        original_filename, serial, zoom_candidates,
                    )
                    for cand in zoom_candidates:
                        m2, conf2, cands2 = _match_serial_to_meters(cand, meters)
                        if m2 is not None:
                            matched = m2
                            item["detected_serial"] = cand
                            item["match_confidence"] = conf2
                            item["matched_meter"] = _meter_to_dict(m2)
                            item["candidate_meters"] = [_meter_to_dict(m) for m in cands2]
                            item["detection_method"] = item["detection_method"] or "llm"
                            logger.info(
                                "zoom-serial matched: file=%s serial=%r meter=%s confidence=%s",
                                original_filename, cand, m2.serial_number, conf2,
                            )
                            break

                # Second-pass LLM with context hint:
                # Triggered when the first pass found no value OR the digit pattern
                # doesn't match the meter type (e.g. decimal comma missed).
                _fmt_ok = _matches_format(item["detected_value"], matched.meter_type.value if matched else None)
                logger.info(
                    "bulk format-check: file=%s value=%r matched=%s meter_type=%s fmt_ok=%s",
                    original_filename,
                    item["detected_value"],
                    matched.serial_number if matched else None,
                    matched.meter_type.value if matched else None,
                    _fmt_ok,
                )
                if (
                    (item["detected_value"] is None or not _fmt_ok)
                    and matched is not None
                    and engine != "ocr"
                    and settings.ollama_url
                ):
                    last_reading_obj = (
                        db.query(Reading)
                        .filter(Reading.meter_id == matched.id)
                        .order_by(Reading.read_at.desc())
                        .first()
                    )
                    if last_reading_obj is not None:
                        hint_value = float(last_reading_obj.value)
                        logger.info(
                            "bulk hint-retry: file=%s value=%r → retry with last_reading=%s meter_type=%s",
                            original_filename, item["detected_value"], hint_value, matched.meter_type.value,
                        )
                        detected2, serial2 = await asyncio.to_thread(
                            _llm_fallback, filepath, False, hint_value, matched.meter_type.value
                        )
                        logger.info("bulk hint-retry result: value=%r serial=%r", detected2, serial2)
                        if detected2 is not None:
                            item["detected_value"] = detected2
                            item["detection_method"] = "llm"
                        if serial2 and not item["detected_serial"]:
                            item["detected_serial"] = serial2

            except Exception as exc:
                logger.error("Bulk scan error for %s: %s", original_filename, exc)
                item["error"] = "OCR processing failed"

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
