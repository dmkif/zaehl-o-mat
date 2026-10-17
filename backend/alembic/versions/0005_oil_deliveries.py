"""Oil deliveries: optional prices, creator, dismissed jumps.

Prices become optional so a delivery can be recorded without the invoice.
``oil_delivery_dismissals`` remembers jumps between two readings that the
user declared a reading error, so they are not offered as deliveries again.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-17
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("oil_deliveries", "total_price", existing_type=sa.Numeric(10, 2), nullable=True)
    op.alter_column("oil_deliveries", "price_per_liter", existing_type=sa.Numeric(8, 4), nullable=True)
    op.add_column(
        "oil_deliveries",
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_table(
        "oil_delivery_dismissals",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("meter_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("meters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_reading_id", sa.BigInteger, sa.ForeignKey("readings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_reading_id", sa.BigInteger, sa.ForeignKey("readings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dismissed_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("from_reading_id", "to_reading_id"),
    )


def downgrade() -> None:
    op.drop_table("oil_delivery_dismissals")
    op.drop_column("oil_deliveries", "created_by_id")
    # Deliveries recorded without a price get 0 so the NOT NULL constraint can return.
    op.execute("UPDATE oil_deliveries SET total_price = 0 WHERE total_price IS NULL")
    op.execute("UPDATE oil_deliveries SET price_per_liter = 0 WHERE price_per_liter IS NULL")
    op.alter_column("oil_deliveries", "total_price", existing_type=sa.Numeric(10, 2), nullable=False)
    op.alter_column("oil_deliveries", "price_per_liter", existing_type=sa.Numeric(8, 4), nullable=False)
