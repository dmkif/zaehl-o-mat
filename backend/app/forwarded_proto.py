"""Take the request scheme from X-Forwarded-Proto, but only from a trusted proxy.

uvicorn runs with ``--no-proxy-headers`` (so a client cannot forge its address
for the rate limiter, F-002).  That also leaves the ASGI scheme at ``http``
behind a TLS-terminating proxy, and redirects (for example the trailing-slash
redirect) then point to ``http://``.  This middleware restores only the scheme,
and only when the direct TCP peer is one of ``TRUSTED_PROXIES``.
"""
from starlette.types import ASGIApp, Receive, Scope, Send

from app.limiter import _in, _trusted

_SCHEMES = {"http": "http", "https": "https", "ws": "ws", "wss": "wss"}


class ForwardedProtoMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            if client and _in(client[0], _trusted):
                for name, value in scope.get("headers", []):
                    if name == b"x-forwarded-proto":
                        # Last entry = the hop closest to us (set by the trusted proxy).
                        last = value.decode("latin-1").split(",")[-1].strip().lower()
                        scheme = _SCHEMES.get(last)
                        if scheme and not (scope["type"] == "websocket" and scheme in ("http", "https")):
                            scope = dict(scope)
                            scope["scheme"] = scheme
                        break
        await self.app(scope, receive, send)
