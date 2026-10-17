"""Rate limiter with a spoof-resistant client key.

uvicorn runs with ``--no-proxy-headers`` (see Dockerfile), so ``request.client``
is always the direct TCP peer. The X-Forwarded-For header is only consulted when
that peer is one of ``settings.trusted_proxies``; then the chain is walked from
the right and the first address that is not itself a trusted proxy is the
client. Entries to its left are client-controlled and are never used.
"""
import ipaddress
import logging

from slowapi import Limiter
from starlette.requests import Request

from app.config import settings

logger = logging.getLogger(__name__)

_Net = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_trusted_proxies(value: str) -> list[_Net]:
    nets: list[_Net] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if item == "*":
            logger.warning("TRUSTED_PROXIES '*' is rejected; list explicit CIDR ranges")
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            logger.warning("Ignoring invalid TRUSTED_PROXIES entry %r", item)
    return nets


def _in(addr: str, nets: list[_Net]) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in n for n in nets)


def client_key(request: Request, nets: list[_Net]) -> str:
    peer = request.client.host if request.client else "unknown"
    if not _in(peer, nets):
        return peer
    xff = request.headers.get("x-forwarded-for", "")
    for entry in reversed([e.strip() for e in xff.split(",") if e.strip()]):
        try:
            ipaddress.ip_address(entry)
        except ValueError:
            return peer  # garbage in the chain: do not trust anything left of it
        if not _in(entry, nets):
            return entry
    return peer


_trusted = parse_trusted_proxies(settings.trusted_proxies)


def _key_func(request: Request) -> str:
    return client_key(request, _trusted)


limiter = Limiter(key_func=_key_func)
