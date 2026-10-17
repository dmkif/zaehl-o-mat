"""Rate-limiter key must not be controllable via client-supplied X-Forwarded-For (B-02/DEP-02)."""
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.limiter import client_key, parse_trusted_proxies
from app.main import app

TRUSTED = parse_trusted_proxies("127.0.0.0/8,10.0.0.0/8")


def _req(peer: str, xff: str | None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    return Request({"type": "http", "headers": headers, "client": (peer, 1234)})


def test_untrusted_peer_ignores_spoofed_header():
    assert client_key(_req("203.0.113.9", "1.2.3.4"), TRUSTED) == "203.0.113.9"


def test_trusted_proxy_uses_rightmost_untrusted_entry():
    # spoofed leftmost entry, real client appended by the proxy
    assert client_key(_req("10.1.2.3", "6.6.6.6, 198.51.100.7"), TRUSTED) == "198.51.100.7"
    # chain of trusted hops on the right is skipped
    assert client_key(_req("10.1.2.3", "6.6.6.6, 198.51.100.7, 10.9.9.9"), TRUSTED) == "198.51.100.7"


def test_trusted_proxy_without_or_with_garbage_header_falls_back_to_peer():
    assert client_key(_req("10.1.2.3", None), TRUSTED) == "10.1.2.3"
    assert client_key(_req("10.1.2.3", "not-an-ip"), TRUSTED) == "10.1.2.3"


def test_default_is_not_wildcard():
    assert parse_trusted_proxies("*") == []  # '*' is rejected, never "trust everything"


def test_superadmin_login_limit_not_bypassable_with_rotating_xff():
    c = TestClient(app, client=("203.0.113.50", 5000))
    codes = [
        c.post(
            "/api/auth/superadmin-login",
            json={"username": "x", "password": "y"},
            headers={"X-Forwarded-For": f"9.9.9.{i}"},
        ).status_code
        for i in range(6)
    ]
    assert codes[-1] == 429, codes
    assert 429 not in codes[:5], codes
