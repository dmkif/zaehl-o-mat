"""Auth-protected endpoint for serving uploaded meter images."""
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models import Meter, Reading, User
from app.permissions import has_property_access, is_global_admin
from app.upload_registry import get_upload, may_use_upload

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.get("/{filename}")
def serve_upload(
    filename: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Serve an uploaded meter image, requiring a valid JWT.

    Tenant check: if the image is referenced by a stored reading, the caller
    must have access to the property that reading belongs to.  Images not yet
    attached to any reading (fresh scans awaiting confirmation) are served to
    their uploader and to members of the property they were uploaded for.
    """
    upload_root = Path(settings.upload_path).resolve()
    try:
        filepath = (upload_root / filename).resolve()
        filepath.relative_to(upload_root)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid filename")
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    if not is_global_admin(current_user):
        stored_path = f"uploads/{filename}"
        readings = (
            db.query(Reading)
            .join(Meter, Reading.meter_id == Meter.id)
            .filter(
                or_(
                    Reading.image_path == stored_path,
                    Reading.serial_image_path == stored_path,
                )
            )
            .all()
        )
        if readings:
            # Attached file: any reading the caller may read grants access.
            if not any(
                has_property_access(db, r.meter.property_id, current_user, level="read")
                for r in readings
            ):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        else:
            # Not attached yet (fresh scan): uploader or property member only.
            # Files unknown to the registry are not served (F-004).
            row = get_upload(db, filename)
            if row is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
            if not may_use_upload(db, row, current_user):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    mime_type, _ = mimetypes.guess_type(str(filepath))
    return FileResponse(
        path=str(filepath),
        media_type=mime_type or "application/octet-stream",
        filename=filename,
    )
