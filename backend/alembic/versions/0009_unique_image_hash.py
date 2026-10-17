"""Unique image hash per meter (F-022).

The duplicate-image check was a check-then-insert; concurrent requests could
store the same photo twice.  This migration adds a partial unique index on
``(meter_id, image_hash) WHERE image_hash IS NOT NULL`` so the database decides.

Existing duplicates are resolved first **without deleting anything**: for every
``(meter_id, image_hash)`` group the oldest reading (lowest id) keeps its hash,
the hash of all later ones is set to NULL.  Their image links, values and notes
stay untouched; they merely stop counting as "the" reading of that photo.

Downgrade drops the index.  The NULLed hashes are not restored (they were the
duplicates by definition; a re-scan of the stored file recomputes them).

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

INDEX = "uq_readings_meter_image_hash"


def dedupe_image_hashes(conn) -> None:
    """NULL the hash of every reading except the oldest per (meter_id, image_hash)."""
    conn.execute(sa.text(
        """
        UPDATE readings SET image_hash = NULL
        WHERE image_hash IS NOT NULL
          AND id NOT IN (
              SELECT MIN(id) FROM readings
              WHERE image_hash IS NOT NULL
              GROUP BY meter_id, image_hash
          )
        """
    ))


def upgrade() -> None:
    dedupe_image_hashes(op.get_bind())
    op.create_index(
        INDEX, "readings", ["meter_id", "image_hash"], unique=True,
        postgresql_where=sa.text("image_hash IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="readings")
