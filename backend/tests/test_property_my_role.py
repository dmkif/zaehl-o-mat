"""Spec 014: GET /properties and GET /properties/{id} report the caller's role in each property (`my_role`)."""
import pytest

from app.models import PropertyUser, PropertyUserRole, UserRole
from tests.conftest import auth_headers, make_property, make_user


def _member(db, prop, user, role):
    db.add(PropertyUser(property_id=prop.id, user_id=user.id, role=role))
    db.commit()


@pytest.mark.parametrize("role", ["admin", "manager", "user"])
def test_member_role_reported_in_list_and_detail(client, db, role):
    prop = make_property(db, name="mine")
    user = make_user(db, role=UserRole.user)
    _member(db, prop, user, PropertyUserRole(role))
    listed = client.get("/api/properties", headers=auth_headers(user)).json()
    assert [p["my_role"] for p in listed] == [role]
    one = client.get(f"/api/properties/{prop.id}", headers=auth_headers(user)).json()
    assert one["my_role"] == role


@pytest.mark.parametrize("global_role", [UserRole.admin, UserRole.superadmin])
def test_global_admin_is_admin_everywhere(client, db, global_role):
    prop = make_property(db)
    admin = make_user(db, role=global_role, username="ga", email="ga@x.de")
    assert client.get("/api/properties", headers=auth_headers(admin)).json()[0]["my_role"] == "admin"
    assert client.get(f"/api/properties/{prop.id}", headers=auth_headers(admin)).json()["my_role"] == "admin"


def test_global_admin_with_lower_membership_still_admin(client, db):
    prop = make_property(db)
    admin = make_user(db, role=UserRole.admin, username="ga2", email="ga2@x.de")
    _member(db, prop, admin, PropertyUserRole.user)
    assert client.get(f"/api/properties/{prop.id}", headers=auth_headers(admin)).json()["my_role"] == "admin"


def test_global_manager_role_does_not_grant_property_role(client, db):
    """The role is the property membership, not the global role: a global manager who is only property user is 'user'."""
    prop = make_property(db)
    gm = make_user(db, role=UserRole.manager, username="gm", email="gm@x.de")
    _member(db, prop, gm, PropertyUserRole.user)
    assert client.get(f"/api/properties/{prop.id}", headers=auth_headers(gm)).json()["my_role"] == "user"


def test_roles_are_per_property(client, db):
    a, b = make_property(db, name="A"), make_property(db, name="B")
    user = make_user(db, role=UserRole.user)
    _member(db, a, user, PropertyUserRole.admin)
    _member(db, b, user, PropertyUserRole.user)
    roles = {p["name"]: p["my_role"] for p in client.get("/api/properties", headers=auth_headers(user)).json()}
    assert roles == {"A": "admin", "B": "user"}


def test_foreign_property_not_visible(client, db):
    prop = make_property(db)
    user = make_user(db, role=UserRole.user)
    assert client.get(f"/api/properties/{prop.id}", headers=auth_headers(user)).status_code == 403
    assert client.get("/api/properties", headers=auth_headers(user)).json() == []


def test_write_responses_carry_role_for_admin(client, db):
    admin = make_user(db, role=UserRole.admin, username="ga3", email="ga3@x.de")
    r = client.post("/api/properties", headers=auth_headers(admin), json={"name": "N"})
    assert r.status_code == 201 and r.json()["my_role"] == "admin"
