"""Weak-secret validation in Settings (spec 006, F-009)."""
import pytest
from pydantic import ValidationError

from app.config import Settings

STRONG_JWT = "kJ8s2Lq0Zp9xVw3Yt6uNb1MdR4eHgF7aCiOoXzQ5vB2n"
STRONG_PW = "Zr7!kQ2mVx9pLw"


def make(**kw):
    base = dict(database_url="sqlite://", jwt_secret_key=STRONG_JWT)
    return Settings(_env_file=None, **{**base, **kw})


def err(**kw) -> str:
    with pytest.raises(ValidationError) as exc:
        make(**kw)
    return str(exc.value)


def test_strong_config_starts():
    s = make(superadmin_user="root", superadmin_password=STRONG_PW)
    assert s.superadmin_user == "root"


def test_no_superadmin_is_allowed():
    make()


def test_production_sized_secrets_pass():
    make(jwt_secret_key="aB3dE5gH7jK9mN1pQ3sT5vX7zC9bD1fG3hJ5kL7mN9pR", superadmin_user="x", superadmin_password="Qw3rT5yU7iO9pA1sD3fG5hJ7kL9z")  # gitleaks:allow (Testwert)


@pytest.mark.parametrize("secret", [
    "short", "x" * 31, "x" * 40, "", " " * 40,
    "changeme" * 5, "secret" * 8, "password" * 5,
    "local-dev-secret-change-in-prod",
    "local-dev-secret-change-in-prod-padded-to-be-long",
    "change-me-in-production",
])
def test_weak_jwt_rejected_without_leaking(secret):
    msg = err(jwt_secret_key=secret)
    assert "JWT_SECRET_KEY" in msg
    assert not secret.strip() or secret not in msg


@pytest.mark.parametrize("name", ["changeme", "secret", "password", "local-dev-secret-change-in-prod"])
def test_exact_denylist_jwt(name):
    assert "JWT_SECRET_KEY" in err(jwt_secret_key=name)


def test_deny_list_is_case_insensitive():
    assert "JWT_SECRET_KEY" in err(jwt_secret_key="LOCAL-DEV-SECRET-CHANGE-IN-PROD")


@pytest.mark.parametrize("pw", ["short", "admin123", "Admin123", "password", "changeme", "a" * 11])
def test_weak_superadmin_password_rejected_without_leaking(pw):
    msg = err(superadmin_user="admin", superadmin_password=pw)
    assert "SUPERADMIN_PASSWORD" in msg
    assert pw not in msg


@pytest.mark.parametrize("pw", ["administrator", "passwordpassword", "changemechangeme"])
def test_denylisted_long_password_rejected(pw):
    # entries of the deny-list are rejected even when long enough
    from app.config import WEAK_PASSWORDS
    if pw.lower() in WEAK_PASSWORDS:
        assert "SUPERADMIN_PASSWORD" in err(superadmin_user="a", superadmin_password=pw)


def test_user_without_password_rejected():
    assert "SUPERADMIN" in err(superadmin_user="admin")


def test_password_without_user_rejected():
    msg = err(superadmin_password=STRONG_PW)
    assert "SUPERADMIN" in msg and STRONG_PW not in msg


def test_image_limits_defaults_and_validation():
    s = make()
    assert s.max_image_pixels == 40_000_000 and s.max_image_side == 12_000
    for kw in ({"max_image_pixels": 0}, {"max_image_side": -1}):
        with pytest.raises(ValidationError):
            make(**kw)
