"""Align PostgreSQL enum labels with the SQLAlchemy models (F-014).

SQLAlchemy stores enum *names* (``kwh``, ``liter``, ``smart_meter``, ...).
Migration 0000 created other labels (``kWh``, ``L``, ``modbus``, ...), so on a
migration-built database creating a meter with unit kWh/L or integration type
smart_meter/shelly failed with ``invalid input value for enum``.

Production may have been built by ``create_all`` (labels already right) or by
Alembic (labels missing), so this migration is idempotent: it only adds the
labels that are missing (``ADD VALUE IF NOT EXISTS``), never renames or removes
one.  Old labels stay; no row can contain them because the models never write
them.  ``ALTER TYPE ... ADD VALUE`` must not run inside a transaction block on
PostgreSQL < 12 and the new label cannot be used in the same transaction, hence
``autocommit_block``.

Revision ID: 0008
Revises: 0006
Create Date: 2026-10-17

NOTE: written in parallel to 0007 (session hardening).  After merging both, the
integrator must set ``down_revision = "0007"``.
"""
from alembic import op

revision = "0008"
down_revision = "0006"
branch_labels = None
depends_on = None

# PostgreSQL type name -> enum member names the models persist.
# Hard-coded (not imported from app.models) so history stays stable;
# tests/test_migration_enums.py fails when the models drift from this table.
ENUM_LABELS = {
    "userrole": ["superadmin", "admin", "manager", "user"],
    "propertyuserrole": ["admin", "manager", "user"],
    "metertype": ["water", "electricity", "oil"],
    "meterunit": ["m3", "kwh", "liter"],
    "integrationtype": ["manual", "smart_meter", "shelly", "homeassistant"],
    "readingsource": ["manual", "auto", "archived"],
}


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        for type_name, labels in ENUM_LABELS.items():
            exists = bind.exec_driver_sql(
                "SELECT 1 FROM pg_type WHERE typname = %s AND typtype = 'e'", (type_name,)
            ).first()
            if not exists:
                continue
            for label in labels:
                # identifiers/labels come from the constant table above, never from input
                bind.exec_driver_sql(f"ALTER TYPE {type_name} ADD VALUE IF NOT EXISTS '{label}'")  # nosec B608


def downgrade() -> None:
    """No-op: PostgreSQL cannot remove enum labels, and the added labels are
    exactly the ones the models need, so leaving them is harmless."""
