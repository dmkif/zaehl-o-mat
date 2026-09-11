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


settings = Settings()
