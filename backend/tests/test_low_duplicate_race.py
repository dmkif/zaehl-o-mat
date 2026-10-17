"""Feature 011 / F-022: duplicate image check is backed by a unique index."""
import hashlib
import uuid

import pytest
from sqlalchemy import inspect

from app.models import Reading, UploadFile, UserRole
from tests.conftest import auth_headers, make_meter, make_property, make_user
from tests.test_uploads import _MINIMAL_JPEG


def _url(p, m):
    return f"/api/properties/{p.id}/meters/{m.id}/readings/"


@pytest.fixture
def env(db, tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.upload_path", str(tmp_path))
    prop = make_property(db)
    meter = make_meter(db, prop.id)
    admin = make_user(db, role=UserRole.admin, username="adm", email="a@example.com")

    def upload():
        name = f"{uuid.uuid4()}.jpg"
        (tmp_path / name).write_bytes(_MINIMAL_JPEG)
        db.add(UploadFile(filename=name, property_id=prop.id, uploaded_by_id=admin.id))
        db.commit()
        return f"uploads/{name}"

    return prop, meter, admin, upload


def test_partial_unique_index_declared():
    idx = {i.name: i for i in Reading.__table__.indexes}
    ix = idx["uq_readings_meter_image_hash"]
    assert ix.unique
    assert [c.name for c in ix.columns] == ["meter_id", "image_hash"]


def test_db_rejects_second_row_with_same_hash(db, env):
    from sqlalchemy.exc import IntegrityError
    from datetime import datetime, timezone
    prop, meter, *_ = env
    for _ in range(2):
        db.add(Reading(meter_id=meter.id, value=1, read_at=datetime.now(timezone.utc), image_hash="a" * 64))
        if _ == 0:
            db.commit()
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    # NULL hashes never collide
    for _ in range(2):
        db.add(Reading(meter_id=meter.id, value=1, read_at=datetime.now(timezone.utc)))
    db.commit()


def test_sequential_duplicate_still_409(client, env):
    prop, meter, admin, upload = env
    path = upload()
    assert client.post(_url(prop, meter), headers=auth_headers(admin), json={"value": 1, "image_path": path}).status_code == 201
    r = client.post(_url(prop, meter), headers=auth_headers(admin), json={"value": 2, "image_path": path})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "duplicate_image"


def test_lost_race_is_409_with_existing_reading(client, db, env, monkeypatch):
    """The pre-check misses (race): the insert hits the unique index -> same 409 as the sequential case."""
    prop, meter, admin, upload = env
    path = upload()
    first = client.post(_url(prop, meter), headers=auth_headers(admin), json={"value": 1, "image_path": path})
    assert first.status_code == 201
    monkeypatch.setattr("app.routers.readings._precheck_duplicate", lambda *a, **k: None)
    r = client.post(_url(prop, meter), headers=auth_headers(admin), json={"value": 2, "image_path": path})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "duplicate_image"
    assert detail["reading_id"] == first.json()["id"]
    assert db.query(Reading).count() == 1
