"""
Tests for /api/admin

Covers:
  - GET  /api/admin/users               — list all users
  - PATCH /api/admin/users/{id}/role    — update user role
  - DELETE /api/admin/users/{id}        — delete user

Access-control rules under test:
  - unauthenticated → 401
  - role=user       → 403
  - role=admin      → granted for most ops
  - role=superadmin required to grant superadmin role
  - superadmin user cannot be deleted
"""
import uuid

import pytest

from app.models import UserRole
from tests.conftest import auth_headers, make_user


# ──────────────────────────────────────────────────────────────────────────────
# GET /api/admin/users
# ──────────────────────────────────────────────────────────────────────────────

class TestListUsers:
    def test_unauthenticated_returns_401(self, client, db):
        r = client.get("/api/admin/users")
        assert r.status_code == 401

    def test_regular_user_returns_403(self, client, db):
        user = make_user(db, role=UserRole.user)
        r = client.get("/api/admin/users", headers=auth_headers(user))
        assert r.status_code == 403

    def test_admin_sees_all_users(self, client, db):
        make_user(db, username="alice", email="alice@example.com")
        make_user(db, username="bob", email="bob@example.com")
        admin = make_user(db, role=UserRole.admin, username="admin", email="admin@example.com")
        r = client.get("/api/admin/users", headers=auth_headers(admin))
        assert r.status_code == 200
        usernames = {u["username"] for u in r.json()}
        assert {"alice", "bob", "admin"}.issubset(usernames)

    def test_superadmin_sees_all_users(self, client, db):
        make_user(db, username="regular", email="regular@example.com")
        sadmin = make_user(db, role=UserRole.superadmin, username="sysop", email="sysop@example.com")
        r = client.get("/api/admin/users", headers=auth_headers(sadmin))
        assert r.status_code == 200
        assert len(r.json()) >= 2


# ──────────────────────────────────────────────────────────────────────────────
# PATCH /api/admin/users/{id}/role
# ──────────────────────────────────────────────────────────────────────────────

class TestSetUserRole:
    def test_unauthenticated_returns_401(self, client, db):
        r = client.patch(f"/api/admin/users/{uuid.uuid4()}/role", json={"role": "admin"})
        assert r.status_code == 401

    def test_regular_user_returns_403(self, client, db):
        target = make_user(db, username="target", email="target@example.com")
        caller = make_user(db, role=UserRole.user, username="caller", email="caller@example.com")
        r = client.patch(
            f"/api/admin/users/{target.id}/role",
            json={"role": "admin"},
            headers=auth_headers(caller),
        )
        assert r.status_code == 403

    def test_admin_can_downgrade_user_role(self, client, db):
        target = make_user(db, role=UserRole.admin, username="target", email="target@example.com")
        admin = make_user(db, role=UserRole.admin, username="admin9", email="admin9@example.com")
        r = client.patch(
            f"/api/admin/users/{target.id}/role",
            json={"role": "user"},
            headers=auth_headers(admin),
        )
        assert r.status_code == 200
        assert r.json()["role"] == "user"

    def test_admin_cannot_grant_superadmin_role(self, client, db):
        target = make_user(db, username="target", email="target@example.com")
        admin = make_user(db, role=UserRole.admin, username="admin8", email="admin8@example.com")
        r = client.patch(
            f"/api/admin/users/{target.id}/role",
            json={"role": "superadmin"},
            headers=auth_headers(admin),
        )
        assert r.status_code == 403

    def test_superadmin_can_grant_superadmin_role(self, client, db):
        target = make_user(db, username="target2", email="target2@example.com")
        sadmin = make_user(
            db, role=UserRole.superadmin, username="sysop2", email="sysop2@example.com"
        )
        r = client.patch(
            f"/api/admin/users/{target.id}/role",
            json={"role": "superadmin"},
            headers=auth_headers(sadmin),
        )
        assert r.status_code == 200
        assert r.json()["role"] == "superadmin"

    def test_nonexistent_user_returns_404(self, client, db):
        admin = make_user(db, role=UserRole.admin, username="admin7", email="admin7@example.com")
        r = client.patch(
            f"/api/admin/users/{uuid.uuid4()}/role",
            json={"role": "user"},
            headers=auth_headers(admin),
        )
        assert r.status_code == 404


# ──────────────────────────────────────────────────────────────────────────────
# DELETE /api/admin/users/{id}
# ──────────────────────────────────────────────────────────────────────────────

class TestDeleteUser:
    def test_unauthenticated_returns_401(self, client, db):
        r = client.delete(f"/api/admin/users/{uuid.uuid4()}")
        assert r.status_code == 401

    def test_regular_user_returns_403(self, client, db):
        target = make_user(db, username="victim", email="victim@example.com")
        caller = make_user(db, role=UserRole.user, username="nobody", email="nobody@example.com")
        r = client.delete(f"/api/admin/users/{target.id}", headers=auth_headers(caller))
        assert r.status_code == 403

    def test_admin_can_delete_regular_user(self, client, db):
        target = make_user(db, username="gone", email="gone@example.com")
        admin = make_user(db, role=UserRole.admin, username="boss", email="boss@example.com")
        r = client.delete(f"/api/admin/users/{target.id}", headers=auth_headers(admin))
        assert r.status_code == 204

        # Confirm actually deleted
        r2 = client.get("/api/admin/users", headers=auth_headers(admin))
        usernames = {u["username"] for u in r2.json()}
        assert "gone" not in usernames

    def test_superadmin_cannot_be_deleted(self, client, db):
        sadmin = make_user(
            db, role=UserRole.superadmin, username="untouchable", email="untouchable@example.com"
        )
        admin = make_user(db, role=UserRole.admin, username="admin6", email="admin6@example.com")
        r = client.delete(f"/api/admin/users/{sadmin.id}", headers=auth_headers(admin))
        assert r.status_code == 403

    def test_nonexistent_user_returns_404(self, client, db):
        admin = make_user(db, role=UserRole.admin, username="admin5", email="admin5@example.com")
        r = client.delete(f"/api/admin/users/{uuid.uuid4()}", headers=auth_headers(admin))
        assert r.status_code == 404
