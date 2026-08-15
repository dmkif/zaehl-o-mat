"""
Retention / downsampling job.

Schedule: daily at 03:00 UTC

Policy:
  - Read at > 2 years old      → keep 1 average per hour  (archived)
  - Read at 91 days – 2 years  → keep 1 average per 15 min
  - Read at ≤ 90 days          → keep 1 average per minute
  - Manual readings             → never touched

Also removes uploaded image files that are no longer referenced by any
reading (e.g. scans that were never confirmed) after a grace period.
"""
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)

# Uploaded files younger than this are kept even when unreferenced — they may
# belong to an in-flight scan/bulk-import session awaiting user confirmation.
_ORPHAN_GRACE = timedelta(hours=24)


def run_retention(db: Session) -> None:
    now = datetime.now(timezone.utc)
    cutoff_90d = now - timedelta(days=90)
    cutoff_2y = now - timedelta(days=730)

    _downsample(db, older_than=cutoff_2y, bucket_minutes=60)
    _downsample(db, older_than=cutoff_90d, newer_than=cutoff_2y, bucket_minutes=15)
    _downsample(db, older_than=None, newer_than=cutoff_90d, bucket_minutes=1)

    db.commit()

    cleanup_orphan_uploads(db)
    logger.info("Retention job completed at %s", now.isoformat())


def cleanup_orphan_uploads(db: Session) -> None:
    """Delete files in the upload directory that no reading references."""
    upload_dir = Path(settings.upload_path)
    if not upload_dir.is_dir():
        return

    rows = db.execute(
        text("SELECT image_path, serial_image_path FROM readings")
    ).fetchall()
    referenced = {
        Path(p).name
        for row in rows
        for p in row
        if p
    }

    cutoff = datetime.now(timezone.utc) - _ORPHAN_GRACE
    removed = 0
    for f in upload_dir.iterdir():
        if not f.is_file() or f.name in referenced:
            continue
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            f.unlink(missing_ok=True)
            removed += 1
    if removed:
        logger.info("Removed %d orphaned upload file(s)", removed)


def _downsample(
    db: Session,
    older_than: datetime | None,
    bucket_minutes: int,
    newer_than: datetime | None = None,
) -> None:
    """
    For each meter and each time bucket, keep one averaged row and delete the rest.

    Touches source='auto' readings and — for the coarser tiers — previously
    downsampled source='archived' rows, so that rows aging from one tier into
    the next get re-bucketed (a 15-min archived row must still become hourly
    once it is older than two years).  Re-running on already-downsampled data
    is idempotent: one row per bucket stays one row.

    Uses raw SQL for performance (potentially millions of rows).
    """
    sources = "('auto')" if bucket_minutes == 1 else "('auto', 'archived')"
    conditions = [f"r.source IN {sources}"]
    params: dict = {"bucket": bucket_minutes}

    if older_than:
        conditions.append("r.read_at < :older_than")
        params["older_than"] = older_than
    if newer_than:
        conditions.append("r.read_at >= :newer_than")
        params["newer_than"] = newer_than

    where = " AND ".join(conditions)

    # Step 1: Create averaged rows (one per bucket) into a temp table
    db.execute(
        text(f"""
        CREATE TEMP TABLE _retention_keep AS
        SELECT
            meter_id,
            date_trunc('minute', read_at - ((EXTRACT(MINUTE FROM read_at)::int % :bucket) * interval '1 minute')) AS bucket_start,
            AVG(value) AS avg_value,
            MIN(read_at) AS keep_at
        FROM readings r
        WHERE {where}
        GROUP BY meter_id, bucket_start
        """),
        params,
    )

    # Step 2: Delete all auto-readings in range except one per bucket (we'll re-insert averaged)
    db.execute(
        text(f"""
        DELETE FROM readings r
        WHERE {where}
        """),
        params,
    )

    # Step 3: Re-insert averaged rows
    db.execute(
        text("""
        INSERT INTO readings (meter_id, value, read_at, source)
        SELECT meter_id, avg_value, keep_at, 'archived'
        FROM _retention_keep
        """)
    )

    db.execute(text("DROP TABLE IF EXISTS _retention_keep"))
