import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


# ── Enums ──────────────────────────────────────────────────────────────────────

class UserRole(str, enum.Enum):
    superadmin = "superadmin"
    admin = "admin"
    manager = "manager"
    user = "user"


class PropertyUserRole(str, enum.Enum):
    admin = "admin"
    manager = "manager"
    user = "user"


class MeterType(str, enum.Enum):
    water = "water"
    electricity = "electricity"
    oil = "oil"


class MeterUnit(str, enum.Enum):
    m3 = "m³"
    kwh = "kWh"
    liter = "L"


class IntegrationType(str, enum.Enum):
    manual = "manual"
    smart_meter = "smart_meter"
    shelly = "shelly"
    homeassistant = "homeassistant"


class ReadingSource(str, enum.Enum):
    manual = "manual"
    auto = "auto"
    archived = "archived"


class OilPriceSource(str, enum.Enum):
    heizoel_aktuell = "heizoel-aktuell"
    tankerkoenig = "tankerkoenig"
    custom = "custom"


# ── Models ─────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)  # null for OIDC-only users
    oidc_sub: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    ldap_dn: Mapped[str | None] = mapped_column(String(512), nullable=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False, default=UserRole.user)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    property_users: Mapped[list["PropertyUser"]] = relationship(back_populates="user")


class LdapRoleMapping(Base):
    __tablename__ = "ldap_role_mappings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ldap_group: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    app_role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False)


class Property(Base):
    __tablename__ = "properties"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    property_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    manager_ldap_group: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    property_users: Mapped[list["PropertyUser"]] = relationship(back_populates="property", cascade="all, delete-orphan")
    meters: Mapped[list["Meter"]] = relationship(back_populates="property", cascade="all, delete-orphan")
    oil_deliveries: Mapped[list["OilDelivery"]] = relationship(back_populates="property", cascade="all, delete-orphan")


class PropertyUser(Base):
    __tablename__ = "property_users"
    __table_args__ = (UniqueConstraint("property_id", "user_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[PropertyUserRole] = mapped_column(Enum(PropertyUserRole), nullable=False, default=PropertyUserRole.user)

    property: Mapped["Property"] = relationship(back_populates="property_users")
    user: Mapped["User"] = relationship(back_populates="property_users")


class Meter(Base):
    __tablename__ = "meters"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    meter_type: Mapped[MeterType] = mapped_column(Enum(MeterType), nullable=False)
    unit: Mapped[MeterUnit] = mapped_column(Enum(MeterUnit), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    serial_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    integration_type: Mapped[IntegrationType] = mapped_column(Enum(IntegrationType), nullable=False, default=IntegrationType.manual)
    integration_config: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: url, token, entity_id etc.
    # Lifecycle
    replaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("meters.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    property: Mapped["Property"] = relationship(back_populates="meters")
    readings: Mapped[list["Reading"]] = relationship(back_populates="meter", cascade="all, delete-orphan")
    price_entries: Mapped[list["PriceEntry"]] = relationship(back_populates="meter", cascade="all, delete-orphan")
    replaced_by: Mapped["Meter | None"] = relationship("Meter", foreign_keys=[replaced_by_id], remote_side="Meter.id")

    @property
    def is_replaced(self) -> bool:
        return self.replaced_at is not None


class Reading(Base):
    __tablename__ = "readings"
    __table_args__ = (
        Index("idx_readings_meter_read_at", "meter_id", "read_at"),
        Index("idx_readings_auto_source", "meter_id", "read_at",
              postgresql_where="source IN ('auto', 'archived')"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    meter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("meters.id", ondelete="CASCADE"), nullable=False)
    value: Mapped[float] = mapped_column(Numeric(precision=12, scale=3), nullable=False)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source: Mapped[ReadingSource] = mapped_column(Enum(ReadingSource), nullable=False, default=ReadingSource.manual)
    image_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    meter: Mapped["Meter"] = relationship(back_populates="readings")


class PriceEntry(Base):
    __tablename__ = "price_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    meter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("meters.id", ondelete="CASCADE"), nullable=False)
    price_per_unit: Mapped[float] = mapped_column(Numeric(precision=10, scale=4), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    meter: Mapped["Meter"] = relationship(back_populates="price_entries")


class OilDelivery(Base):
    __tablename__ = "oil_deliveries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    liters: Mapped[float] = mapped_column(Numeric(precision=10, scale=2), nullable=False)
    total_price: Mapped[float] = mapped_column(Numeric(precision=10, scale=2), nullable=False)
    price_per_liter: Mapped[float] = mapped_column(Numeric(precision=8, scale=4), nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    supplier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    property: Mapped["Property"] = relationship(back_populates="oil_deliveries")


class OilMarketPrice(Base):
    __tablename__ = "oil_market_prices"
    __table_args__ = (UniqueConstraint("price_date", "source"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    price_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price_per_100l: Mapped[float] = mapped_column(Numeric(precision=8, scale=2), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
