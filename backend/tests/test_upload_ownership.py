"""F-004: uploaded images are bound to the property/user that uploaded them.

Covers attach (create reading), serve, hint-rescan, path shape, delete and
legacy readings whose image_path predates the upload registry.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.models import PropertyUser, PropertyUserRole, Reading, UploadFile
from tests.conftest import auth_headers, make_meter, make_property, make_reading, make_user
from tests.test_uploads import _MINIMAL_JPEG

_OCR_PATCH = "app.routers.ocr._run_ocr_on_file"
_OCR_RESULT = {"raw_texts": [], "detected_value": "12345", "detected_serial": None, "detection_method": "ocr"}


def _readings_url(prop_id, meter_id):
    return f"/api/properties/{prop_id}/meters/{meter_id}/readings/"


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.upload_path", str(tmp_path))
    return tmp_path


def _member(db, prop, name, role=PropertyUserRole.manager):
    user = make_user(db, username=name, email=f"{name}@example.com")
    db.add(PropertyUser(property_id=prop.id, user_id=user.id, role=role))
    db.commit()
    return user


def _register(db, uploads, user, prop=None, name=None):
    """Create a file on disk plus its registry row; return the stored path."""
    name = name or f"{uuid.uuid4()}.jpg"
    (uploads / name).write_bytes(_MINIMAL_JPEG)
    db.add(UploadFile(filename=name, property_id=prop.id if prop else None, uploaded_by_id=user.id))
    db.commit()
    return f"uploads/{name}"


def _two_tenants(db):
    pa, pb = make_property(db, "A"), make_property(db, "B")
    ma, mb = make_meter(db, pa.id), make_meter(db, pb.id)
    return pa, ma, _member(db, pa, "alice"), pb, mb, _member(db, pb, "bob")


class TestAttach:
    def test_foreign_file_cannot_be_attached(self, client, db, uploads):
        pa, ma, alice, pb, mb, bob = _two_tenants(db)
        path = _register(db, uploads, alice, pa)
        r = client.post(_readings_url(pb.id, mb.id), headers=auth_headers(bob),
                        json={"value": 1.0, "source": "auto", "image_path": path})
        assert r.status_code in (400, 403)
        assert db.query(Reading).filter(Reading.meter_id == mb.id).count() == 0

    def test_foreign_serial_file_cannot_be_attached(self, client, db, uploads):
        pa, ma, alice, pb, mb, bob = _two_tenants(db)
        path = _register(db, uploads, alice, pa)
        r = client.post(_readings_url(pb.id, mb.id), headers=auth_headers(bob),
                        json={"value": 1.0, "source": "auto", "serial_image_path": path})
        assert r.status_code in (400, 403)

    def test_own_file_can_be_attached(self, client, db, uploads):
        pa, ma, alice, *_ = _two_tenants(db)
        path = _register(db, uploads, alice, pa)
        r = client.post(_readings_url(pa.id, ma.id), headers=auth_headers(alice),
                        json={"value": 1.0, "source": "auto", "image_path": path})
        assert r.status_code == 201
        assert r.json()["image_path"] == path

    def test_unattached_file_uploaded_by_caller_without_property(self, client, db, uploads):
        pa, ma, alice, *_ = _two_tenants(db)
        path = _register(db, uploads, alice, None)
        r = client.post(_readings_url(pa.id, ma.id), headers=auth_headers(alice),
                        json={"value": 1.0, "source": "auto", "image_path": path})
        assert r.status_code == 201

    def test_unattached_file_of_other_user_without_property_rejected(self, client, db, uploads):
        pa, ma, alice, pb, mb, bob = _two_tenants(db)
        path = _register(db, uploads, bob, None)
        r = client.post(_readings_url(pa.id, ma.id), headers=auth_headers(alice),
                        json={"value": 1.0, "source": "auto", "image_path": path})
        assert r.status_code in (400, 403)

    def test_unregistered_file_rejected(self, client, db, uploads):
        pa, ma, alice, *_ = _two_tenants(db)
        name = f"{uuid.uuid4()}.jpg"
        (uploads / name).write_bytes(_MINIMAL_JPEG)
        r = client.post(_readings_url(pa.id, ma.id), headers=auth_headers(alice),
                        json={"value": 1.0, "source": "auto", "image_path": f"uploads/{name}"})
        assert r.status_code in (400, 403)

    @pytest.mark.parametrize("bad", [
        "uploads/../../etc/passwd",
        "/etc/passwd",
        "../uploads/11111111-1111-1111-1111-111111111111.jpg",
        "uploads/11111111-1111-1111-1111-111111111111.exe",
        "uploads/11111111-1111-1111-1111-111111111111.jpg/../x.jpg",
        "uploads/sub/11111111-1111-1111-1111-111111111111.jpg",
        "http://evil/x.jpg",
        "uploads/test.jpg",
    ])
    def test_bad_path_shape_rejected(self, client, db, uploads, bad):
        pa, ma, alice, *_ = _two_tenants(db)
        for field in ("image_path", "serial_image_path"):
            r = client.post(_readings_url(pa.id, ma.id), headers=auth_headers(alice),
                            json={"value": 1.0, "source": "auto", field: bad})
            assert r.status_code in (400, 422), (field, bad)


class TestServe:
    def test_foreign_unattached_file_not_served(self, client, db, uploads):
        pa, ma, alice, pb, mb, bob = _two_tenants(db)
        path = _register(db, uploads, alice, pa)
        r = client.get(f"/api/{path}", headers=auth_headers(bob))
        assert r.status_code in (403, 404)

    def test_foreign_file_via_foreign_reading_not_served(self, client, db, uploads):
        """Bob attaching nothing: a reading in A referencing the file gates by A's membership."""
        pa, ma, alice, pb, mb, bob = _two_tenants(db)
        path = _register(db, uploads, alice, pa)
        rd = make_reading(db, ma.id, 5.0)
        rd.image_path = path
        db.commit()
        assert client.get(f"/api/{path}", headers=auth_headers(bob)).status_code == 403
        assert client.get(f"/api/{path}", headers=auth_headers(alice)).status_code == 200

    def test_uploader_can_read_unattached_file(self, client, db, uploads):
        pa, ma, alice, *_ = _two_tenants(db)
        path = _register(db, uploads, alice, None)
        assert client.get(f"/api/{path}", headers=auth_headers(alice)).status_code == 200

    def test_property_member_can_read_property_file(self, client, db, uploads):
        pa, ma, alice, *_ = _two_tenants(db)
        carol = _member(db, pa, "carol", PropertyUserRole.user)
        path = _register(db, uploads, alice, pa)
        assert client.get(f"/api/{path}", headers=auth_headers(carol)).status_code == 200

    def test_unknown_file_not_served_to_normal_user(self, client, db, uploads):
        pa, ma, alice, *_ = _two_tenants(db)
        (uploads / "orphan.jpg").write_bytes(_MINIMAL_JPEG)
        assert client.get("/api/uploads/orphan.jpg", headers=auth_headers(alice)).status_code in (403, 404)


