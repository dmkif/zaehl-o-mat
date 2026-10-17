#!/usr/bin/env python
"""Browser edge for the e2e suite (feature 013): a stand-in for Traefik in production.

* HTTPS with a generated throw-away self-signed certificate (127.0.0.1 / localhost).
* `/api/*` -> rig backend, with `X-Forwarded-Proto: https`, `X-Forwarded-For`, `X-Forwarded-Host`
  and the original `Host` (Traefik keeps it). Redirects of the backend are passed on unchanged,
  exactly as Traefik does: a backend that builds `http://` Locations is visible to the browser.
* everything else -> the built SPA (`frontend/dist`) with index.html fallback for client routes.
* Response headers on EVERY route are the production set. They are read from
  chart/templates/middleware-security-headers.yaml (what Traefik sends) and cross-checked against the
  nginx.conf `add_header` lines (docker-compose edge); a drift between the two aborts start-up.

Binds 127.0.0.1 only. Usage: browser_proxy.py --port N --backend URL --dist DIR --cert-dir DIR [--repo DIR]
"""
import argparse
import contextlib
import datetime
import ipaddress
import re
import sys
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, Response, StreamingResponse
from starlette.routing import Route

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
              "transfer-encoding", "upgrade", "host", "content-length"}


# --- production security headers, read from the repository ------------------------------------------------------

def chart_headers(repo: Path) -> dict[str, str]:
    """Headers the Traefik `headers` middleware of the chart sends."""
    tpl = (repo / "chart/templates/middleware-security-headers.yaml").read_text()
    values = (repo / "chart/values.yaml").read_text()
    out: dict[str, str] = {}
    if re.search(r"^\s*frameDeny:\s*true", tpl, re.M):
        out["X-Frame-Options"] = "DENY"
    if re.search(r"^\s*contentTypeNosniff:\s*true", tpl, re.M):
        out["X-Content-Type-Options"] = "nosniff"
    m = re.search(r'^\s*referrerPolicy:\s*"([^"]+)"', tpl, re.M)
    if m:
        out["Referrer-Policy"] = m.group(1)
    if re.search(r"^\s*forceSTSHeader:\s*true", tpl, re.M):
        h = re.search(r"^\s*hsts:\s*\n\s*seconds:\s*(\d+)\s*\n\s*includeSubdomains:\s*(\w+)\s*\n\s*preload:\s*(\w+)",
                      values, re.M)
        if h:
            sts = f"max-age={h.group(1)}"
            if h.group(2) == "true":
                sts += "; includeSubDomains"
            if h.group(3) == "true":
                sts += "; preload"
            out["Strict-Transport-Security"] = sts
    block = re.search(r"customResponseHeaders:\n((?:[ \t]+.*\n?)+)", tpl)
    if not block:
        sys.exit("browser_proxy: customResponseHeaders not found in the chart middleware")
    cur = None
    for line in block.group(1).splitlines():
        if re.match(r"^\s{6}[A-Za-z-]+:", line):
            name, _, val = line.strip().partition(":")
            val = val.strip()
            if val in (">-", ">", "|", "|-"):
                cur, out[name] = name, ""
            else:
                cur, out[name] = None, val.strip('"')
        elif cur and line.strip():
            out[cur] = (out[cur] + " " + line.strip()).strip()
    return out


def nginx_headers(repo: Path) -> dict[str, str]:
    conf = (repo / "nginx.conf").read_text()
    return {m.group(1): m.group(2) for m in re.finditer(r'add_header\s+(\S+)\s+"?([^";]+)"?\s+always;', conf)}


def production_headers(repo: Path) -> dict[str, str]:
    chart, nginx = chart_headers(repo), nginx_headers(repo)
    if not chart.get("Content-Security-Policy"):
        sys.exit("browser_proxy: no Content-Security-Policy found in the chart middleware")
    bad = [n for n, v in nginx.items() if n in chart and chart[n] != v]
    if bad:
        sys.exit("browser_proxy: chart middleware and nginx.conf disagree on: " + ", ".join(bad)
                 + " (keep them in sync, F-041)")
    return chart


# --- certificate ----------------------------------------------------------------------------------------------------

def make_cert(cert_dir: Path) -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "zaehl-o-mat e2e (throw-away)")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                                                    x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_dir.mkdir(parents=True, exist_ok=True)
    kp, cp = cert_dir / "key.pem", cert_dir / "cert.pem"
    kp.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()))
    kp.chmod(0o600)
    cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cp, kp


# --- app ------------------------------------------------------------------------------------------------------------

def build_app(backend: str, dist: Path, headers: dict[str, str]) -> Starlette:
    client = httpx.AsyncClient(base_url=backend, timeout=httpx.Timeout(300.0, connect=10.0), follow_redirects=False)
    index = dist / "index.html"

    def secure(resp: Response) -> Response:
        for k, v in headers.items():
            resp.headers[k] = v
        return resp

    async def api(request: Request) -> Response:
        peer = request.client.host if request.client else "127.0.0.1"
        fwd = [(k, v) for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP
               and k.lower() not in ("x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "x-real-ip")]
        # Traefik appends the peer to an incoming X-Forwarded-For chain; the browser sends none.
        prior = request.headers.get("x-forwarded-for")
        fwd += [("X-Forwarded-For", f"{prior}, {peer}" if prior else peer), ("X-Forwarded-Proto", "https"),
                ("X-Forwarded-Host", request.headers.get("host", "")), ("X-Real-Ip", peer),
                ("Host", request.headers.get("host", ""))]
        url = httpx.URL(path=request.url.path, query=request.url.query.encode())
        has_body = "content-length" in request.headers or "transfer-encoding" in request.headers
        upstream = await client.send(
            client.build_request(request.method, url, headers=fwd, content=request.stream() if has_body else None),
            stream=True)
        out = [(k, v) for k, v in upstream.headers.multi_items()
               if k.lower() not in HOP_BY_HOP | {"date", "server"}
               and k.lower() not in {h.lower() for h in headers}]
        resp = StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code,
                                 background=None)
        resp.raw_headers = [(k.lower().encode(), v.encode()) for k, v in out]
        return secure(resp)

    async def static(request: Request) -> Response:
        rel = request.path_params.get("path", "")
        target = (dist / rel).resolve() if rel else index
        if rel and dist.resolve() in target.parents and target.is_file():
            return secure(FileResponse(target))
        return secure(FileResponse(index))  # SPA fallback, like nginx error_page 404 -> /index.html

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        yield
        await client.aclose()

    return Starlette(
        routes=[
            Route("/api", api, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]),
            Route("/api/{path:path}", api, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]),
            Route("/{path:path}", static, methods=["GET", "HEAD"]),
        ],
        lifespan=lifespan,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--backend", required=True)
    ap.add_argument("--dist", required=True)
    ap.add_argument("--cert-dir", required=True)
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]))
    a = ap.parse_args()
    if not a.backend.startswith("http://127.0.0.1:"):
        sys.exit("browser_proxy: refusing non-loopback backend")
    dist = Path(a.dist)
    if not (dist / "index.html").is_file():
        sys.exit(f"browser_proxy: {dist}/index.html missing (npm run build in frontend/)")
    headers = production_headers(Path(a.repo))
    cert, key = make_cert(Path(a.cert_dir))
    print("browser_proxy headers:", ", ".join(sorted(headers)), flush=True)
    uvicorn.run(build_app(a.backend, dist, headers), host="127.0.0.1", port=a.port, ssl_certfile=str(cert),
                ssl_keyfile=str(key), log_level="warning", proxy_headers=False)


if __name__ == "__main__":
    main()
