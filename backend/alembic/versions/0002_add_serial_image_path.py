"""add serial_image_path to readings

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-04
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "readings",
        sa.Column("serial_image_path", sa.String(512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("readings", "serial_image_path")
