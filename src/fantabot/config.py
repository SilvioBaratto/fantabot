import os
from collections.abc import Iterable
from pathlib import Path

from pydantic import Field, TypeAdapter, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


def bundled_pgdata() -> Path:
    """Where `fantabot-app` provisions its bundled PostgreSQL 18.

    Computed at call time, not at import: `app/fantabot_app/paths.py` resolves the same
    directory the same way and for the same reason — a home that differs between
    processes, and tests that point `HOME` elsewhere.
    """
    return Path.home() / ".fantabot" / "pgdata"


def default_harvest_dir() -> Path:
    """Where the harvest artefacts live when nothing overrides: ``~/.fantabot/aste_live``.

    Separate from `harvest_dir` below so `Settings` can use it as a default factory
    without importing itself.
    """
    return Path.home() / ".fantabot" / "aste_live"


def harvest_dir() -> Path:
    """The landing zone, the seed and the listone bridge — one home, derived not relative.

    They sat under ``./data/aste_live/``, which only resolves from the repository root.
    The app's working directory is wherever its launcher was started, so a collector
    started from the app and a `harvest load` typed in a terminal addressed two different
    landing zones — the same split that made `bundled_database_url` derive from
    `bundled_pgdata()` rather than read `.env` (the two-databases incident; root
    `CLAUDE.md`, "One database, and it is the app's").

    **A fresh `Settings` per call, not the module singleton**, for the reason
    `bundled_pgdata` is a function: the singleton binds `Path.home()` at import, and both
    a test that repoints ``HOME`` and a launcher running under another one must see the
    new path. Constructing it here also keeps ``FANTABOT_HARVEST_DIR`` winning at call
    time — which is what an operator whose home volume cannot hold 1.4 GB of landing zone
    reaches for. A few Settings constructions per command is not a cost worth caching.
    """
    return Settings().fantabot_harvest_dir


AUTO_ACT_VAR = "FANTABOT_AUTO_ACT"
"""The ambient arming lock's variable name, as the operator types it into `.env`."""

_DOTENV_INJECTED: dict[str, Path] = {}
"""Names a launcher copied out of a `.env` into ``os.environ``, and the file each came from.

Written by :func:`note_dotenv_injection`, read only by :func:`live_auto_act`. It exists to
tell two indistinguishable things apart: a variable the operator **exported**, which is them
speaking later than the file and must win, and one a launcher **copied** out of `.env` at
boot, which is the file speaking and must not outrank a later edit of that same file.
"""


def note_dotenv_injection(path: Path, names: Iterable[str]) -> None:
    """Record that *names* were injected into ``os.environ`` from the `.env` at *path*.

    Called once by the app's launcher, right after its `load_dotenv(..., override=False)`.
    The CLI never calls it and does not need to: it is one process per invocation, so
    nothing it read can go stale within a run.
    """
    _DOTENV_INJECTED.clear()
    _DOTENV_INJECTED.update(dict.fromkeys(names, path))


def live_auto_act() -> bool:
    """`FANTABOT_AUTO_ACT` **now** — re-read, not remembered. Fails closed.

    `application/arming.py` promised this and did not do it: it read
    ``settings.fantabot_auto_act``, and `settings` is the module singleton built at first
    import (below), so a long-lived app server answered every request with the state of the
    world at boot. Editing `.env` to disarm did nothing, and neither did changing
    ``os.environ`` — the operator who disarms at 21:47 and does not restart the server was
    still armed. Same argument as :func:`harvest_dir`, on the one setting where being stale
    means acting when told not to.

    Precedence, and it is the repository's existing one (root `CLAUDE.md`, on
    ``FANTABOT_HARVEST_DIR``: *"An exported variable still wins"*):

    1. a genuinely **exported** variable — the operator speaking later than the file;
    2. otherwise the `.env`, **re-read from disk on every call**.

    Only :data:`_DOTENV_INJECTED` separates those, because after a launcher's
    ``load_dotenv`` both look identical in ``os.environ``.

    Anything unreadable, absent or unparseable is ``False``. The ambient lock is the
    conservative one; a re-read that failed open would flip the default that root
    `CLAUDE.md` says not to flip.
    """
    raw: str | None = os.environ.get(AUTO_ACT_VAR)
    if raw is None or AUTO_ACT_VAR in _DOTENV_INJECTED:
        raw = _dotenv_value(AUTO_ACT_VAR)
    if raw is None:
        return False
    try:
        return bool(TypeAdapter(bool).validate_python(raw.strip()))
    except ValidationError:
        return False


#: The three lineup settings, by env name. Re-read like `live_auto_act`, and for the same
#: reason: `settings` is the module singleton built at first import, so a long-lived app
#: server would answer every request with the state of the world at boot — and an operator
#: who switched the model at 21:47 without restarting would keep getting the old one.
LINEUP_MODEL_VAR = "FANTABOT_LINEUP_MODEL"
LINEUP_SUB_MODE_VAR = "FANTABOT_LINEUP_SUB_MODE"
LINEUP_NEWS_VAR = "FANTABOT_LINEUP_NEWS"


def live_setting(name: str) -> str | None:
    """One setting's value **now**, with `live_auto_act`'s precedence and none of its parsing.

    Parsing belongs at the point of use (AD4): each of the three fails closed differently —
    an unknown model is `indexcompare`, an unknown sub mode is "the operator has not said",
    and anything but an explicit yes leaves the news off — so this returns the raw string
    and the caller decides what it could not read.
    """
    raw: str | None = os.environ.get(name)
    if raw is None or name in _DOTENV_INJECTED:
        raw = _dotenv_value(name)
    return raw


