"""Spec 006 package B: property role escalation (F-005) and superadmin protection (F-006)."""
import logging

import pytest

from app.models import PropertyUser, PropertyUserRole, UserRole
from tests.conftest import auth_headers, make_meter, make_property, make_user


def _member(db, prop, user, role):
    db.add(PropertyUser(property_id=prop.id, user_id=user.id, role=role))
    db.commit()


def _role_of(db, prop, user):
    db.expire_all()
    a = db.query(PropertyUser).filter_by(property_id=prop.id, user_id=user.id).first()
    return a.role if a else None


@pytest.fixture
def setup(db):
    prop = make_property(db)
    padmin = make_user(db, username="padmin", email="padmin@x.de")
    manager = make_user(db, username="mgr", email="mgr@x.de")
    plain = make_user(db, username="plain", email="plain@x.de")
    _member(db, prop, padmin, PropertyUserRole.admin)
    _member(db, prop, manager, PropertyUserRole.manager)
    return prop, padmin, manager, plain


class TestPropertyManagerDenied:
    def test_manager_cannot_add_member(self, client, db, setup):
        prop, _, manager, plain = setup
        r = client.post(f"/api/properties/{prop.id}/users", headers=auth_headers(manager),
                        json={"user_id": str(plain.id), "role": "user"})
        assert r.status_code == 403
        assert _role_of(db, prop, plain) is None

    def test_manager_cannot_self_promote(self, client, db, setup):
        prop, _, manager, _ = setup
        r = client.post(f"/api/properties/{prop.id}/users", headers=auth_headers(manager),
                        json={"user_id": str(manager.id), "role": "admin"})
        assert r.status_code == 403
        assert _role_of(db, prop, manager) == PropertyUserRole.manager

    def test_manager_cannot_remove_member(self, client, db, setup):
        prop, padmin, manager, _ = setup
        r = client.delete(f"/api/properties/{prop.id}/users/{padmin.id}", headers=auth_headers(manager))
        assert r.status_code == 403
        assert _role_of(db, prop, padmin) == PropertyUserRole.admin

    def test_manager_cannot_patch_property(self, client, db, setup):
        prop, _, manager, _ = setup
        r = client.patch(f"/api/properties/{prop.id}", headers=auth_headers(manager), json={"name": "X"})
        assert r.status_code == 403

    def test_manager_keeps_meter_write(self, client, db, setup):
        prop, _, manager, _ = setup
        make_meter(db, prop.id)
        r = client.get(f"/api/properties/{prop.id}/meters", headers=auth_headers(manager))
        assert r.status_code == 200


class TestPropertyAdminAllowed:
    def test_property_admin_manages_members_and_property(self, client, db, setup):
        prop, padmin, manager, plain = setup
        h = auth_headers(padmin)
        assert client.patch(f"/api/properties/{prop.id}", headers=h, json={"name": "Neu"}).status_code == 200
        r = client.post(f"/api/properties/{prop.id}/users", headers=h,
                        json={"user_id": str(plain.id), "role": "user"})
        assert r.status_code == 201
        r = client.post(f"/api/properties/{prop.id}/users", headers=h,
                        json={"user_id": str(manager.id), "role": "user"})
        assert r.status_code == 201
        assert client.delete(f"/api/properties/{prop.id}/users/{plain.id}", headers=h).status_code == 204

    def test_global_admin_still_works(self, client, db, setup):
        prop, _, _, plain = setup
        admin = make_user(db, role=UserRole.admin, username="ga", email="ga@x.de")
        r = client.post(f"/api/properties/{prop.id}/users", headers=auth_headers(admin),
                        json={"user_id": str(plain.id), "role": "manager"})
        assert r.status_code == 201


class TestLastPropertyAdmin:
    def test_cannot_remove_last_admin(self, client, db, setup):
        prop, padmin, _, _ = setup
        admin = make_user(db, role=UserRole.admin, username="ga", email="ga@x.de")
        r = client.delete(f"/api/properties/{prop.id}/users/{padmin.id}", headers=auth_headers(admin))
        assert r.status_code == 409
        assert _role_of(db, prop, padmin) == PropertyUserRole.admin

    def test_cannot_demote_last_admin(self, client, db, setup):
        prop, padmin, _, _ = setup
        admin = make_user(db, role=UserRole.admin, username="ga", email="ga@x.de")
        r = client.post(f"/api/properties/{prop.id}/users", headers=auth_headers(admin),
                        json={"user_id": str(padmin.id), "role": "manager"})
        assert r.status_code == 409
        assert _role_of(db, prop, padmin) == PropertyUserRole.admin

    def test_property_admin_cannot_change_own_role(self, client, db, setup):
        prop, padmin, _, plain = setup
        other = make_user(db, username="p2", email="p2@x.de")
        _member(db, prop, other, PropertyUserRole.admin)
        r = client.post(f"/api/properties/{prop.id}/users", headers=auth_headers(padmin),
                        json={"user_id": str(padmin.id), "role": "user"})
        assert r.status_code == 403
        r = client.delete(f"/api/properties/{prop.id}/users/{padmin.id}", headers=auth_headers(padmin))
        assert r.status_code == 403
        assert _role_of(db, prop, padmin) == PropertyUserRole.admin

    def test_can_demote_admin_when_another_exists(self, client, db, setup):
        prop, padmin, _, _ = setup
        other = make_user(db, username="p2", email="p2@x.de")
        _member(db, prop, other, PropertyUserRole.admin)
        r = client.post(f"/api/properties/{prop.id}/users", headers=auth_headers(padmin),
                        json={"user_id": str(other.id), "role": "user"})
        assert r.status_code == 201


