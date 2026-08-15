"""add image_hash to readings

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "readings",
        sa.Column("image_hash", sa.String(64), nullable=True),
    )
    op.create_index("idx_readings_image_hash", "readings", ["image_hash"])


def downgrade() -> None:
    op.drop_index("idx_readings_image_hash", table_name="readings")
    op.drop_column("readings", "image_hash")
