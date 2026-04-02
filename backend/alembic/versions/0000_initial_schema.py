"""initial schema — all tables

Revision ID: 0000
Revises:
Create Date: 2026-04-02
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0000"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # users
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(150), nullable=False, unique=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(255), nullable=True),
        sa.Column("oidc_sub", sa.String(255), nullable=True, unique=True),
        sa.Column("ldap_dn", sa.String(512), nullable=True),
        sa.Column("role", sa.Enum("superadmin", "admin", "manager", "user", name="userrole"), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, default=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ldap_role_mappings
    op.create_table(
        "ldap_role_mappings",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ldap_group", sa.String(255), nullable=False, unique=True),
        sa.Column("app_role", sa.Enum("superadmin", "admin", "manager", "user", name="userrole"), nullable=False),
    )

    # properties
    op.create_table(
        "properties",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("address", sa.Text, nullable=True),
        sa.Column("property_type", sa.String(100), nullable=True),
        sa.Column("manager_ldap_group", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # property_users
    op.create_table(
        "property_users",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("property_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Enum("admin", "manager", "user", name="propertyuserrole"), nullable=False),
        sa.UniqueConstraint("property_id", "user_id"),
    )

    # meters
    op.create_table(
        "meters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("property_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False),
        sa.Column("meter_type", sa.Enum("electricity", "water", "gas", "oil", "heat", "other", name="metertype"), nullable=False),
        sa.Column("unit", sa.Enum("kWh", "m3", "L", "MWh", "GJ", "other", name="meterunit"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("serial_number", sa.String(100), nullable=True),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("integration_type", sa.Enum("manual", "homeassistant", "modbus", "api", name="integrationtype"), nullable=False),
        sa.Column("integration_config", sa.Text, nullable=True),
        sa.Column("replaced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("meters.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # readings
    op.create_table(
        "readings",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("meter_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("meters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("value", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("source", sa.Enum("manual", "auto", "archived", name="readingsource"), nullable=False),
        sa.Column("image_path", sa.String(512), nullable=True),
        sa.Column("note", sa.Text, nullable=True),
    )
    op.create_index("idx_readings_meter_read_at", "readings", ["meter_id", "read_at"])

    # price_entries
    op.create_table(
        "price_entries",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("meter_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("meters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("price_per_unit", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # oil_deliveries
    op.create_table(
        "oil_deliveries",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("property_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False),
        sa.Column("liters", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("total_price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("price_per_liter", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supplier", sa.String(255), nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # oil_market_prices
    op.create_table(
        "oil_market_prices",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("price_date", sa.Date, nullable=False),
        sa.Column("price_per_100l", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("price_date", "source"),
    )


def downgrade() -> None:
    op.drop_table("oil_market_prices")
    op.drop_table("oil_deliveries")
    op.drop_table("price_entries")
    op.drop_index("idx_readings_meter_read_at")
    op.drop_table("readings")
    op.drop_table("meters")
    op.drop_table("property_users")
    op.drop_table("properties")
    op.drop_table("ldap_role_mappings")
    op.drop_table("users")
