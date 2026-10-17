"""Feature 011: F-021, F-023, F-024, F-037 - validation and data integrity."""
import json
import uuid

import pytest

from app.models import Meter, PropertyUser, PropertyUserRole, Reading, ReadingSource, User, UserRole
from tests.conftest import auth_headers, make_meter, make_property, make_reading, make_user


def _rurl(p, m):
    return f"/api/properties/{p.id}/meters/{m.id}/readings/"


@pytest.fixture
def setup(db):
    prop = make_property(db)
    meter = make_meter(db, prop.id)
    admin = make_user(db, role=UserRole.admin, username="adm", email="adm@example.com")
    return prop, meter, admin


class TestReadingValidation:
    @pytest.mark.parametrize("raw", [
        '{"value": 1e100000}', '{"value": "1' + "0" * 400 + '"}', '{"value": NaN}', '{"value": Infinity}',
        '{"value": "NaN"}', '{"value": 1000000000}', '{"value": -1000000000}', '{"value": null}',
    ])
    def test_bad_numbers_are_422(self, client, setup, raw):
        prop, meter, admin = setup
        r = client.post(_rurl(prop, meter), headers={**auth_headers(admin), "Content-Type": "application/json"},
                        content=raw)
        assert r.status_code == 422, raw

    def test_value_quantised_to_three_decimals(self, client, setup):
        prop, meter, admin = setup
        r = client.post(_rurl(prop, meter), headers=auth_headers(admin), json={"value": "12.34567"})
        assert r.status_code == 201
        assert r.json()["value"] == "12.346"

    def test_max_value_accepted(self, client, setup):
        prop, meter, admin = setup
        r = client.post(_rurl(prop, meter), headers=auth_headers(admin), json={"value": "999999999.999"})
        assert r.status_code == 201

    @pytest.mark.parametrize("note", ["a\x00b", "x" * 5001, "bad\ud800surrogate"])
    def test_bad_note_is_422_and_body_serialisable(self, client, setup, note):
        prop, meter, admin = setup
        payload = json.dumps({"value": 1, "note": note})  # ensure_ascii keeps the lone surrogate as \ud800
        r = client.post(_rurl(prop, meter), headers={**auth_headers(admin), "Content-Type": "application/json"},
                        content=payload)
        assert r.status_code == 422
        r.json()

    def test_patch_value_bounds(self, client, setup, db):
        prop, meter, admin = setup
        rd = make_reading(db, meter.id, 5.0)
        r = client.patch(f"{_rurl(prop, meter)}{rd.id}", headers={**auth_headers(admin), "Content-Type": "application/json"},
                         content='{"value": 1e30}')
        assert r.status_code == 422

    @pytest.mark.parametrize("offset", ["9" * 40, "2147483648"])
    def test_huge_offset_is_422(self, client, setup, offset):
        prop, meter, admin = setup
        r = client.get(_rurl(prop, meter) + f"?offset={offset}", headers=auth_headers(admin))
        assert r.status_code == 422


class TestSourceServerDerived:
    @pytest.mark.parametrize("src", ["auto", "archived", "manual"])
    def test_client_cannot_set_source(self, client, setup, db, src):
        prop, meter, admin = setup
        r = client.post(_rurl(prop, meter), headers=auth_headers(admin), json={"value": 3, "source": src})
        assert r.status_code == 201
        assert r.json()["source"] == "manual"
        assert db.query(Reading).one().source == ReadingSource.manual

    def test_unknown_source_still_422(self, client, setup):
        prop, meter, admin = setup
        r = client.post(_rurl(prop, meter), headers=auth_headers(admin), json={"value": 3, "source": "ocr"})
        assert r.status_code == 422

    def test_retention_never_touches_evidence_rows(self):
        from unittest.mock import MagicMock
        from app.jobs.retention import _downsample

        db = MagicMock()
        _downsample(db, older_than=None, bucket_minutes=1)
        sql = " ".join(str(c.args[0]) for c in db.execute.call_args_list)
        for col in ("image_path IS NULL", "serial_image_path IS NULL", "note IS NULL"):
            assert col in sql


def _murl(p):
    return f"/api/properties/{p.id}/meters/"


