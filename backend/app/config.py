from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

# Secrets directory: pydantic-settings reads files like /run/secrets/database_url
# as values for their matching settings fields.  Environment variables take precedence.
_secrets_dir = Path("/run/secrets")


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
    oidc_discovery_url: str = ""  # e.g. https://auth.apps.schmulzer.de/application/o/zaehl-o-mat/.well-known/openid-configuration
    oidc_redirect_uri: str = "https://zaehlomat.apps.schmulzer.de/auth/callback"
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

    # Oil price fetcher
    oil_price_source: str = "heizoel-aktuell"  # heizoel-aktuell | tankerkoenig | custom
    oil_price_api_key: Optional[str] = None
    oil_price_api_url: Optional[str] = None

    # App
    app_base_url: str = "https://zaehlomat.apps.schmulzer.de"
    debug: bool = False


settings = Settings()
