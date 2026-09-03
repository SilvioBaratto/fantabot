"""Application configuration using Pydantic Settings v2"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import List


class Settings(BaseSettings):
    """Application settings with environment variable support"""

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    # Project Information
    project_name: str = "FastAPI Template"
    version: str = "1.0.0"

    # API Configuration
    api_v1_str: str = "/api/v1"

    # Database Configuration - Local PostgreSQL
    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5433/app_db",
        alias="DATABASE_URL",
    )

    # CORS
    cors_origins: str = Field(default="http://localhost:4200,http://localhost:4300")

    @property
    def cors_origins_list(self) -> List[str]:
        """Get CORS origins as a list"""
        if not self.cors_origins or self.cors_origins.strip() == "":
            return [
                "http://localhost:4200",
                "http://localhost:4300",
                "http://127.0.0.1:4200",
            ]
        return [
            origin.strip() for origin in self.cors_origins.split(",") if origin.strip()
        ]

    # Logging
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")

    # Environment
    debug: bool = Field(default=True)
    environment: str = Field(default="development")

    @property
    def is_development(self) -> bool:
        """Check if running in development"""
        return self.environment == "development" or self.debug


# Create global settings instance
settings = Settings()
