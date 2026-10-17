"""
OCR service — standalone, optional container.

Exposes POST /scan, POST /serial, GET /health per
specs/001-ocr-optional-container/contracts/ocr-service-api.md.

Authentication (F-030): when OCR_API_KEY is set, /scan and /serial require
``Authorization: Bearer <OCR_API_KEY>`` (constant-time comparison, the backend
sends the same variable).  /health stays open for container probes.  Without a
key the service is unauthenticated and logs a warning at startup.
"""
import asyncio
import hmac
import io
import logging
import tempfile
import uuid
from pathlib import Path

import filetype
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status

from app.config import settings
from app.pipeline import _extract_serial_sync, _run_ocr_on_file_sync

logger = logging.getLogger(__name__)



@asynccontextmanager
async def _lifespan(_app: FastAPI):
    if not settings.ocr_api_key:
        logger.warning(
            "OCR_API_KEY is not set: /scan and /serial accept unauthenticated requests. "
            "Set OCR_API_KEY (same value as the backend) unless the network already isolates this service."
        )
    yield


def require_api_key(request: Request) -> None:
    """Bearer-token check for /scan and /serial; no-op while no key is configured."""
    key = settings.ocr_api_key
    if not key:
        return
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    # Compare UTF-8 bytes: compare_digest raises on non-ASCII str.
    ok = scheme.lower() == "bearer" and hmac.compare_digest(token.encode("utf-8"), key.encode("utf-8"))
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )


app = FastAPI(title="Zähl-O-Mat OCR Service", lifespan=_lifespan)

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


def _check_dimensions(contents: bytes) -> None:
    """Reject images whose decoded size is too large, reading only the header (F-011)."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(contents)) as img:
            width, height = img.size
    except Image.DecompressionBombError:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Image dimensions too large") from None
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unreadable or unsupported image") from None
    if (
        max(width, height) > settings.max_image_side
        or width * height > settings.max_image_pixels
    ):
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Image dimensions too large")


def _save_upload(contents: bytes) -> Path:
    """Validate magic bytes and persist to a temp file. Raises 400 on failure."""
    mime = filetype.guess_mime(contents)
    if mime not in _ALLOWED_IMAGE_MIMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unreadable or unsupported image",
        )
    _check_dimensions(contents)
    filepath = _TMP_DIR / f"{uuid.uuid4()}{_MIME_TO_EXT[mime]}"
    filepath.write_bytes(contents)
    return filepath


@app.post("/scan", dependencies=[Depends(require_api_key)])
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


@app.post("/serial", dependencies=[Depends(require_api_key)])
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
