"""Remove unused LDAP artifacts.

LDAP authentication was never implemented — the table and columns were dead
schema. Auth is OIDC (+ env-based superadmin) only.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-09
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("ldap_role_mappings")
    op.drop_column("users", "ldap_dn")
    op.drop_column("properties", "manager_ldap_group")


def downgrade() -> None:
    op.add_column("properties", sa.Column("manager_ldap_group", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("ldap_dn", sa.String(512), nullable=True))
    op.create_table(
        "ldap_role_mappings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ldap_group", sa.String(255), nullable=False, unique=True),
        sa.Column(
            "app_role",
            sa.Enum("superadmin", "admin", "manager", "user", name="userrole"),
            nullable=False,
        ),
    )
