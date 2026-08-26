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

    fantabot_auto_act: bool = False

    # The l_id from docs/leghe-api.md — 4103937 for legamiallerotaie2, 3584692
    # for legamiallerotaie. Runtime state is keyed by it because the account is
    # in two leghe and one flat file could not tell them apart. 0 means unset.
    fantabot_league_id: int = 0

    # The driver must stay +psycopg2. SPEC assumption 3: fantabot is a batch
    # process, and `postgresql+asyncpg://` breaks `alembic upgrade head`.
    fantabot_database_url: str = Field(
        default="postgresql+psycopg2://postgres:postgres@localhost:54321/fantabot",
        repr=False,
    )
    # Host-side ports only; the containers always listen on 5432 and 8080.
    # 54320/18081 belong to optimizer and 5433/8090 to clipcraft.
    fantabot_db_host_port: int = 54321
    fantabot_adminer_host_port: int = 18082

    # Fernet key for the league_tokens ciphertext column. No validator: this
    # class is instantiated at import (below), so one that rejects a malformed
    # key would turn `fantabot --help` into a traceback. TokenCipher validates.
    #
    # repr=False does NOT suppress model_dump, which is what config-check
    # prints — the name must also be in cli.py's exclude set, and a test pins it.
    fantabot_encryption_key: str = Field(default="", repr=False)
    fantabot_apileague_base_url: str = "https://apileague.fantacalcio.it"

    stats_source_base_url: str = ""
    stats_source_api_key: str = Field(default="", repr=False)

    def require_credentials(self) -> None:
        if not (self.lega_email and self.lega_password and self.lega_url):
            raise RuntimeError(
                "LEGA_EMAIL, LEGA_PASSWORD, LEGA_URL must be set in .env (copy .env.example first)"
            )


settings = Settings()
