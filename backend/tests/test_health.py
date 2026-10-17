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