class TestLegacy:
    def test_legacy_reading_image_still_served_to_member_only(self, client, db, uploads):
        pa, ma, alice, pb, mb, bob = _two_tenants(db)
        (uploads / "old_scan.jpg").write_bytes(_MINIMAL_JPEG)
        rd = make_reading(db, ma.id, 5.0)
        rd.image_path = "uploads/old_scan.jpg"
        db.commit()
        assert client.get("/api/uploads/old_scan.jpg", headers=auth_headers(alice)).status_code == 200
        assert client.get("/api/uploads/old_scan.jpg", headers=auth_headers(bob)).status_code == 403

    def test_legacy_reading_listing_and_update_unaffected(self, client, db, uploads):
        pa, ma, alice, *_ = _two_tenants(db)
        rd = make_reading(db, ma.id, 5.0)
        rd.image_path = "uploads/old_scan.jpg"
        db.commit()
        r = client.get(_readings_url(pa.id, ma.id), headers=auth_headers(alice))
        assert r.status_code == 200
        assert r.json()[0]["image_path"] == "uploads/old_scan.jpg"
        r = client.patch(f"{_readings_url(pa.id, ma.id)}{rd.id}", headers=auth_headers(alice), json={"note": "x"})
        assert r.status_code == 200


class TestHintRescan:
    def _post(self, client, user, path, meter):
        return client.post(
            "/api/ocr/hint-rescan",
            data={"image_path": path, "meter_id": str(meter.id), "current_value": "garbage"},
            headers=auth_headers(user),
        )

    def test_foreign_file_not_sent_to_gateway(self, client, db, uploads, monkeypatch):
        pa, ma, alice, pb, mb, bob = _two_tenants(db)
        path = _register(db, uploads, alice, pa)
        monkeypatch.setattr("app.config.settings.ollama_url", "http://ollama:11434")
        with patch("app.routers.ocr._llm_fallback") as llm:
            r = self._post(client, bob, path, mb)
        assert r.status_code in (403, 404)
        llm.assert_not_called()

    def test_own_file_is_rescanned(self, client, db, uploads, monkeypatch):
        pa, ma, alice, *_ = _two_tenants(db)
        path = _register(db, uploads, alice, pa)
        monkeypatch.setattr("app.config.settings.ollama_url", "http://ollama:11434")
        with patch("app.routers.ocr._llm_fallback", return_value=("123.4", None)) as llm:
            r = self._post(client, alice, path, ma)
        assert r.status_code == 200
        llm.assert_called_once()


