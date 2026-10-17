"""
Tests for GET /api/health — OCR-optional-container feature.

Covers the `ocr` field specifically: OCR is an optional subsystem
(Constitution Principle II) and its absence must never degrade overall
`status`.
"""


class TestHealthOcrField:
    def test_ocr_false_when_url_unset(self, client, db, monkeypatch):
        from app import main
        from app.models import UserRole
        from tests.conftest import auth_headers, make_user
        main._details_cache.clear()
        monkeypatch.setattr("app.main.settings.ocr_url", "")
        admin = make_user(db, role=UserRole.admin, username="adm", email="adm@example.com")
        r = client.get("/api/health/details", headers=auth_headers(admin))
        assert r.status_code == 200
        body = r.json()
        assert body["ocr"] is False
        assert body["status"] == "ok"


class TestHealthDetailsAccess:
    """Details are for every signed-in user (status footer); version/model only for admins."""

    def test_details_require_login(self, client):
        assert client.get("/api/health/details").status_code == 401

    def test_normal_user_gets_status_flags_without_version(self, client, db, monkeypatch):
        from app import main
        from app.models import UserRole
        from tests.conftest import auth_headers, make_user
        main._details_cache.clear()
        monkeypatch.setattr("app.main.settings.ocr_url", "")
        user = make_user(db, role=UserRole.user, username="usr", email="usr@example.com")
        r = client.get("/api/health/details", headers=auth_headers(user))
        assert r.status_code == 200
        body = r.json()
        assert {"status", "db", "scheduler", "ocr", "llm"} <= set(body)
        assert "version" not in body and "llm_model" not in body

    def test_admin_gets_version_and_model(self, client, db, monkeypatch):
        from app import main
        from app.models import UserRole
        from tests.conftest import auth_headers, make_user
        main._details_cache.clear()
        monkeypatch.setattr("app.main.settings.ocr_url", "")
        admin = make_user(db, role=UserRole.admin, username="adm2", email="adm2@example.com")
        body = client.get("/api/health/details", headers=auth_headers(admin)).json()
        assert "version" in body and "llm_model" in body
