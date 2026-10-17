"""Fake KI-Gateway (OpenAI-compatible). Contract: security/rig/CONTRACT.md. Binds 127.0.0.1 only."""
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
OK_READING, OK_SERIAL = "12345.6", "1234567"


def _content(serial_only: bool) -> str:
    """Model answer text for the current mode (the string inside choices[0].message.content)."""
    mode, p = STATE["mode"], STATE["params"]
    reading, serial = p.get("reading", OK_READING), p.get("serial", OK_SERIAL)
    if mode == "reading_equals_serial":
        reading = serial = p.get("serial", "24519788")
    if mode == "hostile_huge":
        n = int(p.get("size", 5_000_000))
        return json.dumps({"reading": "9" * n, "serial": "8" * n})
    if mode == "hostile_nested":
        d = int(p.get("depth", 5000))
        return '{"reading":' + "[" * d + "1" + "]" * d + ',"serial":' + '{"a":' * d + "1" + "}" * d + "}"
    if mode == "hostile_types":
        return p.get("raw") or json.dumps({"reading": {"x": [1, 2]}, "serial": 12345678})
    return json.dumps({"serial": serial} if serial_only else {"reading": reading, "serial": serial})


async def _gate(request: Request):
    """Returns an error Response for failure modes, else None."""
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


@app.get("/v1/models")
async def models(request: Request):
    err = await _gate(request)
    return err or {"object": "list", "data": [{"id": "rig-model", "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    err = await _gate(request)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    tail = json.dumps(body.get("messages", "")).replace('\\"', '"').split("Answer ONLY")[-1]
    serial_only = "Answer ONLY" in json.dumps(body.get("messages", "")) and '"reading"' not in tail
    return {
        "id": "chatcmpl-rig", "object": "chat.completion", "created": int(time.time()),
        "model": body.get("model") or "rig-model",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": _content(serial_only)}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


if __name__ == "__main__":
    main(app, 18082)