class TestScanRegistersUpload:
    def test_scan_records_owner_and_property(self, client, db, uploads):
        pa, ma, alice, *_ = _two_tenants(db)
        with patch(_OCR_PATCH, new_callable=AsyncMock, return_value=dict(_OCR_RESULT)):
            r = client.post("/api/ocr/scan", data={"engine": "ocr", "meter_id": str(ma.id)},
                            files={"file": ("m.jpg", _MINIMAL_JPEG, "image/jpeg")},
                            headers=auth_headers(alice))
        assert r.status_code == 200
        name = r.json()["image_path"].split("/", 1)[1]
        row = db.query(UploadFile).filter_by(filename=name).one()
        assert row.uploaded_by_id == alice.id and row.property_id == pa.id

    def test_scan_without_meter_has_null_property(self, client, db, uploads):
        alice = make_user(db, username="alice", email="a@example.com")
        with patch(_OCR_PATCH, new_callable=AsyncMock, return_value=dict(_OCR_RESULT)):
            r = client.post("/api/ocr/scan", data={"engine": "ocr"},
                            files={"file": ("m.jpg", _MINIMAL_JPEG, "image/jpeg")},
                            headers=auth_headers(alice))
        name = r.json()["image_path"].split("/", 1)[1]
        assert db.query(UploadFile).filter_by(filename=name).one().property_id is None

    def test_scan_then_attach_roundtrip(self, client, db, uploads):
        pa, ma, alice, *_ = _two_tenants(db)
        with patch(_OCR_PATCH, new_callable=AsyncMock, return_value=dict(_OCR_RESULT)):
            r = client.post("/api/ocr/scan", data={"engine": "ocr", "meter_id": str(ma.id)},
                            files={"file": ("m.jpg", _MINIMAL_JPEG, "image/jpeg"),
                                   "serial_file": ("s.jpg", _MINIMAL_JPEG, "image/jpeg")},
                            headers=auth_headers(alice))
        body = r.json()
        assert body["serial_image_path"]
        r = client.post(_readings_url(pa.id, ma.id), headers=auth_headers(alice),
                        json={"value": 1.0, "source": "auto", "image_path": body["image_path"],
                              "serial_image_path": body["serial_image_path"]})
        assert r.status_code == 201

    def test_bulk_scan_records_upload(self, client, db, uploads):
        pa, ma, alice, *_ = _two_tenants(db)
        with patch(_OCR_PATCH, new_callable=AsyncMock, return_value=dict(_OCR_RESULT)):
            r = client.post("/api/ocr/bulk-scan", data={"engine": "ocr", "property_id": str(pa.id)},
                            files=[("files", ("m.jpg", _MINIMAL_JPEG, "image/jpeg"))],
                            headers=auth_headers(alice))
        assert r.status_code == 200
        assert db.query(UploadFile).filter_by(uploaded_by_id=alice.id, property_id=pa.id).count() == 1


class TestDelete:
    def _attach(self, db, meter, path):
        rd = make_reading(db, meter.id, 5.0)
        rd.image_path = path
        db.commit()
        return rd

    def test_delete_removes_unreferenced_file(self, client, db, uploads):
        pa, ma, alice, *_ = _two_tenants(db)
        admin = _member(db, pa, "adm", PropertyUserRole.admin)
        path = _register(db, uploads, alice, pa)
        rd = self._attach(db, ma, path)
        r = client.delete(f"{_readings_url(pa.id, ma.id)}{rd.id}", headers=auth_headers(admin))
        assert r.status_code == 204
        assert not (uploads / path.split("/", 1)[1]).exists()

    def test_delete_keeps_file_still_referenced_elsewhere(self, client, db, uploads):
        pa, ma, alice, *_ = _two_tenants(db)
        admin = _member(db, pa, "adm", PropertyUserRole.admin)
        path = _register(db, uploads, alice, pa)
        rd1 = self._attach(db, ma, path)
        self._attach(db, ma, path)
        r = client.delete(f"{_readings_url(pa.id, ma.id)}{rd1.id}", headers=auth_headers(admin))
        assert r.status_code == 204
        assert (uploads / path.split("/", 1)[1]).exists()

    def test_legacy_path_with_traversal_never_unlinks_outside_dir(self, client, db, uploads, tmp_path_factory):
        pa, ma, alice, *_ = _two_tenants(db)
        admin = _member(db, pa, "adm", PropertyUserRole.admin)
        victim = tmp_path_factory.mktemp("other") / "keep.jpg"
        victim.write_bytes(b"x")
        rd = self._attach(db, ma, f"uploads/../../{victim.name}")
        client.delete(f"{_readings_url(pa.id, ma.id)}{rd.id}", headers=auth_headers(admin))
        assert victim.exists()
