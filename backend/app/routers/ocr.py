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
from contextlib import contextmanager
from types import SimpleNamespace
from pathlib import Path

import filetype
import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.limiter import limiter
from app.services.image_hygiene import ImageUndecodable, sanitize_image
from app.services.llm_client import (
    GatewayConfigError,
    LLMError,
    LocalModelUnavailable,
    llm_enabled,
    using_gateway,
)
from app.models import UploadFile as UploadRecord, User, Meter, Reading
from app.permissions import accessible_property_ids, is_global_admin, require_property_access
from app.upload_registry import get_upload, may_use_upload, register_upload
# Re-exported pipeline internals: tests and app.main import these names from
# app.routers.ocr, and monkeypatching them on this module must keep working.
from app.services.ocr_pipeline import (  # noqa: F401
    ImageTooLarge,
    OcrServiceError,
    check_image_dimensions,
    _ALLOWED_IMAGE_MIMES,
    _MAX_UPLOAD_BYTES,
    _MIME_TO_EXT,
    _extract_exif_datetime,
    _format_hint_text,
    _llm_fallback,
    _llm_serial_zoom,
    _match_serial_to_meters,
    _matches_format,
    _normalize_serial,
    _ocr_service_scan,
    _ocr_service_serial,
)

router = APIRouter(prefix="/ocr", tags=["ocr"])

# Gateway path: "model unavailable" / "bad gateway config" are reported to the
# caller with fixed, user-safe texts (never str(exc) of an HTTP error).
_LLM_ERROR_STATUS = {
    LocalModelUnavailable: status.HTTP_503_SERVICE_UNAVAILABLE,
    GatewayConfigError: status.HTTP_502_BAD_GATEWAY,
}


def _require_sane_dimensions(contents: bytes, label: str) -> None:
    """413 when the decoded size (read from the header) exceeds the limits (F-011)."""
    try:
        check_image_dimensions(contents)
    except ImageTooLarge:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"{label} dimensions too large",
        ) from None


async def _sanitized_or_400(contents: bytes, label: str) -> bytes:
    """Decode check + metadata removal before anything is stored (F-032, F-033)."""
    try:
        return await asyncio.to_thread(sanitize_image, contents)
    except ImageUndecodable:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{label} is damaged or incomplete",
        ) from None


def _reject_nul(value: str | None, label: str) -> None:
    if value is not None and "\x00" in value:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"{label} contains invalid characters")


def _ocr_service_http_error() -> HTTPException:
    # F-029: fixed text, the malformed upstream body is never echoed.
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="OCR service returned an invalid response",
    )


@contextmanager
def _short_session(bind):
    """Short-lived DB session for one step of the streamed bulk scan (F-013)."""
    session = Session(bind=bind, autoflush=False)
    try:
        yield session
    finally:
        session.close()


def _discard_upload(db: Session, *filenames: str | None) -> None:
    """Remove stored files and their registry rows of a scan that failed (F-028)."""
    names = [n for n in filenames if n]
    for name in names:
        try:
            (Path(settings.upload_path) / name).unlink(missing_ok=True)
        except OSError:
            logger.warning("could not remove orphan upload %s", name)
    if names:
        try:
            db.query(UploadRecord).filter(UploadRecord.filename.in_(names)).delete(synchronize_session=False)
            db.commit()
        except Exception:
            db.rollback()
            logger.warning("could not remove upload registry rows")


def _discard_bulk_file(bind, filename: str | None) -> None:
    if filename:
        with _short_session(bind) as s:
            _discard_upload(s, filename)


