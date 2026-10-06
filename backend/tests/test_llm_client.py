"""
Tests for the vision-LLM transport (app.services.llm_client) and its use in
the OCR pipeline: gateway contract, error mapping, model profiles, Ollama
regression. No network: httpx.post is routed through httpx.MockTransport.
"""
import base64
import io
import json

import httpx
import pytest
from PIL import Image
from pydantic import SecretStr

from app.services import llm_client, ocr_pipeline

KEY = "sk-test-secret-key"
GW = "http://gw.test/v1"


@pytest.fixture
def gateway(monkeypatch):
    s = llm_client.settings
    monkeypatch.setattr(s, "ocr_backend", "gateway")
    monkeypatch.setattr(s, "gateway_base_url", GW)
    monkeypatch.setattr(s, "gateway_profile", "local-only")
    monkeypatch.setattr(s, "gateway_api_key", SecretStr(KEY))
    monkeypatch.setattr(s, "gateway_timeout_s", 300.0)
    monkeypatch.setattr(s, "model_profile", "")
    monkeypatch.setattr(s, "prompt_dir", "")
    return s


@pytest.fixture
def image(tmp_path):
    p = tmp_path / "meter.jpg"
    Image.new("RGB", (3000, 1200), "white").save(p, "JPEG")
    return p


def route(monkeypatch, handler):
    """Send llm_client's httpx.post/get through a MockTransport; return the seen requests."""
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(wrapped))
    monkeypatch.setattr(llm_client.httpx, "post", lambda url, **kw: client.post(url, **kw))
    monkeypatch.setattr(llm_client.httpx, "get", lambda url, **kw: client.get(url, **kw))
    return seen


def chat_reply(text, model="Qwen3.5-9B-GGUF"):
    return httpx.Response(200, json={"model": model, "choices": [{"message": {"content": text}}]})


# ── Contract (US1) ────────────────────────────────────────────────────────────

def test_gateway_request_matches_contract(gateway, image, monkeypatch):
    seen = route(monkeypatch, lambda r: chat_reply('{"reading": "031009.9", "serial": "1HLY0200026991"}'))
    reading, serial = ocr_pipeline._llm_fallback(image)
    assert (reading, serial) == ("031009.9", "1HLY0200026991")

    req = seen[0]
    assert str(req.url) == f"{GW}/chat/completions"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(req.content)
    assert body["model"] == "local-only"
    assert body["temperature"] == 0
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "response_format" not in body and "stream" not in body
    content = body["messages"][0]["content"]
    assert [c["type"] for c in content] == ["image_url", "text"]  # image first
    assert content[1]["text"].rstrip().endswith('{"reading": string|null, "serial": string|null}')
    # profile qwen3.5-9b: long edge shrunk to 2500 px
    b64 = content[0]["image_url"]["url"].split(",", 1)[1]
    assert max(Image.open(io.BytesIO(base64.b64decode(b64))).size) == 2500


@pytest.mark.parametrize(
    "text",
    [
        '{"reading": "165.539", "serial": "8DME7685272048"}',
        '```json\n{"reading": "165.539", "serial": "8DME7685272048"}\n```',
        'Here you go: {"reading": "165.539", "serial": "8DME 7685 272048"} done',
    ],
)
def test_answer_text_is_parsed(gateway, image, monkeypatch, text):
    route(monkeypatch, lambda r: chat_reply(text))
    assert ocr_pipeline._llm_fallback(image) == ("165.539", "8DME7685272048")


def test_postprocessing_still_applies(gateway, image, monkeypatch):
    """FR-010: separator insertion for a known meter type keeps working."""
    route(monkeypatch, lambda r: chat_reply('{"reading": "0309735", "serial": null}'))
    reading, _ = ocr_pipeline._llm_fallback(image, last_reading=30900.0, meter_type="electricity")
    assert reading == "030973.5"


def test_cropped_image_uses_small_edge(gateway, image, monkeypatch):
    seen = route(monkeypatch, lambda r: chat_reply("{}"))
    ocr_pipeline._llm_fallback(image, already_cropped=True)
    b64 = json.loads(seen[0].content)["messages"][0]["content"][0]["image_url"]["url"].split(",", 1)[1]
    assert max(Image.open(io.BytesIO(base64.b64decode(b64))).size) == llm_client.CROPPED_MAX_SIDE


