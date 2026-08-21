from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    lega_email: str = ""
    lega_password: str = ""
    lega_url: str = ""

    fantabot_data_dir: Path = Path("./data")
    fantabot_storage_state: Path = Path("./data/storage_state.json")
    fantabot_state_file: Path = Path("./data/state.json")

    fantabot_auto_act: bool = False

    stats_source_base_url: str = ""
    stats_source_api_key: str = Field(default="", repr=False)

    def require_credentials(self) -> None:
        if not (self.lega_email and self.lega_password and self.lega_url):
            raise RuntimeError(
                "LEGA_EMAIL, LEGA_PASSWORD, LEGA_URL must be set in .env "
                "(copy .env.example first)"
            )


settings = Settings()
