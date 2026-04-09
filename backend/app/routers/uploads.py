"""Auth-protected endpoint for serving uploaded meter images."""
import mimetypes
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from app.auth import get_current_user
from app.config import settings
from app.models import User

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.get("/{filename}")
def serve_upload(
    filename: str,
    current_user: User = Depends(get_current_user),
):
    """Serve an uploaded meter image, requiring a valid JWT."""
    upload_root = Path(settings.upload_path).resolve()
    try:
        filepath = (upload_root / filename).resolve()
        filepath.relative_to(upload_root)
    except (ValueError, Exception):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid filename")
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    mime_type, _ = mimetypes.guess_type(str(filepath))
    return FileResponse(
        path=str(filepath),
        media_type=mime_type or "application/octet-stream",
        filename=filename,
    )
