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

    # Agent backend. Empty = the Claude Code OAuth subscription: the default, and
    # the only path WebSearch works on. Set to an Anthropic-compatible shim to
    # route the fan-out elsewhere — Ollama's is http://localhost:11434 (the local
    # daemon; ollama.com has no /v1/messages). Cloud models go through the same
    # daemon with a ":cloud" model suffix.
    fantabot_agent_base_url: str = ""
    # Ollama ignores the value but the CLI refuses a custom base URL without one.
    fantabot_agent_auth_token: str = Field(default="ollama", repr=False)
    # Must match the backend — see resolve_agent_model for why that is checked
    # rather than trusted.
    fantabot_agent_model: str = "claude-sonnet-5"

    def resolve_agent_model(self, override: str = "") -> str:
        """The model id for one agent run, checked against the configured backend.

        ``override`` is the CLI's ``--model``, empty when not given.

        The check is two string comparisons and it catches the one mistake this
        setup invites: moving ``FANTABOT_AGENT_BASE_URL`` without moving the
        model. A ``claude-*`` id sent to an Ollama shim fails on the first player
        and then 522 more times, and a ``:cloud`` tag sent to Anthropic does the
        same in reverse — neither is worth discovering from a cron log.
        """
        model = override or self.fantabot_agent_model
        on_shim = bool(self.fantabot_agent_base_url)
        if on_shim and model.startswith("claude-"):
            raise RuntimeError(
                f"model {model!r} is an Anthropic id but FANTABOT_AGENT_BASE_URL is "
                f"{self.fantabot_agent_base_url!r}. Set FANTABOT_AGENT_MODEL to "
                f"something the shim serves, e.g. deepseek-v4-flash:cloud."
            )
        if not on_shim and not model.startswith("claude-"):
            raise RuntimeError(
                f"model {model!r} is not an Anthropic id and FANTABOT_AGENT_BASE_URL "
                f"is unset, so this run would go to the Claude Code subscription and "
                f"fail. Set the base URL, or pick a claude-* model."
            )
        return model

    def require_credentials(self) -> None:
        if not (self.lega_email and self.lega_password and self.lega_url):
            raise RuntimeError(
                "LEGA_EMAIL, LEGA_PASSWORD, LEGA_URL must be set in .env (copy .env.example first)"
            )


settings = Settings()