class TestMeterValidation:
    def _body(self, **kw):
        return {"meter_type": "water", "unit": "m³", "name": "W", **kw}

    @pytest.mark.parametrize("field,value", [
        ("name", "x" * 256), ("name", "a\x00b"), ("serial_number", "x" * 101), ("location", "x" * 256),
        ("notes", "x" * 5001), ("name", "lone\ud800"),
    ])
    def test_create_string_bounds(self, client, setup, field, value):
        prop, _, admin = setup
        payload = json.dumps(self._body(**{field: value}))
        r = client.post(_murl(prop), headers={**auth_headers(admin), "Content-Type": "application/json"}, content=payload)
        assert r.status_code == 422
        r.json()

    @pytest.mark.parametrize("cfg", ["text", 5, [1, 2], True])
    def test_integration_config_must_be_object(self, client, setup, cfg):
        prop, _, admin = setup
        r = client.post(_murl(prop), headers=auth_headers(admin), json=self._body(integration_config=cfg))
        assert r.status_code == 422

    def test_integration_config_object_is_stored_as_json_and_redacted(self, client, setup, db):
        prop, _, admin = setup
        r = client.post(_murl(prop), headers=auth_headers(admin),
                        json=self._body(integration_type="shelly", integration_config={"url": "http://x", "token": "t"}))
        assert r.status_code == 201
        assert r.json()["integration_config"] is True
        stored = db.query(Meter).filter(Meter.id == uuid.UUID(r.json()["id"])).one().integration_config
        assert json.loads(stored) == {"url": "http://x", "token": "t"}

    def test_integration_config_too_large(self, client, setup):
        prop, _, admin = setup
        r = client.post(_murl(prop), headers=auth_headers(admin),
                        json=self._body(integration_config={"k": "v" * 9000}))
        assert r.status_code == 422

    def test_integration_config_null_ok(self, client, setup):
        prop, _, admin = setup
        r = client.post(_murl(prop), headers=auth_headers(admin), json=self._body(integration_config=None))
        assert r.status_code == 201
        assert r.json()["integration_config"] is None

    def test_patch_dict_config_no_500(self, client, setup):
        prop, meter, admin = setup
        r = client.patch(_murl(prop) + str(meter.id), headers=auth_headers(admin),
                         json={"integration_config": {"a": 1}})
        assert r.status_code == 200

    @pytest.mark.parametrize("field", ["name", "integration_type"])
    def test_patch_null_on_required_is_422(self, client, setup, field):
        prop, meter, admin = setup
        r = client.patch(_murl(prop) + str(meter.id), headers=auth_headers(admin), json={field: None})
        assert r.status_code == 422


class TestReplacedBy:
    def test_same_property_ok(self, client, setup, db):
        prop, meter, admin = setup
        new = make_meter(db, prop.id, name="neu")
        r = client.patch(_murl(prop) + str(meter.id), headers=auth_headers(admin), json={"replaced_by_id": str(new.id)})
        assert r.status_code == 200

    def test_foreign_property_rejected(self, client, setup, db):
        prop, meter, admin = setup
        other = make_meter(db, make_property(db, "other").id)
        r = client.patch(_murl(prop) + str(meter.id), headers=auth_headers(admin), json={"replaced_by_id": str(other.id)})
        assert r.status_code == 422
        db.refresh(meter)
        assert meter.replaced_by_id is None

    def test_unknown_and_self_rejected(self, client, setup):
        prop, meter, admin = setup
        for target in (uuid.uuid4(), meter.id):
            r = client.patch(_murl(prop) + str(meter.id), headers=auth_headers(admin), json={"replaced_by_id": str(target)})
            assert r.status_code == 422


class TestDeleteUserWithLinks:
    def test_delete_user_with_property_links(self, client, db):
        sa = make_user(db, role=UserRole.superadmin, username="sa", email="sa@example.com")
        victim = make_user(db, username="victim", email="v@example.com")
        prop = make_property(db)
        db.add(PropertyUser(property_id=prop.id, user_id=victim.id, role=PropertyUserRole.user))
        db.commit()
        vid = victim.id
        r = client.delete(f"/api/admin/users/{vid}", headers=auth_headers(sa))
        assert r.status_code == 204
        db.expire_all()
        assert db.query(User).filter(User.id == vid).count() == 0
        assert db.query(PropertyUser).filter(PropertyUser.user_id == vid).count() == 0


class TestHintRescanNul:
    @pytest.mark.parametrize("field", ["image_path", "current_value"])
    def test_nul_is_422(self, client, setup, field):
        prop, meter, admin = setup
        data = {"image_path": "uploads/x.jpg", "meter_id": str(meter.id), "current_value": "1"}
        data[field] = "a\x00b"
        r = client.post("/api/ocr/hint-rescan", headers=auth_headers(admin), data=data)
        assert r.status_code == 422