def test_empty_or_unparsable_answer_is_not_recognised(gateway, image, monkeypatch):
    route(monkeypatch, lambda r: chat_reply(""))
    assert ocr_pipeline._llm_fallback(image) == (None, None)
    route(monkeypatch, lambda r: httpx.Response(200, text="<html>not json</html>"))
    assert ocr_pipeline._llm_fallback(image) == (None, None)


# ── Errors (US2 / US4) ────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_unavailable_statuses(gateway, image, monkeypatch, status):
    route(monkeypatch, lambda r: httpx.Response(status, json={"error": "x"}))
    with pytest.raises(llm_client.LocalModelUnavailable):
        ocr_pipeline._llm_fallback(image)


@pytest.mark.parametrize("exc", [httpx.ConnectError("boom"), httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("eof")])
def test_transport_errors_are_unavailable(gateway, image, monkeypatch, exc):
    def handler(request):
        raise exc

    route(monkeypatch, handler)
    with pytest.raises(llm_client.LocalModelUnavailable):
        ocr_pipeline._llm_fallback(image)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_config_error_statuses(gateway, image, monkeypatch, status):
    route(monkeypatch, lambda r: httpx.Response(status, json={"error": "nope"}))
    with pytest.raises(llm_client.GatewayConfigError) as err:
        ocr_pipeline._llm_fallback(image)
    assert str(err.value) == llm_client.MSG_CONFIG
    assert KEY not in str(err.value)


def test_error_messages_are_fixed_texts():
    assert str(llm_client.LocalModelUnavailable()) == "Lokales Modell nicht erreichbar, bitte später erneut versuchen"
    assert str(llm_client.GatewayConfigError()) == "Konfigurationsfehler beim KI-Gateway"


def test_no_fallback_to_other_path_on_failure(gateway, image, monkeypatch):
    """No cloud / Ollama fallback: exactly one request, to the gateway, then the error."""
    monkeypatch.setattr(llm_client.settings, "ollama_url", "http://ollama.test:11434")
    seen = route(monkeypatch, lambda r: httpx.Response(503))
    with pytest.raises(llm_client.LocalModelUnavailable):
        ocr_pipeline._llm_fallback(image)
    assert [r.url.host for r in seen] == ["gw.test"]


def test_zoom_pass_propagates_errors_and_uses_gateway(gateway, image, monkeypatch):
    seen = route(monkeypatch, lambda r: chat_reply('{"serial": "8DME7685272048"}'))
    assert ocr_pipeline._llm_serial_zoom(image) == ["8DME7685272048"]
    assert len(seen) == 3
    assert json.loads(seen[0].content)["messages"][0]["content"][1]["text"].endswith('{"serial": string|null}')
    route(monkeypatch, lambda r: httpx.Response(503))
    with pytest.raises(llm_client.LocalModelUnavailable):
        ocr_pipeline._llm_serial_zoom(image)


def test_timeout_is_at_least_300s(gateway, image, monkeypatch):
    captured = {}

    def fake_post(url, **kw):
        captured.update(kw)
        return chat_reply("{}")

    monkeypatch.setattr(llm_client.httpx, "post", fake_post)
    ocr_pipeline._llm_fallback(image)
    assert captured["timeout"].read >= 300


# ── Model profiles (US3) ──────────────────────────────────────────────────────

def test_default_profiles_per_backend(gateway):
    assert llm_client.get_profile().name == "qwen3.5-9b"
    assert llm_client.get_profile().max_side == 2500
    assert llm_client.get_profile().enable_thinking is False
    gateway.ocr_backend = "ollama"
    assert llm_client.get_profile().name == "gemma4-e4b"
    assert llm_client.get_profile().max_side == 1500


def test_unknown_profile_falls_back_to_default(gateway):
    gateway.model_profile = "does-not-exist"
    assert llm_client.get_profile().name == "qwen3.5-9b"


