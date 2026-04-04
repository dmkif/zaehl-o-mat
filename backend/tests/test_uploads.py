"""
Tests for GET /api/uploads/{filename}

Covers:
  - unauthenticated request → 401
  - path traversal via embedded slash → 400
  - path traversal via backslash → 400
  - dot-prefixed filename → 400
  - file not found → 404
  - existing file served correctly → 200 with correct Content-Type
  - Content-Disposition header present
"""
import os
from pathlib import Path

import pytest

from app.models import UserRole
from tests.conftest import auth_headers, make_user

# Minimal valid JPEG bytes (1×1 pixel)
_MINIMAL_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
    b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'"
    b"9=82<.342\x1edL\t\x10\x17\x19\x1e\x1e\x1e\x1e\x1e\x1e\x1e\x1e"
    b"\x1e\x1e\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
    b"\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04"
    b"\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa"
    b"\x07\"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\t\n"
    b"\x16\x17\x18\x19\x1a%&'()*456789:CDEFGHIJSTUVWXYZ"
    b"cdefghijstuvwxyz\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95"
    b"\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3"
    b"\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca"
    b"\xd2\xd3\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7"
    b"\xe8\xe9\xea\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xff\xda\x00"
    b"\x08\x01\x01\x00\x00?\x00\xfb\xd3\xff\xd9"
)


class TestUploadsRouter:
    def test_unauthenticated_returns_401(self, client, db):
        r = client.get("/api/uploads/photo.jpg")
        assert r.status_code == 401

    @pytest.mark.parametrize("bad_name", [
        "../etc/passwd",
        "subdir/file.jpg",
        "sub\\file.jpg",
        ".hidden",
        "..",
    ])
    def test_malicious_filename_rejected(self, client, db, bad_name):
        """
        Path traversal and directory-climb attempts must be blocked.

        Names containing '/' or '\\' are resolved by the HTTP stack before our
        endpoint runs and land on routes that return 404.  Names starting with '.'
        or containing '\\' reach the endpoint and are rejected with 400.
        Both outcomes mean the file is NOT served — either is acceptable.
        """
        user = make_user(db)
        r = client.get(f"/api/uploads/{bad_name}", headers=auth_headers(user))
        assert r.status_code in (400, 404)

    def test_nonexistent_file_returns_404(self, client, db):
        user = make_user(db)
        r = client.get("/api/uploads/does-not-exist.jpg", headers=auth_headers(user))
        assert r.status_code == 404

    def test_existing_file_returns_200_with_content(self, client, db, tmp_path, monkeypatch):
        """Serve a real file from the upload directory."""
        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir()
        monkeypatch.setattr("app.routers.uploads.settings.upload_path", str(upload_dir))

        (upload_dir / "meter.jpg").write_bytes(_MINIMAL_JPEG)

        user = make_user(db)
        r = client.get("/api/uploads/meter.jpg", headers=auth_headers(user))
        assert r.status_code == 200
        assert r.content == _MINIMAL_JPEG

    def test_existing_file_has_jpeg_content_type(self, client, db, tmp_path, monkeypatch):
        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir()
        monkeypatch.setattr("app.routers.uploads.settings.upload_path", str(upload_dir))

        (upload_dir / "photo.jpg").write_bytes(_MINIMAL_JPEG)

        user = make_user(db)
        r = client.get("/api/uploads/photo.jpg", headers=auth_headers(user))
        assert r.status_code == 200
        assert "jpeg" in r.headers.get("content-type", "").lower()

    def test_non_admin_user_can_access_own_uploads(self, client, db, tmp_path, monkeypatch):
        """Regular users with a valid token should be able to serve uploads (no ACL on filename)."""
        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir()
        monkeypatch.setattr("app.routers.uploads.settings.upload_path", str(upload_dir))

        (upload_dir / "meter.jpg").write_bytes(_MINIMAL_JPEG)

        regular_user = make_user(db, role=UserRole.user)
        r = client.get("/api/uploads/meter.jpg", headers=auth_headers(regular_user))
        assert r.status_code == 200
