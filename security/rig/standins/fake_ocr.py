"""Fake OCR service (POST /scan, POST /serial). Contract: security/rig/CONTRACT.md. Binds 127.0.0.1 only."""
MODES = {"ok", "hostile_huge", "hostile_nested", "hostile_types", "reading_equals_serial",
         "slow", "down", "unauthorized", "garbage"}
import argparse
import asyncio
import json
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

_REDACT = {"authorization", "cookie", "x-api-key", "proxy-authorization"}
STATE = {"mode": "ok", "params": {}}
REQUESTS: list[dict] = []


def install_control(app: FastAPI) -> None:
    @app.middleware("http")
    async def _record(request: Request, call_next):
        if not request.url.path.startswith("/_control"):
            body = await request.body()
            headers = {k: ("<redacted>" if k.lower() in _REDACT else v) for k, v in request.headers.items()}
            REQUESTS.append({
                "ts": time.time(), "method": request.method, "path": request.url.path,
                "query": request.url.query, "headers": headers, "body_size": len(body),
                "mode": STATE["mode"],
            })
            del REQUESTS[:-5000]
        return await call_next(request)

    @app.post("/_control/mode")
    async def set_mode(request: Request):
        data = await request.json()
        mode = data.pop("mode", None)
        if not isinstance(mode, str) or mode not in MODES:
            return JSONResponse({"error": "unknown mode", "modes": sorted(MODES)}, status_code=400)
        STATE["mode"], STATE["params"] = mode, data
        return {"mode": mode, "params": data}

    @app.get("/_control/mode")
    async def get_mode():
        return STATE

    @app.post("/_control/reset")
    async def reset():
        STATE["mode"], STATE["params"] = "ok", {}
        REQUESTS.clear()
        return STATE

    @app.get("/_control/requests")
    async def get_requests():
        return REQUESTS


def main(app: FastAPI, default_port: int) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=default_port)
    ap.add_argument("--api-key", default=None, help="if set, require this Bearer key (401 otherwise)")
    ap.add_argument("--client-secret", default=None, help="oidc only: required client_secret at /token")
    args = ap.parse_args()
    ARGS.update(vars(args))
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


ARGS: dict = {}

app = FastAPI()
install_control(app)


async def _gate(request: Request):
    mode = STATE["mode"]
    if ARGS.get("api_key") and request.headers.get("authorization") != f"Bearer {ARGS['api_key']}":
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if mode == "slow":
        await asyncio.sleep(float(STATE["params"].get("delay_s", 5)))
    if mode == "down":
        return JSONResponse({"error": "service unavailable"}, status_code=503)
    if mode == "unauthorized":
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if mode == "garbage":
        return PlainTextResponse("<html>not json \x00\xff garbage", status_code=200)
    return None


def _scan_body() -> dict:
    mode, p = STATE["mode"], STATE["params"]
    value, serial = p.get("reading", "12345.6"), p.get("serial", "1234567")
    if mode == "reading_equals_serial":
        value = serial = p.get("serial", "24519788")
    if mode == "hostile_huge":
        n = int(p.get("size", 5_000_000))
        return {"raw_texts": ["A" * n] * 3, "detected_value": "9" * n, "detected_serial": "8" * n,
                "detection_method": "ocr"}
    if mode == "hostile_nested":
        d = int(p.get("depth", 5000))
        raw = ('{"raw_texts":' + "[" * d + '"x"' + "]" * d + ',"detected_value":' + "[" * d + "1" + "]" * d
               + ',"detected_serial":null,"detection_method":"ocr"}')
        return {"__raw__": raw}
    if mode == "hostile_types":
        return {"raw_texts": "not-a-list", "detected_value": {"x": [1]}, "detected_serial": 12345678,
                "detection_method": ["ocr"]}
    return {"raw_texts": [value, serial], "detected_value": value, "detected_serial": serial,
            "detection_method": "ocr"}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/scan")
async def scan(request: Request):
    err = await _gate(request)
    if err:
        return err
    body = _scan_body()
    if "__raw__" in body:
        return Response(body["__raw__"], media_type="application/json")
    return body


@app.post("/serial")
async def serial(request: Request):
    err = await _gate(request)
    if err:
        return err
    body = _scan_body()
    if "__raw__" in body:
        return Response('{"detected_serial":' + "[" * int(STATE["params"].get("depth", 5000)) + "1"
                        + "]" * int(STATE["params"].get("depth", 5000)) + "}", media_type="application/json")
    return {"detected_serial": body["detected_serial"]}


if __name__ == "__main__":
    main(app, 18083)
