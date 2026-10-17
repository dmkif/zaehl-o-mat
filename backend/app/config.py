import logging
from pathlib import Path
from typing import Optional

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Secrets directory: pydantic-settings reads files like /run/secrets/database_url
# as values for their matching settings fields.  Environment variables take precedence.
_secrets_dir = Path("/run/secrets")

# Minimum client timeout for the gateway path (spec 012 FR-006).
MIN_GATEWAY_TIMEOUT_S = 300.0

# Secret strength rules (spec 006, F-009). Error messages must never contain the value.
MIN_JWT_SECRET_LEN = 32
MIN_SUPERADMIN_PASSWORD_LEN = 12
WEAK_JWT_SECRETS = frozenset({
    "changeme", "change-me", "change-me-in-production", "secret", "password", "jwt-secret",
    "local-dev-secret-change-in-prod", "test", "default",
})
WEAK_PASSWORDS = frozenset({
    "admin", "admin123", "administrator", "password", "password123", "changeme", "change-me",
    "secret", "letmein", "12345678", "123456789012", "qwertz", "qwerty", "superadmin",
})


def _is_weak(value: str, deny: frozenset[str]) -> bool:
    """Deny-list match, case-insensitive; also an entry repeated or followed by a short suffix."""
    low = value.strip().lower()
    if len(set(low)) <= 1:  # empty, whitespace, or one repeated character
        return True
    for d in deny:
        rest = low
        while rest.startswith(d):
            rest = rest[len(d):]
        if rest != low and len(rest) <= 3:
            return True
        if low == d or (len(d) >= 16 and d in low):
            return True
    return False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        hide_input_in_errors=True,  # validation errors must never echo secret values
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        secrets_dir=_secrets_dir if _secrets_dir.is_dir() else None,
    )

    # Database
    database_url: str = "postgresql://zaehlwart:zaehlwart@localhost:5432/zaehlwart"

    # JWT
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60

    # OIDC / Authentik
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_discovery_url: str = ""  # e.g. https://idp.example.com/application/o/zaehl-o-mat/.well-known/openid-configuration
    # Must point at the backend callback endpoint (under /api), and must match
    # the redirect URI registered with the identity provider.
    oidc_redirect_uri: str = "https://zaehl-o-mat.example.com/api/auth/callback"
    oidc_groups_claim: str = "groups"

    # OIDC group → role mapping
    oidc_admin_group: str = "admin"
    oidc_manager_group: str = "verwalter"
    oidc_user_group: str = "user"
    oidc_default_manager_group: str = "verwalter"

    # Super-Admin (env-only, no OIDC required)
    superadmin_user: str = ""
    superadmin_password: str = ""

    # Reverse proxies whose X-Forwarded-For is believed (comma-separated CIDRs).
    # Only the rightmost entry that is NOT in this list is used as the client
    # address for rate limiting. '*' is rejected. Default: loopback + RFC1918
    # (compose network / k3s pod CIDR); narrow it to the real proxy range.
    trusted_proxies: str = "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"

    # File storage
    upload_path: str = "/data/uploads"

    # Ollama vision model (optional — leave ollama_url empty to disable)
    ollama_url: str = ""
    ollama_model: str = "gemma4:e4b"
    # Bearer token for hosted/proxied Ollama-compatible endpoints. A local,
    # unauthenticated Ollama instance doesn't need this.
    ollama_api_key: Optional[str] = None

    # Vision-LLM transport. "ollama" = direct Ollama API (above); "gateway" =
    # OpenAI-compatible KI-Gateway (local models only, never cloud — the
    # gateway key itself carries no cloud connection).
    ocr_backend: str = "ollama"
    gateway_base_url: str = ""  # e.g. http://ai-gateway.ai-gateway.svc:20128/v1
    gateway_profile: str = "local-only"  # sent as OpenAI `model` (gateway combo)
    gateway_api_key: Optional[SecretStr] = None
    # Local cold start / GPU host wake-up takes up to ~2 min; the gateway
    # combo times out at 280 s, so the client must wait at least 300 s.
    gateway_timeout_s: float = 300.0
    # Model profile (image edge, thinking, prompt). Empty = default for the
    # selected backend (gateway: qwen3.5-9b, ollama: gemma4-e4b).
    model_profile: str = ""
    # Directory with per-profile prompt files (<profile>.txt); empty = package prompts.
    prompt_dir: str = ""

    # OCR service (optional — leave ocr_url empty to disable)
    ocr_url: str = ""
    # Bearer token for an OCR service fronted by an authenticating proxy. A
    # local, unauthenticated OCR service doesn't need this.
    ocr_api_key: Optional[str] = None

    # Oil price fetcher
    oil_price_source: str = "heizoel-aktuell"  # heizoel-aktuell | tankerkoenig | custom
    oil_price_api_key: Optional[str] = None
    oil_price_api_url: Optional[str] = None

    # App
    app_base_url: str = "https://zaehl-o-mat.example.com"
    debug: bool = False

    # Image limits (spec 006, F-011; consumed by the upload/OCR routers)
    max_image_pixels: int = Field(default=40_000_000, gt=0)
    max_image_side: int = Field(default=12_000, gt=0)

    @model_validator(mode="after")
    def _validate_security_settings(self) -> "Settings":
        if self.database_url == "postgresql://zaehlwart:zaehlwart@localhost:5432/zaehlwart":
            raise ValueError(
                "DATABASE_URL must be overridden — default credentials are insecure"
            )
        if len(self.jwt_secret_key.strip()) < MIN_JWT_SECRET_LEN or _is_weak(self.jwt_secret_key, WEAK_JWT_SECRETS):
            raise ValueError(
                f"JWT_SECRET_KEY is missing or too weak: use at least {MIN_JWT_SECRET_LEN} random "
                "characters and no placeholder (generate: openssl rand -base64 48)"
            )
        if bool(self.superadmin_user) != bool(self.superadmin_password):
            raise ValueError("SUPERADMIN_USER and SUPERADMIN_PASSWORD must be set together or both left empty")
        if self.superadmin_password and (
            len(self.superadmin_password) < MIN_SUPERADMIN_PASSWORD_LEN
            or _is_weak(self.superadmin_password, WEAK_PASSWORDS)
        ):
            raise ValueError(
                f"SUPERADMIN_PASSWORD is too weak: use at least {MIN_SUPERADMIN_PASSWORD_LEN} characters "
                "and no well-known value (generate: openssl rand -base64 18)"
            )
        self.ocr_backend = self.ocr_backend.strip().lower()
        if self.ocr_backend not in ("ollama", "gateway"):
            raise ValueError("OCR_BACKEND must be 'ollama' or 'gateway'")
        if self.ocr_backend == "gateway":
            if not self.gateway_base_url:
                raise ValueError("GATEWAY_BASE_URL is required when OCR_BACKEND=gateway")
            if self.gateway_api_key is None or not self.gateway_api_key.get_secret_value():
                raise ValueError("GATEWAY_API_KEY is required when OCR_BACKEND=gateway")
            if self.gateway_timeout_s < MIN_GATEWAY_TIMEOUT_S:
                raise ValueError(
                    f"GATEWAY_TIMEOUT_S must be at least {MIN_GATEWAY_TIMEOUT_S:g} s "
                    "(local cold start / GPU host wake-up takes up to ~2 min)"
                )
            if self.gateway_profile != "local-only":
                logging.getLogger(__name__).warning(
                    "GATEWAY_PROFILE is not 'local-only' — only the gateway key keeps "
                    "meter images off cloud providers"
                )
        return self


settings = Settings()
