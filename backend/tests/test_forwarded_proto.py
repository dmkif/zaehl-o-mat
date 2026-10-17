"""Redirects behind a TLS-terminating proxy must keep https (regression of --no-proxy-headers).

uvicorn runs with --no-proxy-headers (F-002), so the ASGI scheme is always
``http``.  A trailing-slash redirect then pointed the browser to http://,
which the page CSP (connect-src 'self') blocked.  The scheme is taken from
X-Forwarded-Proto only when the TCP peer is a trusted proxy.
"""
from fastapi.testclient import TestClient

from app.main import app

PATH = "/api/properties"  # route is defined with a trailing slash -> 307 to /api/properties/


def _location(peer: str, headers: dict) -> str:
    client = TestClient(app, base_url="http://zaehl.example", client=(peer, 50000), follow_redirects=False)
    r = client.get(PATH, headers=headers)
    assert r.status_code in (307, 308), r.status_code
    return r.headers["location"]


def test_https_kept_when_trusted_proxy_says_https():
    assert _location("10.42.0.1", {"X-Forwarded-Proto": "https"}).startswith("https://")


def test_untrusted_peer_cannot_set_scheme():
    assert _location("203.0.113.9", {"X-Forwarded-Proto": "https"}).startswith("http://")


def test_no_header_keeps_request_scheme():
    assert _location("10.42.0.1", {}).startswith("http://")


def test_garbage_proto_ignored():
    assert _location("10.42.0.1", {"X-Forwarded-Proto": "javascript"}).startswith("http://")


def test_rightmost_proto_wins_in_a_chain():
    assert _location("10.42.0.1", {"X-Forwarded-Proto": "http, https"}).startswith("https://")