def _dotenv_value(name: str) -> str | None:
    """One name's current value in the `.env`, read fresh. ``None`` if anything is wrong.

    The file is the one the launcher injected from when there is one, and otherwise `.env`
    relative to the working directory — which is what ``Settings.model_config``'s
    ``env_file=".env"`` resolves, so the CLI and the app read the same file they always did.
    """
    path = _DOTENV_INJECTED.get(name, Path(".env"))
    try:
        from dotenv import dotenv_values

        return dotenv_values(path).get(name)
    except OSError:
        return None


#: The evening's only record, as the CLI has always named it.
JOURNAL_FILE = "room_journal.jsonl"


def journal_path() -> Path:
    """The live room's journal — one derived path, four literals replaced.

    `interface/asta.py` joined this by hand in `asta room` and again in `asta bid`, and
    the app's endpoint twice more. Four spellings of one file is four chances to disagree
    about the evening's only record, and `harvest_dir` is the precedent for closing that.

    **The home deliberately does not move.** `fantabot_data_dir` is `Path("./data")` and
    *relative*, unlike `fantabot_harvest_dir`, so a writer and a reader agree only when
    both processes were started from the repository root — a real footgun, kept, because
    moving it would move an artefact the CLI owns and the 2026-09-01 audit was performed
    against. What this buys is that every caller now has the *same* footgun, stated here
    once, and `.resolve()` means a viewer can say which file it actually read.

    A fresh `Settings` per call, for `harvest_dir`'s reason: the module singleton binds at
    import, so an exported value has to win at call time.
    """
    return (Settings().fantabot_data_dir / JOURNAL_FILE).resolve()


#: The scheduled lineup's run record, one JSONL line per `lineup submit --scheduled`.
LINEUP_RUNS_FILE = "lineup_runs.jsonl"


def lineup_runs_path() -> Path:
    """Where every scheduled lineup run is recorded, and where the app reads the history.

    **Derived from the home directory, not from `./data`.** `journal_path` above is relative
    and says so: a writer and a reader agree only when both started from the repository
    root. The `launchd` job starts in the repository and the app starts wherever its
    launcher was, so a relative path here would put the record where the app never looks —
    the silent failure the record exists to prevent. `harvest_dir` is the precedent.

    Read at call time rather than bound at import, so a test that repoints the home
    directory sees it.

    ⚠ "repoints `HOME`" is what this said, and it is only true on POSIX. `Path.home()` is
    `os.path.expanduser("~")`, and `ntpath` reads **`USERPROFILE`** and ignores `HOME`
    entirely — so a test that set one variable redirected nothing on Windows, read the
    operator's real home and found no record. The app's test suite redirects through one
    helper that sets both; see `api/tests/conftest.redirect_home`.
    """
    return Path.home() / ".fantabot" / LINEUP_RUNS_FILE


#: The refresh marker: which source last succeeded, for which matchday, and when.
LINEUP_REFRESH_FILE = "lineup_refresh.json"


def lineup_refresh_path() -> Path:
    """Where the hourly refresh records what it has already done.

    Beside `lineup_runs.jsonl` and derived the same way, for the same reason: the `launchd`
    job starts in the repository and the app starts wherever its launcher was, so a path
    relative to `./data` would let the writer and a reader disagree about the one file that
    says whether this hour's work is still owed. A marker nobody can find is a marker that
    re-runs every source, every hour, for ever.

    Read at call time rather than bound at import, so a test that repoints the home
    directory sees it — and `redirect_home` sets `USERPROFILE` too, because `ntpath`
    ignores `HOME`.
    """
    return Path.home() / ".fantabot" / LINEUP_REFRESH_FILE


def bundled_database_url(database: str = "fantabot") -> str:
    """The DSN of the app's bundled Postgres — the canonical database.

    Derived, not configured, so that a `fantabot` command run from any directory reaches
    the same database the app writes to. `.env` pointing the CLI at a compose Postgres on
    `localhost:54321` while the app provisioned its own server is what let a week of
    Classic auction collection read as lost (root `CLAUDE.md`, "One database, and it is
    the app's").

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

    # The harvest home (see `harvest_dir`). A `Path`, so an exported value is a path and
    # not a string that later concatenates wrong.
    fantabot_harvest_dir: Path = Field(default_factory=default_harvest_dir)

    fantabot_data_dir: Path = Path("./data")
    fantabot_storage_state: Path = Path("./data/storage_state.json")

    fantabot_auto_act: bool = False

    # The l_id from docs/leghe-api.md — 4103937 for legamiallerotaie2, 3584692
    # for legamiallerotaie. Runtime state is keyed by it because the account is
    # in two leghe and one flat file could not tell them apart. 0 means unset.
    fantabot_league_id: int = 0

    # The lineup model, the lega's substitution mode and the news gate (SPEC A4/A7/A19(5)).
    # `str | None`, never `Literal` and never required: `Settings()` runs at import, and a
    # typo in `.env` must not stop the hourly job at the import line. Each is parsed at its
    # own point of use and fails closed — an unrecognised model falls back to
    # `indexcompare`, an unrecognised sub mode is "the operator has not said", and anything
    # but an explicit yes leaves the news off.
    fantabot_lineup_model: str | None = None
    fantabot_lineup_sub_mode: str | None = None
    fantabot_lineup_news: str | None = None

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
    # Where `pytest -m db` writes. A separate database on the same bundled server, because
    # the tier's write tests must not be able to reach the canonical one — `pytest -m db`
    # once deleted a real player's weekly reading. `tests/conftest.py` refuses to run when
    # this resolves to the same database name as the setting above.
    #
    #     fantabot-app db create fantabot_test
    #     FANTABOT_DATABASE_URL="$(fantabot-app db url --database fantabot_test)" \
    #       alembic upgrade head
    fantabot_test_database_url: str = Field(
        default_factory=lambda: bundled_database_url("fantabot_test"),
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
