"""OIDC group diagnostics (spec 003)."""
import logging

import pytest
from fastapi import HTTPException

from app.auth import get_or_create_user_from_oidc
from app.config import Settings, settings
from app.models import UserRole

EXPECTED = ("default-admins", "zaehlomat-manager", "zaehlomat-users")
SECRET_MAIL = "secret.person@example.com"


@pytest.fixture(autouse=True)
def groups_cfg(monkeypatch):
    monkeypatch.setattr(settings, "oidc_admin_group", EXPECTED[0])
    monkeypatch.setattr(settings, "oidc_manager_group", EXPECTED[1])
    monkeypatch.setattr(settings, "oidc_user_group", EXPECTED[2])


@pytest.fixture
def diag_on(monkeypatch):
    monkeypatch.setattr(settings, "oidc_group_diagnostics", True)


def data(groups="__absent__", **kw):
    d = {"sub": "sub-123", "email": SECRET_MAIL, "preferred_username": "alice"}
    if groups != "__absent__":
        d[settings.oidc_groups_claim] = groups
    d.update(kw)
    return d


def audit(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "app.oidc_audit"]


def security(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "app.security"]


def reject(db, caplog, payload):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(HTTPException) as ei:
        get_or_create_user_from_oidc(db, payload)
    assert ei.value.status_code == 403
    return audit(caplog), security(caplog)


# -- config ------------------------------------------------------------------
def test_default_off():
    assert Settings.model_fields["oidc_group_diagnostics"].default is False


@pytest.mark.parametrize("raw,exp", [("true", True), ("false", False)])
def test_parse(monkeypatch, raw, exp):
    monkeypatch.setenv("OIDC_GROUP_DIAGNOSTICS", raw)
    assert Settings().oidc_group_diagnostics is exp


def test_invalid_value_fails(monkeypatch):
    monkeypatch.setenv("OIDC_GROUP_DIAGNOSTICS", "maybe")
    with pytest.raises(ValueError):
        Settings()


def test_startup_warning(monkeypatch, caplog):
    from app.main import warn_if_group_diagnostics
    monkeypatch.setattr(settings, "oidc_group_diagnostics", True)
    caplog.set_level(logging.INFO)
    warn_if_group_diagnostics()
    assert any("OIDC group diagnostics are ENABLED" in r.getMessage() and r.levelno == logging.WARNING
               for r in caplog.records)


def test_no_startup_warning_when_off(monkeypatch, caplog):
    from app.main import warn_if_group_diagnostics
    monkeypatch.setattr(settings, "oidc_group_diagnostics", False)
    caplog.set_level(logging.INFO)
    warn_if_group_diagnostics()
    assert not any("diagnostics" in r.getMessage() for r in caplog.records)


# -- rejected ----------------------------------------------------------------
def test_rejected_on_shows_delivered_and_expected(db, caplog, diag_on):
    lines, sec = reject(db, caplog, data(["zaehlomat-user"]))
    assert len(lines) == 1
    line = lines[0]
    assert line.startswith("oidc.group_diagnostic ")
    assert 'user="alice"' in line and '["zaehlomat-user"]' in line and "omitted=0" in line
    for g in EXPECTED:
        assert g in line
    assert any("reason=no_permitted_group" in m and "alice" in m for m in sec)


def test_rejected_off_has_user_but_no_group_names(db, caplog):
    lines, sec = reject(db, caplog, data(["zaehlomat-user"]))
    assert lines == []
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "zaehlomat-user" not in joined
    for g in EXPECTED:
        assert g not in joined
    assert any("event=sso_login" in m and "outcome=denied" in m and "alice" in m for m in sec)


@pytest.mark.parametrize("claim", ["__absent__", None, [], {"a": 1}, 5, [1, 2]])
def test_claim_missing_distinct_line(db, caplog, diag_on, claim):
    lines, sec = reject(db, caplog, data(claim))
    assert len(lines) == 1 and lines[0].startswith("oidc.group_claim_missing ")
    assert 'claim="groups"' in lines[0] and "delivered=" not in lines[0]
    assert any("reason=no_group_information" in m for m in sec)


def test_claim_missing_off_no_audit_line(db, caplog):
    lines, sec = reject(db, caplog, data([]))
    assert lines == []


def test_hostile_name_stays_one_line(db, caplog, diag_on):
    evil = 'x"\n2026 FAKE oidc.signin_accepted user="root"\r' + "A" * 500
    lines, _ = reject(db, caplog, data([evil]))
    assert len(lines) == 1
    assert "\n" not in lines[0] and "\r" not in lines[0]
    assert "A" * 65 not in lines[0]


def test_many_groups_bounded(db, caplog, diag_on):
    lines, _ = reject(db, caplog, data([f"g{i}" for i in range(50)]))
    assert "g19" in lines[0] and "g20" not in lines[0] and "omitted=30" in lines[0]


def test_non_string_entries_counted_as_omitted(db, caplog, diag_on):
    lines, _ = reject(db, caplog, data(["a", 7, None]))
    assert '["a"]' in lines[0] and "omitted=2" in lines[0]


def test_no_email_sub_in_any_line(db, caplog, diag_on):
    lines, sec = reject(db, caplog, data(["nope"]))
    joined = " ".join(lines + sec)
    assert "example.com" not in joined and "secret.person" not in joined and "sub-123" not in joined


def test_user_label_fallbacks(db, caplog, diag_on):
    d = data(["nope"])
    del d["preferred_username"]
    lines, _ = reject(db, caplog, d)
    assert 'user="sub:' in lines[0] and "sub-123" not in lines[0] and "secret.person" not in lines[0]
    caplog.clear()
    d2 = data(["nope"])
    del d2["preferred_username"], d2["sub"]
    lines, _ = reject(db, caplog, d2)
    assert 'user="unknown"' in lines[0]


def test_long_user_name_truncated(db, caplog, diag_on):
    lines, _ = reject(db, caplog, data(["nope"], preferred_username="u" * 300 + "\n"))
    assert len(lines) == 1 and "u" * 65 not in lines[0] and "\n" not in lines[0]


# -- accepted ----------------------------------------------------------------
def test_accepted_on_role_and_count_no_names(db, caplog, diag_on):
    caplog.set_level(logging.DEBUG)
    user = get_or_create_user_from_oidc(db, data([EXPECTED[1], "other-secret"]))
    assert user.role == UserRole.manager
    lines = audit(caplog)
    assert len(lines) == 1
    assert lines[0] == 'oidc.signin_accepted user="alice" role=manager groups=2'


def test_accepted_off_no_line(db, caplog):
    caplog.set_level(logging.DEBUG)
    get_or_create_user_from_oidc(db, data([EXPECTED[2]]))
    assert audit(caplog) == []


@pytest.mark.parametrize("on", [True, False])
def test_outcome_identical(db, monkeypatch, on):
    monkeypatch.setattr(settings, "oidc_group_diagnostics", on)
    u = get_or_create_user_from_oidc(db, data([EXPECTED[0], EXPECTED[2]]))
    assert u.role == UserRole.admin
    with pytest.raises(HTTPException) as ei:
        get_or_create_user_from_oidc(db, data(["x"], sub="other"))
    assert ei.value.status_code == 403
