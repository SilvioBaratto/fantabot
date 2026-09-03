"""API-specific configuration.

Only settings the HTTP adapter itself needs live here (project identity, CORS, logging).
The database URL and encryption key are **not** duplicated — they belong to fantabot and
are read from ``fantabot.config.settings`` at the point of use (the DB URL through
``database_manager``, the key only on decrypt paths). Keeping them out of here is what
makes "one DB, one engine" (SPEC A6) enforceable.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    # Project Information
    project_name: str = "fantabot-app"
    version: str = "0.1.0"

    # API Configuration
    api_v1_str: str = "/api/v1"

    # CORS
    cors_origins: str = Field(default="http://localhost:4200,http://localhost:4300")

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS origins as a list, with a safe local default."""
        if not self.cors_origins or self.cors_origins.strip() == "":
            return [
                "http://localhost:4200",
                "http://localhost:4300",
                "http://127.0.0.1:4200",
            ]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    # Logging
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")

    # Environment
    debug: bool = Field(default=True)
    environment: str = Field(default="development")

    @property
    def is_development(self) -> bool:
        """True in development or when debug is on."""
        return self.environment == "development" or self.debug


# Global settings instance
settings = Settings()
