"""
Vision-LLM transport for the meter-reading pipeline.

Two ways to reach a local vision model:

- ``ollama``  — direct Ollama API (``/api/generate``), the original path.
  Errors surface as raw ``httpx`` exceptions, exactly as before.
- ``gateway`` — OpenAI-compatible KI-Gateway (``/chat/completions``). Only
  local models are reachable through the gateway key; there is no cloud
  fallback anywhere in this module. Failures are mapped to
  ``LocalModelUnavailable`` (retry later) or ``GatewayConfigError``
  (operator problem) so callers can tell "model is off" from "nothing read".

Nothing here logs request/response bodies, headers or keys.
"""
from __future__ import annotations

import base64
import io
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

MSG_UNAVAILABLE = "Lokales Modell nicht erreichbar, bitte später erneut versuchen"
MSG_CONFIG = "Konfigurationsfehler beim KI-Gateway"

_PACKAGE_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


class LLMError(Exception):
    """Base for errors of the gateway path; messages are fixed, user-safe texts."""


class LocalModelUnavailable(LLMError):
    """No local model answered (connection, timeout, 408/429/5xx). Retry later."""

    def __init__(self) -> None:
        super().__init__(MSG_UNAVAILABLE)


class GatewayConfigError(LLMError):
    """Gateway rejected the request (401/403/other 4xx). Operator must fix config."""

    def __init__(self) -> None:
        super().__init__(MSG_CONFIG)


@dataclass(frozen=True)
class ModelProfile:
    name: str
    max_side: int
    enable_thinking: bool
    prompt_file: str


PROFILES: dict[str, ModelProfile] = {
    "qwen3.5-9b": ModelProfile("qwen3.5-9b", 2500, False, "qwen3.5-9b.txt"),
    "qwen3-vl-8b": ModelProfile("qwen3-vl-8b", 2500, False, "qwen3-vl-8b.txt"),
    "gemma4-e4b": ModelProfile("gemma4-e4b", 1500, False, "v7-original.txt"),
}

_DEFAULT_PROFILE = {"gateway": "qwen3.5-9b", "ollama": "gemma4-e4b"}

# Cropped display images need far fewer pixels than full meter photos.
CROPPED_MAX_SIDE = 800


def using_gateway() -> bool:
    return settings.ocr_backend == "gateway"


def llm_enabled() -> bool:
    """True when a vision-LLM path is configured (gateway or Ollama)."""
    if using_gateway():
        return bool(settings.gateway_base_url)
    return bool(settings.ollama_url)


def get_profile() -> ModelProfile:
    name = settings.model_profile or _DEFAULT_PROFILE["gateway" if using_gateway() else "ollama"]
    profile = PROFILES.get(name)
    if profile is None:
        fallback = _DEFAULT_PROFILE["gateway" if using_gateway() else "ollama"]
        logger.warning("Unknown MODEL_PROFILE %r — using %s", name, fallback)
        profile = PROFILES[fallback]
    return profile


def load_prompt(profile: ModelProfile) -> str:
    """Prompt text for a profile; PROMPT_DIR/<profile>.txt overrides the packaged file."""
    candidates = []
    if settings.prompt_dir:
        candidates.append(Path(settings.prompt_dir) / f"{profile.name}.txt")
    candidates.append(_PACKAGE_PROMPTS / profile.prompt_file)
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8").rstrip("\n")
    raise FileNotFoundError(f"prompt file for profile {profile.name} not found")


def load_image(filepath: Path):
    """Open an image with EXIF rotation applied (phone photos carry Orientation=6)."""
    from PIL import Image, ImageOps

    return ImageOps.exif_transpose(Image.open(filepath))


def encode_image(img, max_side: int) -> str:
    """Shrink (never enlarge) to ``max_side`` on the long edge, JPEG, base64."""
    from PIL import Image

    if max(img.size) > max_side:
        scale = max_side / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def _gateway_headers() -> dict[str, str]:
    key = settings.gateway_api_key.get_secret_value() if settings.gateway_api_key else ""
    return {"Authorization": f"Bearer {key}"}


def _gateway_url(path: str) -> str:
    return f"{settings.gateway_base_url.rstrip('/')}/{path}"


def _raise_for_gateway_status(status: int) -> None:
    if status < 400:
        return
    if status in (408, 429) or status >= 500:
        raise LocalModelUnavailable()
    raise GatewayConfigError()


def gateway_complete(b64_image: str, prompt: str, profile: ModelProfile) -> tuple[str, str | None]:
    """
    One chat completion against the gateway. Returns (text, answering_model).
    Contract: specs/012-zaehl-o-mat-gateway-local/contracts/gateway-chat.md
    """
    body = {
        "model": settings.gateway_profile,
        "temperature": 0,
        "max_tokens": 256,
        "chat_template_kwargs": {"enable_thinking": profile.enable_thinking},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }
    timeout = httpx.Timeout(connect=10.0, read=settings.gateway_timeout_s, write=30.0, pool=10.0)
    started = time.monotonic()
    try:
        resp = httpx.post(
            _gateway_url("chat/completions"), json=body, headers=_gateway_headers(), timeout=timeout
        )
    except httpx.HTTPError as exc:
        # Only the exception class is logged: httpx messages can echo URLs.
        logger.warning("gateway request failed (%s) after %.1fs", type(exc).__name__, time.monotonic() - started)
        raise LocalModelUnavailable() from None
    try:
        _raise_for_gateway_status(resp.status_code)
    except LLMError as err:
        logger.warning("gateway answered HTTP %s after %.1fs", resp.status_code, time.monotonic() - started)
        raise err from None
    try:
        data = resp.json()
        text = (data["choices"][0]["message"].get("content") or "").strip()
        model = data.get("model") if isinstance(data.get("model"), str) else None
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        text, model = "", None
    logger.info(
        "gateway ok profile=%s model=%s duration=%.1fs", profile.name, model, time.monotonic() - started
    )
    return text, model


def ollama_complete(b64_image: str, prompt: str, schema: dict, model: str, timeout: float) -> str:
    """Original Ollama call. Raises raw httpx errors like before (callers swallow them)."""
    resp = httpx.post(
        f"{settings.ollama_url}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "images": [b64_image],
            "stream": False,
            # JSON Schema enforces field names and nullable types (better than "json" string)
            "format": schema,
            "think": False,
            "options": {"temperature": 0, "num_ctx": 8192, "num_image_tokens": 1120},
        },
        headers=_ollama_headers(),
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()
    # Ollama-Quirk (verified 2026-07-17, qwen3-vl + structured output):
    # the JSON answer lands in "thinking" while "response" stays empty.
    return (body.get("response", "") or body.get("thinking", "") or "").strip()


def _ollama_headers() -> dict[str, str] | None:
    if settings.ollama_api_key:
        return {"Authorization": f"Bearer {settings.ollama_api_key}"}
    return None


def complete(b64_image: str, prompt: str, schema: dict, profile: ModelProfile, ollama_timeout: float) -> str:
    """Dispatch to the configured path; returns the raw answer text."""
    if using_gateway():
        text, _ = gateway_complete(b64_image, prompt, profile)
        return text
    return ollama_complete(b64_image, prompt, schema, settings.ollama_model, ollama_timeout)


def gateway_healthy() -> bool:
    """GET /models with the key (3 s). Never raises, never exposes error details."""
    try:
        r = httpx.get(_gateway_url("models"), headers=_gateway_headers(), timeout=3.0)
        return r.status_code == 200
    except Exception:  # nosec B110 - optional subsystem, health check must not crash
        return False