def test_profile_changes_edge_and_prompt(gateway, image, monkeypatch):
    gateway.model_profile = "gemma4-e4b"
    seen = route(monkeypatch, lambda r: chat_reply("{}"))
    ocr_pipeline._llm_fallback(image)
    content = json.loads(seen[0].content)["messages"][0]["content"]
    assert max(Image.open(io.BytesIO(base64.b64decode(content[0]["image_url"]["url"].split(",", 1)[1]))).size) == 1500
    assert "RED DRUMS: on mechanical roller displays" in content[1]["text"]  # v7-original prompt


def test_prompt_dir_overrides_packaged_prompt(gateway, image, tmp_path, monkeypatch):
    pdir = tmp_path / "prompts"
    pdir.mkdir()
    (pdir / "qwen3.5-9b.txt").write_text("CUSTOM PROMPT\n")
    gateway.prompt_dir = str(pdir)
    seen = route(monkeypatch, lambda r: chat_reply("{}"))
    ocr_pipeline._llm_fallback(image)
    text = json.loads(seen[0].content)["messages"][0]["content"][1]["text"]
    assert text.startswith("CUSTOM PROMPT")


def test_packaged_prompts_exist_and_v7_matches_inline_history():
    for name in ("qwen3.5-9b", "qwen3-vl-8b", "gemma4-e4b"):
        assert llm_client.load_prompt(llm_client.PROFILES[name]).startswith("Read the utility meter")


# ── Ollama path unchanged (FR-011) ────────────────────────────────────────────

def test_ollama_path_regression(image, monkeypatch):
    s = llm_client.settings
    monkeypatch.setattr(s, "ocr_backend", "ollama")
    monkeypatch.setattr(s, "ollama_url", "http://ollama.test:11434")
    monkeypatch.setattr(s, "ollama_model", "gemma4:e4b")
    monkeypatch.setattr(s, "model_profile", "")
    seen = route(monkeypatch, lambda r: httpx.Response(200, json={"response": '{"reading": "1374", "serial": null}'}))
    assert ocr_pipeline._llm_fallback(image) == ("1374", None)
    req = seen[0]
    assert str(req.url) == "http://ollama.test:11434/api/generate"
    body = json.loads(req.content)
    assert body["model"] == "gemma4:e4b"
    assert body["think"] is False and body["format"]["required"] == ["reading", "serial"]
    assert body["options"]["temperature"] == 0
    assert "Answer ONLY as JSON" not in body["prompt"]
    assert max(Image.open(io.BytesIO(base64.b64decode(body["images"][0]))).size) == 1500


def test_ollama_errors_still_degrade_to_none(image, monkeypatch):
    s = llm_client.settings
    monkeypatch.setattr(s, "ocr_backend", "ollama")
    monkeypatch.setattr(s, "ollama_url", "http://ollama.test:11434")
    route(monkeypatch, lambda r: httpx.Response(500))
    assert ocr_pipeline._llm_fallback(image) == (None, None)


def test_ollama_thinking_field_quirk(image, monkeypatch):
    s = llm_client.settings
    monkeypatch.setattr(s, "ocr_backend", "ollama")
    monkeypatch.setattr(s, "ollama_url", "http://ollama.test:11434")
    route(monkeypatch, lambda r: httpx.Response(200, json={"response": "", "thinking": '{"reading": "1365", "serial": null}'}))
    assert ocr_pipeline._llm_fallback(image) == ("1365", None)


# ── Health ────────────────────────────────────────────────────────────────────

def test_gateway_health(gateway, monkeypatch):
    seen = route(monkeypatch, lambda r: httpx.Response(200, json={"data": []}))
    assert llm_client.gateway_healthy() is True
    assert str(seen[0].url) == f"{GW}/models" and seen[0].headers["authorization"] == f"Bearer {KEY}"
    route(monkeypatch, lambda r: httpx.Response(401))
    assert llm_client.gateway_healthy() is False

    def boom(request):
        raise httpx.ConnectError("x")

    route(monkeypatch, boom)
    assert llm_client.gateway_healthy() is False
