"""Ownership registry for stored meter images (F-004).

Every file written by scan / bulk-scan gets a row in ``upload_files``.  A file
may only be attached to a reading, re-scanned or served when the caller
uploaded it or belongs to the property it was uploaded for.  Readings that
predate the registry keep working: serving falls back to the access rule of
the reading that references the file.
"""
import re
import uuid

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import Reading, UploadFile as UploadRecord, User
from app.permissions import has_property_access, is_global_admin

# What new/changed ``image_path`` / ``serial_image_path`` values must look like:
# exactly what scan / bulk-scan return (``uploads/<uuid>[_serial].<ext>``).
STORED_PATH_RE = re.compile(
    r"^uploads/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"(?:_serial)?\.(?:jpg|jpeg|png|webp|gif|bmp|tiff)$"
)


def is_valid_stored_path(value: str) -> bool:
    return STORED_PATH_RE.fullmatch(value) is not None


def filename_of(stored_path: str) -> str:
    return stored_path.rsplit("/", 1)[-1]


def register_upload(db: Session, filename: str, property_id: uuid.UUID | None, user: User) -> None:
    """Add (not commit) the registry row for a freshly stored file."""
    db.add(UploadRecord(filename=filename, property_id=property_id, uploaded_by_id=user.id))


def get_upload(db: Session, filename: str) -> UploadRecord | None:
    return db.query(UploadRecord).filter(UploadRecord.filename == filename).first()


def may_use_upload(db: Session, row: UploadRecord | None, user: User, property_id: uuid.UUID | None = None) -> bool:
    """True when ``user`` may attach / re-scan / read this registered file.

    ``property_id`` is the property the file is about to be used for; when
    given, a file registered for exactly that property is usable by its
    members (who passed their own access check on that property).
    """
    if row is None:
        return False
    if is_global_admin(user) or row.uploaded_by_id == user.id:
        return True
    if row.property_id is None:
        return False
    if property_id is not None:
        return row.property_id == property_id
    return has_property_access(db, row.property_id, user, level="read")


def other_references(db: Session, filename: str, exclude_reading_id: int | None = None) -> int:
    """Number of readings (other than ``exclude_reading_id``) pointing at the file."""
    names = (filename, f"uploads/{filename}")
    q = db.query(Reading).filter(
        or_(
            Reading.image_path.in_(names),
            Reading.serial_image_path.in_(names),
            Reading.image_path.like(f"%/{filename}"),
            Reading.serial_image_path.like(f"%/{filename}"),
        )
    )
    if exclude_reading_id is not None:
        q = q.filter(Reading.id != exclude_reading_id)
    return q.count()
