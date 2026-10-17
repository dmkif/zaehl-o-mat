"""Upload registry: who uploaded which stored meter image.

``upload_files`` binds every file written by scan / bulk-scan to its uploader
and (when known) the property it was scanned for, so a file name alone no
longer grants access to the file (F-004).  Readings that predate this table
keep working - serving falls back to the referencing reading's access rule.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-17
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "upload_files",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("filename", sa.String(128), nullable=False),
        sa.Column("property_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("properties.id", ondelete="CASCADE"), nullable=True),
        sa.Column("uploaded_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("filename"),
    )
    op.create_index("ix_upload_files_property_id", "upload_files", ["property_id"])


def downgrade() -> None:
    op.drop_index("ix_upload_files_property_id", table_name="upload_files")
    op.drop_table("upload_files")
