from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def bundled_pgdata() -> Path:
    """Where `fantabot-app` provisions its bundled PostgreSQL 18.

    Computed at call time, not at import: `app/fantabot_app/paths.py` resolves the same
    directory the same way and for the same reason — a home that differs between
    processes, and tests that point `HOME` elsewhere.
    """
    return Path.home() / ".fantabot" / "pgdata"


def bundled_database_url(database: str = "fantabot") -> str:
    """The DSN of the app's bundled Postgres — the canonical database.

    Derived, not configured, so that a `fantabot` command run from any directory reaches
    the same database the app writes to. `.env` pointing the CLI at a compose Postgres on
    `localhost:54321` while the app provisioned its own server is what let a week of
    Classic auction collection read as lost (`todo/TODO.md` §1).

    **Read from `postmaster.pid` when it is there**, because the DSN's *shape* is not
    fixed: pgserver listens on a unix socket on macOS/Linux and on 127.0.0.1 with a port
    it chooses on Windows, and it may place the socket outside pgdata (under `$TMPDIR`,
    when the pgdata path is too long for `sun_path`). Falling back to the socket form when
    the file is absent, torn or unreadable keeps this a *default* — `fantabot --help` must
    not become a traceback because a postmaster died mid-write.

    **Never percent-encoded.** alembic's config is a `ConfigParser`, which interpolates
    `%` and rejects a `%2F`-encoded socket path with `ValueError: invalid interpolation
    syntax`. SQLAlchemy parses the raw path fine, spaces included — verified both ways.

    This is a path, not an import: `fantabot` gains no dependency on `fantabot_app`.
    """
    pgdata = bundled_pgdata()
    socket = f"postgresql+psycopg2://postgres:@/{database}?host={pgdata}"
    try:
        lines = (pgdata / "postmaster.pid").read_text().splitlines()
        port, socket_dir, hostname = lines[3].strip(), lines[4].strip(), lines[5].strip()
    except (OSError, IndexError, ValueError):
        return socket
    if socket_dir:
        return f"postgresql+psycopg2://postgres:@/{database}?host={socket_dir}"
    if hostname and port:
        return f"postgresql+psycopg2://postgres:@{hostname}:{port}/{database}"
    return socket


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Kept, but used by nothing that signs in. `login.py` opens a headed window and
    # the human types the credentials; CLAUDE.md forbids scripting that, so these
    # three can never be what authenticates. `lega_email` and `lega_password` are
    # read only by `config-check`, which prints whether they are set and masks the
    # password. `lega_url` is read by nobody — `login.py:213` explains that it
    # deliberately navigates to the site root instead. See tasks/todo.md P3-A9.
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
    #
    # The default is the app's bundled server (see `bundled_database_url`), so the CLI and
    # the app cannot drift onto two databases again. An exported variable and a `.env`
    # entry both still override, in pydantic-settings' usual order.
    fantabot_database_url: str = Field(
        default_factory=bundled_database_url,
        repr=False,
    )

    # Fernet key for the league_tokens ciphertext column. No validator: this
    # class is instantiated at import (below), so one that rejects a malformed
    # key would turn `fantabot --help` into a traceback. TokenCipher validates.
    #
    # repr=False does NOT suppress model_dump, which is what config-check
    # prints — the name must also be in cli.py's exclude set, and a test pins it.
    fantabot_encryption_key: str = Field(default="", repr=False)
    fantabot_apileague_base_url: str = "https://apileague.fantacalcio.it"
    # The own-room FantaLab API. Reads (league record, live list) and participant
    # bids are unauthenticated — see docs/fantalab/06-asta-write-path.md §10.
    fantabot_fantalab_base_url: str = "https://api.fantalab.it"

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
    # rather than trusted. The subscription here is Foundry-routed, so the default
    # is the custom Sonnet id that login exposes, not the public "claude-sonnet-5"
    # alias. Still claude-* prefixed, so resolve_agent_model treats it as the
    # subscription path.
    fantabot_agent_model: str = "claude-sonnet-4-6-eaq-gf08h1"

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


settings = Settings()
