"""
Tests for /api/properties/{property_id}/meters

Covers:
  - List meters (include_replaced filter)
  - Create / Get / Patch / Delete
  - Access control (admin vs. property manager vs. plain user)
  - Replaced meter lifecycle
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.models import MeterType, MeterUnit, PropertyUser, PropertyUserRole, UserRole
from tests.conftest import auth_headers, make_meter, make_property, make_user


class TestListMeters:
    def test_unauthenticated_returns_401(self, client, db):
        prop = make_property(db)
        r = client.get(f"/api/properties/{prop.id}/meters")
        assert r.status_code == 401

    def test_admin_lists_meters(self, client, db):
        prop = make_property(db)
        make_meter(db, prop.id, name="Z1")
        make_meter(db, prop.id, name="Z2")
        admin = make_user(db, role=UserRole.admin)
        r = client.get(f"/api/properties/{prop.id}/meters", headers=auth_headers(admin))
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_replaced_meters_hidden_by_default(self, client, db):
        from app.models import Meter
        prop = make_property(db)
        active = make_meter(db, prop.id, name="Aktiv")
        replaced = make_meter(db, prop.id, name="Ersetzt")
        # Mark replaced_at directly on the model
        replaced.replaced_at = datetime.now(timezone.utc)
        db.commit()

        admin = make_user(db, role=UserRole.admin)
        r = client.get(f"/api/properties/{prop.id}/meters", headers=auth_headers(admin))
        assert r.status_code == 200
        names = [m["name"] for m in r.json()]
        assert "Aktiv" in names
        assert "Ersetzt" not in names

    def test_include_replaced_shows_all(self, client, db):
        from app.models import Meter
        prop = make_property(db)
        make_meter(db, prop.id, name="Aktiv")
        replaced = make_meter(db, prop.id, name="Ersetzt")
        replaced.replaced_at = datetime.now(timezone.utc)
        db.commit()

        admin = make_user(db, role=UserRole.admin)
        r = client.get(
            f"/api/properties/{prop.id}/meters",
            params={"include_replaced": True},
            headers=auth_headers(admin),
        )
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_assigned_user_can_list_meters(self, client, db):
        prop = make_property(db)
        make_meter(db, prop.id)
        user = make_user(db, role=UserRole.user)
        db.add(PropertyUser(property_id=prop.id, user_id=user.id, role=PropertyUserRole.user))
        db.commit()
        r = client.get(f"/api/properties/{prop.id}/meters", headers=auth_headers(user))
        assert r.status_code == 200
        assert len(r.json()) == 1

    def test_unassigned_user_gets_403(self, client, db):
        prop = make_property(db)
        user = make_user(db, role=UserRole.user)
        r = client.get(f"/api/properties/{prop.id}/meters", headers=auth_headers(user))
        assert r.status_code == 403


class TestCreateMeter:
    def test_admin_can_create_meter(self, client, db):
        prop = make_property(db)
        admin = make_user(db, role=UserRole.admin)
        r = client.post(
            f"/api/properties/{prop.id}/meters",
            headers=auth_headers(admin),
            json={
                "name": "Neuer Zähler",
                "meter_type": "water",
                "unit": "m³",
                "integration_type": "manual",
            },
        )
        assert r.status_code == 201
        assert r.json()["name"] == "Neuer Zähler"
        assert r.json()["meter_type"] == "water"

    def test_property_manager_can_create_meter(self, client, db):
        prop = make_property(db)
        user = make_user(db, role=UserRole.user)
        db.add(PropertyUser(property_id=prop.id, user_id=user.id, role=PropertyUserRole.manager))
        db.commit()
        r = client.post(
            f"/api/properties/{prop.id}/meters",
            headers=auth_headers(user),
            json={
                "name": "Manager Zähler",
                "meter_type": "electricity",
                "unit": "kWh",
                "integration_type": "manual",
            },
        )
        assert r.status_code == 201

    def test_property_user_cannot_create_meter(self, client, db):
        prop = make_property(db)
        user = make_user(db, role=UserRole.user)
        db.add(PropertyUser(property_id=prop.id, user_id=user.id, role=PropertyUserRole.user))
        db.commit()
        r = client.post(
            f"/api/properties/{prop.id}/meters",
            headers=auth_headers(user),
            json={"name": "Verboten", "meter_type": "water", "unit": "m³", "integration_type": "manual"},
        )
        assert r.status_code == 403

    def test_unknown_property_returns_404(self, client, db):
        admin = make_user(db, role=UserRole.admin)
        r = client.post(
            f"/api/properties/{uuid.uuid4()}/meters",
            headers=auth_headers(admin),
            json={"name": "X", "meter_type": "water", "unit": "m³", "integration_type": "manual"},
        )
        assert r.status_code == 404


class TestGetMeter:
    def test_admin_can_get_meter(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id, name="Z-Read")
        admin = make_user(db, role=UserRole.admin)
        r = client.get(f"/api/properties/{prop.id}/meters/{meter.id}", headers=auth_headers(admin))
        assert r.status_code == 200
        assert r.json()["name"] == "Z-Read"

    def test_nonexistent_meter_returns_404(self, client, db):
        prop = make_property(db)
        admin = make_user(db, role=UserRole.admin)
        r = client.get(
            f"/api/properties/{prop.id}/meters/{uuid.uuid4()}",
            headers=auth_headers(admin),
        )
        assert r.status_code == 404


class TestUpdateMeter:
    def test_admin_can_update_meter(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id, name="Old Name")
        admin = make_user(db, role=UserRole.admin)
        r = client.patch(
            f"/api/properties/{prop.id}/meters/{meter.id}",
            headers=auth_headers(admin),
            json={"name": "New Name"},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "New Name"

    def test_plain_user_cannot_update_meter(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        user = make_user(db, role=UserRole.user)
        db.add(PropertyUser(property_id=prop.id, user_id=user.id, role=PropertyUserRole.user))
        db.commit()
        r = client.patch(
            f"/api/properties/{prop.id}/meters/{meter.id}",
            headers=auth_headers(user),
            json={"name": "Versuch"},
        )
        assert r.status_code == 403


class TestDeleteMeter:
    def test_admin_can_delete_meter(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        admin = make_user(db, role=UserRole.admin)
        r = client.delete(f"/api/properties/{prop.id}/meters/{meter.id}", headers=auth_headers(admin))
        assert r.status_code == 204

    def test_plain_user_cannot_delete_meter(self, client, db):
        prop = make_property(db)
        meter = make_meter(db, prop.id)
        user = make_user(db, role=UserRole.user)
        db.add(PropertyUser(property_id=prop.id, user_id=user.id, role=PropertyUserRole.user))
        db.commit()
        r = client.delete(f"/api/properties/{prop.id}/meters/{meter.id}", headers=auth_headers(user))
        assert r.status_code == 403
