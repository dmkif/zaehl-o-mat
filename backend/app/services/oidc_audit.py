"""OIDC group diagnostics (spec 003).

Always on: the rejected sign-in security event (reason + sign-in name, never
group names).  Only with ``OIDC_GROUP_DIAGNOSTICS=true``: delivered/expected
group names of rejected sign-ins, a distinct line for a missing group claim and
role + group count for accepted sign-ins.  All values are JSON-encoded and
bounded so they cannot forge log lines.
"""
import hashlib
import json
import logging

from app.config import settings
from app.security_log import log_event

_logger = logging.getLogger("app.oidc_audit")

MAX_VALUE_LEN = 64
MAX_GROUPS = 20


def _enc(value) -> str:
    return json.dumps(str(value)[:MAX_VALUE_LEN], ensure_ascii=True)


def user_label(oidc_data: dict) -> str:
    """JSON-quoted sign-in name; never the e-mail or the raw subject."""
    name = oidc_data.get("preferred_username")
    if isinstance(name, str) and name:
        return _enc(name)
    sub = oidc_data.get("sub")
    if isinstance(sub, str) and sub:
        return _enc("sub:" + hashlib.sha256(sub.encode()).hexdigest()[:8])
    return _enc("unknown")


def _delivered(raw) -> tuple[list[str], int]:
    """Bounded string names and the count of omitted entries."""
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        return [], 0
    names = [g[:MAX_VALUE_LEN] for g in items if isinstance(g, str)]
    omitted = len(items) - len(names)
    if len(names) > MAX_GROUPS:
        omitted += len(names) - MAX_GROUPS
        names = names[:MAX_GROUPS]
    return names, omitted


def log_rejected(oidc_data: dict, groups: list[str]) -> None:
    """Rejected sign-in: always-on event, optional group diagnostic."""
    user = user_label(oidc_data)
    reason = "no_permitted_group" if groups else "no_group_information"
    log_event("sso_login", outcome="denied", actor=user, reason=reason)
    if not settings.oidc_group_diagnostics:
        return
    if not groups:
        _logger.warning(
            "oidc.group_claim_missing user=%s claim=%s hint=%s", user, _enc(settings.oidc_groups_claim),
            '"identity provider delivered no usable group list; check the scope/property mapping"')
        return
    names, omitted = _delivered(oidc_data.get(settings.oidc_groups_claim))
    expected = {"admin": settings.oidc_admin_group, "manager": settings.oidc_manager_group,
                "user": settings.oidc_user_group}
    expected = {k: str(v)[:MAX_VALUE_LEN] for k, v in expected.items()}
    _logger.warning(
        "oidc.group_diagnostic user=%s delivered=%s omitted=%d expected=%s", user,
        json.dumps(names, ensure_ascii=True), omitted, json.dumps(expected, ensure_ascii=True))


def log_accepted(oidc_data: dict, role, groups: list[str]) -> None:
    if settings.oidc_group_diagnostics:
        _logger.info("oidc.signin_accepted user=%s role=%s groups=%d", user_label(oidc_data),
                     getattr(role, "value", role), len(groups))
