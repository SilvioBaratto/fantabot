"""API-specific configuration.

Only settings the HTTP adapter itself needs live here (project identity, CORS, logging).
The database URL and encryption key are **not** duplicated — they belong to fantabot and
are read from ``fantabot.config.settings`` at the point of use (the DB URL through
``database_manager``, the key only on decrypt paths). Keeping them out of here is what
makes "one DB, one engine" (SPEC A6) enforceable.
"""

from __future__ import annotations

from pydantic import AliasChoices, Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    # debug, version, environment and log_level are prefixed because DEBUG, VERSION and
    # LOG_LEVEL are common ambient shell variables that would otherwise crash or hijack startup.

    # Project Information
    project_name: str = "fantabot-app"
    version: str = Field(default="0.1.0", validation_alias=AliasChoices("FANTABOT_APP_VERSION"))

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
    log_level: str = Field(default="INFO", validation_alias=AliasChoices("FANTABOT_APP_LOG_LEVEL"))
    log_format: str = Field(default="json")

    # Environment
    debug: bool = Field(default=True, validation_alias=AliasChoices("FANTABOT_APP_DEBUG"))
    environment: str = Field(
        default="development", validation_alias=AliasChoices("FANTABOT_APP_ENVIRONMENT")
    )

    @field_validator("*", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object, info: ValidationInfo) -> object:
        """A blank value is the setting's default, as in `fantabot.config.Settings`.

        `settings = Settings()` runs at import and `load_configuration` copies `.env` into
        the environment, so a blank `FANTABOT_APP_DEBUG=` stopped the app before it started,
        and a blank log level or API prefix broke it a moment later. `cors_origins` keeps
        its blank, because `cors_origins_list` already reads one as the local origins.

        The default is returned rather than signalled with `PydanticUseDefault`, which
        recurses on a blank default under `BaseSettings`' `validate_default`.
        """
        name = info.field_name
        if name is None or name == "cors_origins":
            return value
        if isinstance(value, str) and not value.strip():
            return cls.model_fields[name].get_default(call_default_factory=True)
        return value


# Global settings instance
settings = Settings()
