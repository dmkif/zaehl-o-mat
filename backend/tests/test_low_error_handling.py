"""Feature 011 / F-035 (and F-023 leftovers): database failures map to clean responses."""
import pytest
from sqlalchemy.exc import DataError, IntegrityError, InterfaceError, OperationalError

from app.models import Reading, UserRole
from tests.conftest import auth_headers, make_meter, make_property, make_user

SECRET = "host=db-internal.example password=hunter2"


def _url(p, m):
    return f"/api/properties/{p.id}/meters/{m.id}/readings/"


@pytest.fixture
def env(db):
    prop = make_property(db)
    meter = make_meter(db, prop.id)
    admin = make_user(db, role=UserRole.admin, username="adm", email="a@example.com")
    return prop, meter, admin


def _assert_clean(r):
    assert SECRET not in r.text and "hunter2" not in r.text and "Traceback" not in r.text
    assert "detail" in r.json()


def test_commit_operational_error_is_503(client, db, env, monkeypatch):
    prop, meter, admin = env
    def boom():
        raise OperationalError("INSERT ...", {}, Exception(SECRET))
    monkeypatch.setattr(db, "commit", boom)
    r = client.post(_url(prop, meter), headers=auth_headers(admin), json={"value": 1})
    assert r.status_code == 503
    assert r.headers.get("retry-after")
    _assert_clean(r)


def test_interface_error_is_503(client, db, env, monkeypatch):
    prop, meter, admin = env
    monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(InterfaceError("x", {}, Exception(SECRET))))
    r = client.post(_url(prop, meter), headers=auth_headers(admin), json={"value": 1})
    assert r.status_code == 503
    _assert_clean(r)


def test_data_error_is_422(client, db, env, monkeypatch):
    prop, meter, admin = env
    monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(DataError("x", {}, Exception(SECRET))))
    r = client.post(_url(prop, meter), headers=auth_headers(admin), json={"value": 1})
    assert r.status_code == 422
    _assert_clean(r)


def test_unexpected_integrity_error_is_409(client, db, env, monkeypatch):
    prop, meter, admin = env
    monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(IntegrityError("x", {}, Exception(SECRET))))
    r = client.patch(f"/api/properties/{prop.id}/meters/{meter.id}", headers=auth_headers(admin), json={"name": "n"})
    assert r.status_code == 409
    _assert_clean(r)


def test_unhandled_exception_is_generic_500(db, env, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    prop, meter, admin = env
    from app.database import get_db
    def broken():
        raise RuntimeError(SECRET)
    app.dependency_overrides[get_db] = broken
    from unittest.mock import AsyncMock, patch
    try:
        with patch("app.main._run_oil_price", new_callable=AsyncMock), \
                TestClient(app, raise_server_exceptions=False) as c:
            r = c.get(_url(prop, meter), headers=auth_headers(admin))
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 500
    _assert_clean(r)


def test_refresh_failure_after_commit_does_not_lose_acknowledgement(client, db, env, monkeypatch):
    prop, meter, admin = env
    def no_refresh(*a, **k):
        raise OperationalError("SELECT", {}, Exception(SECRET))
    monkeypatch.setattr(db, "refresh", no_refresh)
    r = client.post(_url(prop, meter), headers=auth_headers(admin), json={"value": 12.5})
    assert r.status_code == 201
    assert r.json()["id"] and float(r.json()["value"]) == 12.5
    assert db.query(Reading).count() == 1


def test_patch_reading_and_meter_survive_refresh_failure(client, db, env, monkeypatch):
    from tests.conftest import make_reading
    prop, meter, admin = env
    rd = make_reading(db, meter.id, 1.0)
    monkeypatch.setattr(db, "refresh", lambda *a, **k: (_ for _ in ()).throw(OperationalError("S", {}, Exception(SECRET))))
    r = client.patch(f"{_url(prop, meter)}{rd.id}", headers=auth_headers(admin), json={"value": 2})
    assert r.status_code == 200
    r = client.patch(f"/api/properties/{prop.id}/meters/{meter.id}", headers=auth_headers(admin), json={"name": "neu"})
    assert r.status_code == 200 and r.json()["name"] == "neu"
    r = client.post(f"/api/properties/{prop.id}/meters/", headers=auth_headers(admin),
                    json={"meter_type": "water", "unit": "m³", "name": "x"})
    assert r.status_code == 201
