import io
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models import User, Meter, Property, PropertyUser, UserRole

router = APIRouter(prefix="/ocr", tags=["ocr"])

# EasyOCR reader is lazily initialized to avoid slow startup
_reader = None


def _get_reader():
    global _reader
    if _reader is None:
        import easyocr  # type: ignore
        _reader = easyocr.Reader(["de", "en"], gpu=False)
    return _reader


def _extract_numeric(texts: list[str]) -> str | None:
    """Pick the best numeric candidate from OCR results."""
    import re
    candidates = []
    for t in texts:
        # Remove whitespace, keep digits, comma, dot
        cleaned = re.sub(r"[^\d.,]", "", t)
        if cleaned:
            # Normalize German decimal comma to dot
            cleaned = cleaned.replace(",", ".")
            candidates.append(cleaned)
    # Return the longest numeric-looking string
    candidates.sort(key=len, reverse=True)
    return candidates[0] if candidates else None


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

    # Save to disk
    upload_dir = Path(settings.upload_path)
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4()}{Path(file.filename or 'img.jpg').suffix.lower() or '.jpg'}"
    filepath = upload_dir / filename
    filepath.write_bytes(contents)

    # Run OCR
    try:
        reader = _get_reader()
        results = reader.readtext(str(filepath), detail=0)
    except Exception as exc:
        # Don't expose internal errors, but log them
        import logging
        logging.getLogger(__name__).error("OCR failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="OCR processing failed")

    detected = _extract_numeric(results)

    return {
        "image_path": f"uploads/{filename}",
        "raw_texts": results,
        "detected_value": detected,
    }
