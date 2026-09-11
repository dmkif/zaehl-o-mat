"""
Tests for GET /api/health — OCR-optional-container feature.

Covers the `ocr` field specifically: OCR is an optional subsystem
(Constitution Principle II) and its absence must never degrade overall
`status`.
"""


class TestHealthOcrField:
    def test_ocr_false_when_url_unset(self, client, db, monkeypatch):
        monkeypatch.setattr("app.main.settings.ocr_url", "")
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["ocr"] is False
        assert body["status"] == "ok"
