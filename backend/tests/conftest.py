"""
Shared pytest fixtures.

Uses SQLite in-memory so tests run without a real PostgreSQL instance.
PostgreSQL-specific types (UUID, partial index expressions) require dialect shims — see conftest.
"""
import os
# Set DATABASE_URL before any app module is imported so that app/database.py
# does not attempt to connect to PostgreSQL during the test run.
os.environ.setdefault("DATABASE_URL", "sqlite://")
# Use a deterministic test-only JWT secret (never used in production)
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-not-for-production")
# Redirect uploads to a writable temp directory during tests
os.environ.setdefault("UPLOAD_PATH", "/tmp/zaehlwart-test-uploads")
import uuid
from datetime import datetime, timezone
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, StaticPool
from sqlalchemy.orm import sessionmaker, Session

import app.database as _app_db
from app.database import Base, get_db
from app.main import app
from app.models import (
    Meter, MeterType, MeterUnit, IntegrationType,
    Property, PropertyUser, PropertyUserRole,
    Reading, ReadingSource,
    User, UserRole,
)
from app.auth import create_access_token

# ── SQLite in-memory engine ────────────────────────────────────────────────────

SQLALCHEMY_DATABASE_URL = "sqlite://"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

# SQLite doesn't enforce FK constraints by default — enable them
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, _):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="function")
def db() -> Generator[Session, None, None]:
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db: Session) -> Generator[TestClient, None, None]:
    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    # The lifespan calls _run_oil_price() immediately on startup, which:
    #   (a) makes a real HTTP request to heizoel24.de, and
    #   (b) creates a session via app.database.SessionLocal — a separate in-memory
    #       SQLite engine that has no tables, since create_all() was called on the
    #       test engine above.
    # Patching it to a no-op fixes both issues without affecting any endpoint tests.
    from unittest.mock import AsyncMock, patch
    with patch("app.main._run_oil_price", new_callable=AsyncMock):
        with TestClient(app) as c:
            yield c
    app.dependency_overrides.clear()


# ── Helper factories ───────────────────────────────────────────────────────────

def make_user(
    db: Session,
    *,
    role: UserRole = UserRole.user,
    username: str = "testuser",
    email: str = "test@example.com",
) -> User:
    user = User(
        id=uuid.uuid4(),
        username=username,
        email=email,
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def make_property(db: Session, name: str = "Test Property") -> Property:
    prop = Property(id=uuid.uuid4(), name=name)
    db.add(prop)
    db.commit()
    db.refresh(prop)
    return prop


def make_meter(
    db: Session,
    property_id: uuid.UUID,
    *,
    meter_type: MeterType = MeterType.water,
    unit: MeterUnit = MeterUnit.m3,
    name: str = "Wasserzähler",
) -> Meter:
    meter = Meter(
        id=uuid.uuid4(),
        property_id=property_id,
        meter_type=meter_type,
        unit=unit,
        name=name,
        integration_type=IntegrationType.manual,
    )
    db.add(meter)
    db.commit()
    db.refresh(meter)
    return meter


def make_reading(
    db: Session,
    meter_id: uuid.UUID,
    value: float,
    read_at: datetime | None = None,
    source: ReadingSource = ReadingSource.manual,
) -> Reading:
    r = Reading(
        meter_id=meter_id,
        value=value,
        read_at=read_at or datetime.now(timezone.utc),
        source=source,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def auth_headers(user: User) -> dict:
    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    return {"Authorization": f"Bearer {token}"}
