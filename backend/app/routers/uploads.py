"""Auth-protected endpoint for serving uploaded meter images."""
import mimetypes
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
    # Prevent path traversal: only allow plain filenames (no slashes or dots that navigate)
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid filename")

    filepath = Path(settings.upload_path) / filename
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    mime_type, _ = mimetypes.guess_type(str(filepath))
    return FileResponse(
        path=str(filepath),
        media_type=mime_type or "application/octet-stream",
        filename=filename,
    )
