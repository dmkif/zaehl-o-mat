"""Session hardening: users.token_version and login_codes

``token_version`` is the per-user revocation counter embedded in every token
(F-007); ``login_codes`` holds hashed single-use codes exchanged by the SPA for
the session token so the JWT never travels in a URL (F-008).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-17
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("token_version", sa.Integer, nullable=False, server_default="0"))
    op.create_table(
        "login_codes",
        sa.Column("code_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_login_codes_expires_at", "login_codes", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_login_codes_expires_at", table_name="login_codes")
    op.drop_table("login_codes")
    op.drop_column("users", "token_version")