class TestSuperadminProtection:
    def test_admin_cannot_demote_superadmin(self, client, db):
        sa = make_user(db, role=UserRole.superadmin, username="sa", email="sa@x.de")
        admin = make_user(db, role=UserRole.admin, username="ga", email="ga@x.de")
        r = client.patch(f"/api/admin/users/{sa.id}/role", headers=auth_headers(admin), json={"role": "user"})
        assert r.status_code == 403
        db.refresh(sa)
        assert sa.role == UserRole.superadmin

    def test_admin_cannot_delete_superadmin(self, client, db):
        sa = make_user(db, role=UserRole.superadmin, username="sa", email="sa@x.de")
        admin = make_user(db, role=UserRole.admin, username="ga", email="ga@x.de")
        assert client.delete(f"/api/admin/users/{sa.id}", headers=auth_headers(admin)).status_code == 403

    def test_last_superadmin_cannot_be_demoted_even_by_itself(self, client, db):
        sa = make_user(db, role=UserRole.superadmin, username="sa", email="sa@x.de")
        r = client.patch(f"/api/admin/users/{sa.id}/role", headers=auth_headers(sa), json={"role": "admin"})
        assert r.status_code == 409
        db.refresh(sa)
        assert sa.role == UserRole.superadmin

    def test_last_superadmin_cannot_be_deleted_even_by_itself(self, client, db):
        sa = make_user(db, role=UserRole.superadmin, username="sa", email="sa@x.de")
        assert client.delete(f"/api/admin/users/{sa.id}", headers=auth_headers(sa)).status_code == 409

    def test_superadmin_can_demote_another_superadmin(self, client, db):
        sa = make_user(db, role=UserRole.superadmin, username="sa", email="sa@x.de")
        sb = make_user(db, role=UserRole.superadmin, username="sb", email="sb@x.de")
        r = client.patch(f"/api/admin/users/{sb.id}/role", headers=auth_headers(sa), json={"role": "admin"})
        assert r.status_code == 200
        assert r.json()["role"] == "admin"

    def test_superadmin_can_delete_another_superadmin(self, client, db):
        sa = make_user(db, role=UserRole.superadmin, username="sa", email="sa@x.de")
        sb = make_user(db, role=UserRole.superadmin, username="sb", email="sb@x.de")
        assert client.delete(f"/api/admin/users/{sb.id}", headers=auth_headers(sa)).status_code == 204


class TestSecurityEvents:
    def _events(self, caplog):
        return [r.getMessage() for r in caplog.records if r.name == "app.security"]

    def test_denied_membership_change_logged(self, client, db, setup, caplog):
        prop, _, manager, plain = setup
        with caplog.at_level(logging.INFO, logger="app.security"):
            client.post(f"/api/properties/{prop.id}/users", headers=auth_headers(manager),
                        json={"user_id": str(plain.id), "role": "user"})
        msgs = self._events(caplog)
        assert any("outcome=denied" in m and f"actor={manager.id}" in m for m in msgs)

    def test_membership_and_role_and_delete_logged_without_pii(self, client, db, setup, caplog):
        prop, padmin, _, plain = setup
        admin = make_user(db, role=UserRole.admin, username="ga", email="ga@x.de")
        victim = make_user(db, username="vic", email="vic@x.de")
        with caplog.at_level(logging.INFO, logger="app.security"):
            client.post(f"/api/properties/{prop.id}/users", headers=auth_headers(padmin),
                        json={"user_id": str(plain.id), "role": "user"})
            client.delete(f"/api/properties/{prop.id}/users/{plain.id}", headers=auth_headers(padmin))
            client.patch(f"/api/admin/users/{victim.id}/role", headers=auth_headers(admin), json={"role": "manager"})
            client.delete(f"/api/admin/users/{victim.id}", headers=auth_headers(admin))
        text = "\n".join(self._events(caplog))
        for ev in ("member_added", "member_removed", "user_role_changed", "user_deleted"):
            assert f"event={ev}" in text, ev
        assert "@x.de" not in text and "vic" not in text.replace("victim", "")
