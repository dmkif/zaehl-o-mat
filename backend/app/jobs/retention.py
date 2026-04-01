"""
Retention / downsampling job.

Schedule: daily at 03:00 UTC

Policy:
  - Read at > 2 years old      → keep 1 average per hour  (archived)
  - Read at 91 days – 2 years  → keep 1 average per 15 min
  - Read at ≤ 90 days          → keep 1 average per minute
  - Manual readings             → never touched
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def run_retention(db: Session) -> None:
    now = datetime.now(timezone.utc)
    cutoff_90d = now - timedelta(days=90)
    cutoff_2y = now - timedelta(days=730)

    _downsample(db, older_than=cutoff_2y, bucket_minutes=60)
    _downsample(db, older_than=cutoff_90d, newer_than=cutoff_2y, bucket_minutes=15)
    _downsample(db, older_than=None, newer_than=cutoff_90d, bucket_minutes=1)

    db.commit()
    logger.info("Retention job completed at %s", now.isoformat())


def _downsample(
    db: Session,
    older_than: datetime | None,
    bucket_minutes: int,
    newer_than: datetime | None = None,
) -> None:
    """
    For each meter and each time bucket, keep one averaged row and delete the rest.
    Only touches source='auto' readings.
    Uses raw SQL for performance (potentially millions of rows).
    """
    # Build time range filter
    conditions = ["r.source = 'auto'"]
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
