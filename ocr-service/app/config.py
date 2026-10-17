from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"  # nosec B104 - must bind all interfaces to be reachable from other containers
    port: int = 8100
    log_level: str = "info"
    # F-011: decoded-size limits, checked from the image header before any decoding.
    max_image_pixels: int = 40_000_000
    max_image_side: int = 12_000
    # F-030: shared secret. When set, /scan and /serial require `Authorization: Bearer <key>`;
    # when empty the service runs unauthenticated (and says so at startup).
    ocr_api_key: Optional[str] = None


settings = Settings()