async def _read_limited(upload: UploadFile, limit: int) -> bytes:
    """Read at most ``limit + 1`` bytes in chunks (the caller checks for overflow)."""
    chunks: list[bytes] = []
    total = 0
    while total <= limit:
        chunk = await upload.read(min(65536, limit + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _llm_http_error(exc: LLMError) -> HTTPException:
    return HTTPException(
        status_code=_LLM_ERROR_STATUS.get(type(exc), status.HTTP_503_SERVICE_UNAVAILABLE),
        detail=str(exc),
    )

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


async def _try_ocr_service_serial(filepath: Path) -> str | None:
    """
    Best-effort dedicated serial-crop OCR via the optional OCR service.

    Returns None on any failure (not configured, unreachable, timeout) —
    this is always a secondary lookup on top of an already-returned
    reading, never the primary flow, so failures here must not fail the
    whole request.
    """
    if not settings.ocr_url:
        return None
    try:
        return await asyncio.to_thread(_ocr_service_serial, filepath)
    except (httpx.HTTPError, OcrServiceError):
        logger.warning("OCR-service serial lookup failed for %s", filepath)
        return None


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
      "auto"  — try LLM first (if OLLAMA_URL is set), fall back to the
                optional OCR service (if OCR_URL is set)
      "ocr"   — OCR service only, skip LLM entirely; raises HTTP 503 when
                OCR_URL is not configured or the service is unreachable
                (no silent fallback to another engine — FR-005)
      "llm"   — Ollama only; raises HTTP 503 when OLLAMA_URL is not configured
    already_cropped:
      When True, tells the OCR service to skip its auto-strip-detection
      step (the user already isolated the display area via the crop UI).
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
        if not llm_enabled():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "LLM engine requested but GATEWAY_BASE_URL is not configured"
                    if using_gateway()
                    else "Ollama engine requested but OLLAMA_URL is not configured"
                ),
            )
        detected, detected_serial = await asyncio.to_thread(_llm_fallback, filepath, already_cropped)
        if _hint_retry_needed(detected):
            detected, detected_serial = await _retry_with_hint(detected, detected_serial)
        return {"raw_texts": [], "detected_value": detected, "detected_serial": detected_serial, "detection_method": "llm"}

    if engine == "ocr":
        if not settings.ocr_url:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OCR engine requested but OCR_URL is not configured",
            )
        try:
            return await asyncio.to_thread(_ocr_service_scan, filepath, already_cropped)
        except httpx.HTTPError as exc:
            # FR-005: explicit engine=ocr never silently falls back to another engine.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OCR service is unreachable",
            ) from exc
        except OcrServiceError:
            raise _ocr_service_http_error() from None

    # engine == "auto": LLM first (if available), fall back to the optional OCR service
    detected = None
    detected_serial = None
    if llm_enabled():
        detected, detected_serial = await asyncio.to_thread(_llm_fallback, filepath, already_cropped)
        if _hint_retry_needed(detected):
            detected, detected_serial = await _retry_with_hint(detected, detected_serial)

    if detected is None and settings.ocr_url:
        try:
            return await asyncio.to_thread(_ocr_service_scan, filepath, already_cropped)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OCR service is unreachable",
            ) from exc
        except OcrServiceError:
            raise _ocr_service_http_error() from None

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

    try:
        result = await _run_ocr_on_file(filepath, engine=engine, already_cropped=True, last_reading=last_reading_value, meter_type=meter.meter_type.value)
    except LLMError as exc:
        raise _llm_http_error(exc) from None

    # If a serial crop is stored and OCR mode is used, also detect the serial number
    if result.get("detected_serial") is None and reading.serial_image_path and engine in ("ocr", "auto"):
        serial_filepath = Path(settings.upload_path) / Path(reading.serial_image_path).name
        if serial_filepath.exists():
            result["detected_serial"] = await _try_ocr_service_serial(serial_filepath)

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

    _require_sane_dimensions(contents, "Image")
    contents = await _sanitized_or_400(contents, "Image")

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
        _require_sane_dimensions(serial_contents, "Serial image")
        serial_contents = await _sanitized_or_400(serial_contents, "Serial image")

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
    upload_property_id = meter.property_id if meter is not None else None
    register_upload(db, filename, upload_property_id, current_user)
    db.commit()

    # SHA-256 of the stored (sanitised) bytes - the same value create_reading derives from the file
    image_hash = hashlib.sha256(contents).hexdigest()

    stored = [filename]  # F-028: everything written for this scan is removed again on failure
    serial_image_path: str | None = None
    try:
        result = await _run_ocr_on_file(filepath, engine=engine, already_cropped=already_cropped, last_reading=last_reading_value, meter_type=meter_type_str)

        # Serial crop is stored in both cases; dedicated serial OCR only when the
        # pipeline did not already find a serial (LLM may have).
        if serial_contents is not None:
            serial_filename = f"{uuid.uuid4()}_serial{_MIME_TO_EXT.get(serial_detected_mime, '.jpg')}"
            serial_filepath = upload_dir / serial_filename
            serial_filepath.write_bytes(serial_contents)
            stored.append(serial_filename)
            serial_image_path = f"uploads/{serial_filename}"
            register_upload(db, serial_filename, upload_property_id, current_user)
            db.commit()
            if result.get("detected_serial") is None:
                result["detected_serial"] = await _try_ocr_service_serial(serial_filepath)
    except LLMError as exc:
        _discard_upload(db, *stored)
        raise _llm_http_error(exc) from None
    except Exception:
        _discard_upload(db, *stored)
        raise

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
    _reject_nul(image_path, "image_path")
    _reject_nul(current_value, "current_value")
    if len(image_path) > 512 or (current_value is not None and len(current_value) > 64):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Value too long")
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
    # F-004: the file must be one the caller uploaded or that belongs to the
    # meter's property - checked before anything is sent to the LLM gateway.
    if not may_use_upload(db, get_upload(db, clean_name), current_user, meter.property_id):
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
    if engine == "ocr" or not llm_enabled():
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
    try:
        detected, _ = await asyncio.to_thread(
            _llm_fallback, filepath, False, last_reading_value, meter_type
        )
    except LLMError as exc:
        raise _llm_http_error(exc) from None
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
    if len(files) > settings.max_bulk_files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum {settings.max_bulk_files} files per batch",
        )

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

    # ── Read the uploads in chunks against a cumulative budget (F-012) ───────
    # A file over the per-file limit is kept as ``None`` (error item later, its
    # bytes are not retained); exceeding the total budget rejects the request.
    upload_dir = Path(settings.upload_path)
    upload_dir.mkdir(parents=True, exist_ok=True)

    budget = settings.max_bulk_total_bytes
    used = 0
    file_payloads: list[tuple[str, bytes | None]] = []
    for f in files:
        data = await _read_limited(f, _MAX_UPLOAD_BYTES)
        used += len(data)
        if used > budget:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Upload too large (max {budget // (1024 * 1024)} MB per batch)",
            )
        file_payloads.append((f.filename or "image.jpg", None if len(data) > _MAX_UPLOAD_BYTES else data))

    all_meters_dicts = [_meter_to_dict(m) for m in meters]
    accessible_meter_ids = [m.id for m in meters]

    # ── F-013: release the request session before the long streamed run ──────
    # Every step in the generator that needs the database opens its own short
    # session; nothing is held while the LLM/OCR service is awaited.
    uploader = SimpleNamespace(id=current_user.id)
    bind = db.get_bind()
    db.expunge_all()
    db.close()

    async def generate():
        # Track hashes seen within this batch to catch in-session duplicates
        seen_hashes: dict[str, str] = {}  # hash → original_filename
        # Set once the local model is unreachable / misconfigured: the remaining
        # images are not sent (each would wait up to the full client timeout).
        llm_abort: str | None = None

        while file_payloads:
            original_filename, contents = file_payloads.pop(0)  # release the bytes once processed
            stored_name: str | None = None
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
            if llm_abort is not None and engine != "ocr":
                item["error"] = llm_abort
                yield f"data: {json.dumps(item)}\n\n"
                continue
            try:
                if contents is None:
                    item["error"] = "File too large (max 10 MB)"
                    yield f"data: {json.dumps(item)}\n\n"
                    continue

                detected_mime = filetype.guess_mime(contents)
                if detected_mime not in _ALLOWED_IMAGE_MIMES:
                    item["error"] = "Not a supported image format (JPEG, PNG, WEBP, BMP, TIFF)"
                    yield f"data: {json.dumps(item)}\n\n"
                    continue

                try:
                    check_image_dimensions(contents)
                except ImageTooLarge:
                    item["error"] = "Image dimensions too large"
                    yield f"data: {json.dumps(item)}\n\n"
                    continue

                try:
                    contents = await asyncio.to_thread(sanitize_image, contents)
                except ImageUndecodable:
                    item["error"] = "Image is damaged or incomplete"
                    yield f"data: {json.dumps(item)}\n\n"
                    continue

                # ── SHA-256 of the stored (sanitised) bytes for duplicate detection ──
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

                ext = _MIME_TO_EXT.get(detected_mime, ".jpg")
                filename = f"{uuid.uuid4()}{ext}"
                filepath = upload_dir / filename
                filepath.write_bytes(contents)
                stored_name = filename
                item["temp_image_path"] = f"uploads/{filename}"
                # Short session: duplicate lookup + upload registration
                with _short_session(bind) as s:
                    if item["duplicate_reading"] is None:
                        # existing reading with this hash — scoped to the caller's
                        # accessible meters so no foreign tenant data leaks into the response
                        existing = (
                            s.query(Reading)
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
                    register_upload(s, filename, property_id, uploader)
                    s.commit()

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
                if matched is None and engine != "ocr" and llm_enabled():
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
                    and llm_enabled()
                ):
                    with _short_session(bind) as s:
                        last_reading_obj = (
                            s.query(Reading)
                            .filter(Reading.meter_id == matched.id)
                            .order_by(Reading.read_at.desc())
                            .first()
                        )
                        hint_value = float(last_reading_obj.value) if last_reading_obj is not None else None
                    if hint_value is not None:
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

            except LLMError as exc:
                logger.warning("Bulk scan: %s (%s) — skipping remaining images", type(exc).__name__, original_filename)
                llm_abort = str(exc)
                item["error"] = llm_abort
                item["temp_image_path"] = None
                _discard_bulk_file(bind, stored_name)
            except Exception as exc:
                logger.error("Bulk scan error for %s: %s", original_filename, type(exc).__name__)
                item["error"] = "OCR processing failed"
                item["temp_image_path"] = None
                _discard_bulk_file(bind, stored_name)

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
