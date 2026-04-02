"""
Tests for /api/properties

Covers:
  - Admin can list all properties
  - Regular user only sees assigned properties
  - Unauthenticated request returns 401
  - Admin can create a property
  - Non-admin cannot create a property (403)
  - GET /properties/{id} access control
  - PATCH updates only allowed fields
  - DELETE removes property
  - Property user assignment: add, update role, remove
"""
import uuid

import pytest

from app.models import PropertyUser, PropertyUserRole, UserRole
from tests.conftest import auth_headers, make_property, make_user


class TestListProperties:
    def test_unauthenticated_returns_401(self, client):
        r = client.get("/api/properties")
        assert r.status_code == 401

    def test_admin_sees_all_properties(self, client, db):
        make_property(db, name="Objekt A")
        make_property(db, name="Objekt B")
        admin = make_user(db, role=UserRole.admin, username="admin1", email="admin1@x.de")
        r = client.get("/api/properties", headers=auth_headers(admin))
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_regular_user_sees_only_assigned_properties(self, client, db):
        prop_a = make_property(db, name="Assigned")
        make_property(db, name="Not Assigned")
        user = make_user(db, role=UserRole.user)
        pu = PropertyUser(property_id=prop_a.id, user_id=user.id, role=PropertyUserRole.user)
        db.add(pu)
        db.commit()
        r = client.get("/api/properties", headers=auth_headers(user))
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["name"] == "Assigned"

    def test_user_with_no_assignments_sees_empty_list(self, client, db):
        make_property(db)
        user = make_user(db, role=UserRole.user)
        r = client.get("/api/properties", headers=auth_headers(user))
        assert r.status_code == 200
        assert r.json() == []


class TestCreateProperty:
    def test_admin_can_create_property(self, client, db):
        admin = make_user(db, role=UserRole.admin)
        r = client.post(
            "/api/properties",
            headers=auth_headers(admin),
            json={"name": "Neues Objekt", "address": "Musterstr. 1"},
        )
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "Neues Objekt"
        assert data["address"] == "Musterstr. 1"

    def test_regular_user_cannot_create_property(self, client, db):
        user = make_user(db, role=UserRole.user)
        r = client.post(
            "/api/properties",
            headers=auth_headers(user),
            json={"name": "Verboten"},
        )
        assert r.status_code == 403

    def test_manager_cannot_create_property(self, client, db):
        manager = make_user(db, role=UserRole.manager)
        r = client.post(
            "/api/properties",
            headers=auth_headers(manager),
            json={"name": "Verboten"},
        )
        assert r.status_code == 403

    def test_create_without_name_returns_422(self, client, db):
        admin = make_user(db, role=UserRole.admin)
        r = client.post("/api/properties", headers=auth_headers(admin), json={})
        assert r.status_code == 422


class TestGetProperty:
    def test_admin_can_get_any_property(self, client, db):
        prop = make_property(db, name="Prop X")
        admin = make_user(db, role=UserRole.admin)
        r = client.get(f"/api/properties/{prop.id}", headers=auth_headers(admin))
        assert r.status_code == 200
        assert r.json()["name"] == "Prop X"

    def test_assigned_user_can_get_property(self, client, db):
        prop = make_property(db)
        user = make_user(db, role=UserRole.user)
        db.add(PropertyUser(property_id=prop.id, user_id=user.id, role=PropertyUserRole.user))
        db.commit()
        r = client.get(f"/api/properties/{prop.id}", headers=auth_headers(user))
        assert r.status_code == 200

    def test_unassigned_user_gets_403(self, client, db):
        prop = make_property(db)
        user = make_user(db, role=UserRole.user)
        r = client.get(f"/api/properties/{prop.id}", headers=auth_headers(user))
        assert r.status_code == 403

    def test_nonexistent_property_returns_404(self, client, db):
        admin = make_user(db, role=UserRole.admin)
        r = client.get(f"/api/properties/{uuid.uuid4()}", headers=auth_headers(admin))
        assert r.status_code == 404


class TestUpdateProperty:
    def test_admin_can_update_property(self, client, db):
        prop = make_property(db, name="Alt")
        admin = make_user(db, role=UserRole.admin)
        r = client.patch(
            f"/api/properties/{prop.id}",
            headers=auth_headers(admin),
            json={"name": "Neu"},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "Neu"

    def test_property_manager_can_update(self, client, db):
        prop = make_property(db, name="Alt")
        user = make_user(db, role=UserRole.user)
        db.add(PropertyUser(property_id=prop.id, user_id=user.id, role=PropertyUserRole.manager))
        db.commit()
        r = client.patch(
            f"/api/properties/{prop.id}",
            headers=auth_headers(user),
            json={"name": "Geändert"},
        )
        assert r.status_code == 200

    def test_property_user_cannot_update(self, client, db):
        prop = make_property(db)
        user = make_user(db, role=UserRole.user)
        db.add(PropertyUser(property_id=prop.id, user_id=user.id, role=PropertyUserRole.user))
        db.commit()
        r = client.patch(
            f"/api/properties/{prop.id}",
            headers=auth_headers(user),
            json={"name": "Versuch"},
        )
        assert r.status_code == 403


class TestDeleteProperty:
    def test_admin_can_delete_property(self, client, db):
        prop = make_property(db)
        admin = make_user(db, role=UserRole.admin)
        r = client.delete(f"/api/properties/{prop.id}", headers=auth_headers(admin))
        assert r.status_code == 204

    def test_regular_user_cannot_delete(self, client, db):
        prop = make_property(db)
        user = make_user(db, role=UserRole.user)
        r = client.delete(f"/api/properties/{prop.id}", headers=auth_headers(user))
        assert r.status_code == 403


class TestPropertyUsers:
    def test_admin_can_add_user_to_property(self, client, db):
        prop = make_property(db)
        admin = make_user(db, role=UserRole.admin, username="admin2", email="admin2@x.de")
        target = make_user(db, role=UserRole.user, username="target", email="target@x.de")
        r = client.post(
            f"/api/properties/{prop.id}/users",
            headers=auth_headers(admin),
            json={"user_id": str(target.id), "role": "user"},
        )
        assert r.status_code == 201
        assert r.json()["role"] == "user"

    def test_adding_twice_updates_role(self, client, db):
        prop = make_property(db)
        admin = make_user(db, role=UserRole.admin, username="admin3", email="admin3@x.de")
        target = make_user(db, role=UserRole.user, username="utarget", email="utarget@x.de")
        db.add(PropertyUser(property_id=prop.id, user_id=target.id, role=PropertyUserRole.user))
        db.commit()
        r = client.post(
            f"/api/properties/{prop.id}/users",
            headers=auth_headers(admin),
            json={"user_id": str(target.id), "role": "manager"},
        )
        assert r.status_code == 201
        assert r.json()["role"] == "manager"

    def test_remove_user_from_property(self, client, db):
        prop = make_property(db)
        admin = make_user(db, role=UserRole.admin, username="admin4", email="admin4@x.de")
        target = make_user(db, role=UserRole.user, username="toremove", email="toremove@x.de")
        db.add(PropertyUser(property_id=prop.id, user_id=target.id, role=PropertyUserRole.user))
        db.commit()
        r = client.delete(
            f"/api/properties/{prop.id}/users/{target.id}",
            headers=auth_headers(admin),
        )
        assert r.status_code == 204
