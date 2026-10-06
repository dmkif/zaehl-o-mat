"""Settings validation for the gateway path (spec 012 FR-006, data-model.md)."""
import logging

import pytest
from pydantic import ValidationError

from app.config import Settings

BASE = dict(database_url="sqlite://", jwt_secret_key="x" * 40)
GW = dict(ocr_backend="gateway", gateway_base_url="http://gw.test/v1", gateway_api_key="k")


def make(**kw):
    return Settings(_env_file=None, **{**BASE, **kw})


def test_defaults_keep_ollama_path():
    s = make()
    assert s.ocr_backend == "ollama" and s.gateway_profile == "local-only" and s.gateway_timeout_s == 300


def test_gateway_minimal_ok():
    s = make(**GW)
    assert s.ocr_backend == "gateway" and s.gateway_api_key.get_secret_value() == "k"


def test_key_is_not_printed():
    assert "k'" not in repr(make(**GW).gateway_api_key)


@pytest.mark.parametrize("timeout", [0, 120, 299.9])
def test_timeout_below_300_rejects_start(timeout):
    with pytest.raises(ValidationError, match="at least 300"):
        make(**GW, gateway_timeout_s=timeout)


def test_timeout_300_or_more_ok():
    assert make(**GW, gateway_timeout_s=600).gateway_timeout_s == 600


def test_unknown_backend_rejected():
    with pytest.raises(ValidationError, match="OCR_BACKEND"):
        make(ocr_backend="openai")


@pytest.mark.parametrize("missing", ["gateway_base_url", "gateway_api_key"])
def test_gateway_requires_url_and_key(missing):
    kw = {**GW}
    kw.pop(missing)
    with pytest.raises(ValidationError):
        make(**kw)


def test_non_local_profile_warns_but_starts(caplog):
    with caplog.at_level(logging.WARNING):
        s = make(**GW, gateway_profile="cloud")
    assert s.gateway_profile == "cloud"
    assert "not 'local-only'" in caplog.text


def test_timeout_not_checked_for_ollama_backend():
    assert make(gateway_timeout_s=10).ocr_backend == "ollama"
