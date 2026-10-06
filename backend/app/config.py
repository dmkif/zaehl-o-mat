import logging
from pathlib import Path
from typing import Optional

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Secrets directory: pydantic-settings reads files like /run/secrets/database_url
# as values for their matching settings fields.  Environment variables take precedence.
_secrets_dir = Path("/run/secrets")

# Minimum client timeout for the gateway path (spec 012 FR-006).
MIN_GATEWAY_TIMEOUT_S = 300.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
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

    @model_validator(mode="after")
    def _validate_security_settings(self) -> "Settings":
        if self.database_url == "postgresql://zaehlwart:zaehlwart@localhost:5432/zaehlwart":
            raise ValueError(
                "DATABASE_URL must be overridden — default credentials are insecure"
            )
        if self.jwt_secret_key == "change-me-in-production":  # nosec B105 - sentinel default, not a real credential
            raise ValueError(
                "JWT_SECRET_KEY must be set to a strong random secret"
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
