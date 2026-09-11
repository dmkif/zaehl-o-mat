"""
OCR service — standalone, optional container.

Exposes POST /scan, POST /serial, GET /health per
specs/001-ocr-optional-container/contracts/ocr-service-api.md. No
authentication is implemented here (same as Ollama today) — an operator
who wants to secure this link fronts it with an authenticating reverse
proxy (see contracts/ocr-service-api.md and research.md Decision 3).
"""
import asyncio
import logging
import tempfile
import uuid
from pathlib import Path

import filetype
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status

from app.pipeline import _extract_serial_sync, _run_ocr_on_file_sync

logger = logging.getLogger(__name__)

app = FastAPI(title="Zähl-O-Mat OCR Service")

_ALLOWED_IMAGE_MIMES = frozenset({
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/bmp",
    "image/tiff",
})
_MIME_TO_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}

# A fresh, randomly-named directory per process — avoids a predictable
# shared /tmp path (CWE-377) while still being a plain filesystem path the
# EasyOCR pipeline functions (which take a Path) can work with directly.
_TMP_DIR = Path(tempfile.mkdtemp(prefix="ocr-service-"))


def _save_upload(contents: bytes) -> Path:
    """Validate magic bytes and persist to a temp file. Raises 400 on failure."""
    mime = filetype.guess_mime(contents)
    if mime not in _ALLOWED_IMAGE_MIMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unreadable or unsupported image",
        )
    filepath = _TMP_DIR / f"{uuid.uuid4()}{_MIME_TO_EXT[mime]}"
    filepath.write_bytes(contents)
    return filepath


@app.post("/scan")
async def scan(file: UploadFile = File(...), already_cropped: bool = Form(False)):
    contents = await file.read()
    filepath = _save_upload(contents)
    try:
        return await asyncio.to_thread(_run_ocr_on_file_sync, filepath, already_cropped)
    except RuntimeError as exc:
        logger.error("OCR scan failed for %s: %s", filepath, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OCR processing failed",
        ) from exc
    finally:
        filepath.unlink(missing_ok=True)


@app.post("/serial")
async def serial(file: UploadFile = File(...)):
    contents = await file.read()
    filepath = _save_upload(contents)
    try:
        detected = await asyncio.to_thread(_extract_serial_sync, filepath)
        return {"detected_serial": detected}
    finally:
        filepath.unlink(missing_ok=True)


@app.get("/health")
def health():
    return {"status": "ok"}
