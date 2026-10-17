"""Request-body limit for the bulk-scan route (F-012).

FastAPI parses the multipart form before the endpoint runs, so a limit inside
the endpoint comes too late.  This pure ASGI middleware refuses a declared
``Content-Length`` above the limit and aborts a chunked body that grows past it.
Only ``POST /api/ocr/bulk-scan`` is affected.
"""
import json

from fastapi import status

from app.config import settings

BULK_SCAN_PATH = "/api/ocr/bulk-scan"
# Multipart framing (boundaries, headers) on top of the file budget.
MULTIPART_SLACK = 1024 * 1024
_DETAIL = "Request body too large"


class BodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != BULK_SCAN_PATH:
            await self.app(scope, receive, send)
            return

        limit = settings.max_bulk_total_bytes + MULTIPART_SLACK
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None:
            try:
                too_big = int(declared) > limit
            except ValueError:
                too_big = True
            if too_big:
                await self._reject(send)
                return

        received = 0
        exceeded = False
        sent_413 = False

        async def limited_receive():
            # Past the limit the stream ends as a client disconnect. The framework turns whatever the
            # parser raises into a 400, so the response is replaced by a 413 in ``guarded_send``.
            nonlocal received, exceeded
            if exceeded:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            nonlocal sent_413
            if not exceeded:
                await send(message)
            elif not sent_413:
                sent_413 = True
                await self._reject(send)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not exceeded:
                raise
        if exceeded and not sent_413:  # the app finished without answering
            await self._reject(send)

    @staticmethod
    async def _reject(send):
        body = json.dumps({"detail": _DETAIL}).encode()
        await send({
            "type": "http.response.start",
            "status": status.HTTP_413_CONTENT_TOO_LARGE,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})
