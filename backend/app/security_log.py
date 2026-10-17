"""Structured security-event logging (spec 006, F-010).

One line per event with fixed fields.  Never pass tokens, codes, passwords,
e-mail addresses or group names here.
"""
import logging
from typing import Optional

_logger = logging.getLogger("app.security")


def log_event(
    event: str,
    *,
    outcome: str = "ok",
    actor: Optional[object] = None,
    target: Optional[object] = None,
    ip: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    """Log ``security event=<event> outcome=<ok|denied|failed> actor=<id> target=<id> ip=<ip> reason=<code>``."""
    fields = {"event": event, "outcome": outcome, "actor": actor, "target": target, "ip": ip, "reason": reason}
    text = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
    level = logging.INFO if outcome == "ok" else logging.WARNING
    _logger.log(level, "security %s", text)
