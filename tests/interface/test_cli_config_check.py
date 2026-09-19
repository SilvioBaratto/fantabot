"""T3: config-check must resolve the DSN without printing a credential.

`fantabot config-check` is run interactively and from cron. Cron captures
stdout, so anything this command prints ends up in a log file that outlives the
run. Every secret in Settings has to be masked here, not just described.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from fantabot.interface.app import app

runner = CliRunner()


def test_default_dsn_password_is_not_printed() -> None:
    """The default DSN embeds postgres:postgres, which must not reach stdout."""
    result = runner.invoke(app, ["config-check"])

    assert result.exit_code == 0
    assert "postgres:postgres@" not in result.output


def test_dsn_host_and_database_are_still_shown() -> None:
    """Masking is worthless if it hides what the operator came to check.

    It used to assert the compose port, 54321. That number came from `.env`, not from a
    default, so the assertion was really about the developer's own environment — and it
    would have kept passing while the CLI and the app sat on two different databases,
    which is the failure this whole phase exists to close. What is checked now is the
    thing the operator came for: *which* database.

    It used to assert only the database name, because against the bundled server the host
    is a filesystem path that Rich broke across lines and that `render_as_string`
    percent-encoded. Both were real — and both are fixed by T28 below, which is why this
    no longer has to look away from the half of the line that matters.
    """
    result = runner.invoke(app, ["config-check"])

    assert result.exit_code == 0
    assert "fantabot_database_url" in result.output
    assert "postgresql+psycopg2://" in result.output
    assert "fantabot" in result.output


def test_env_override_is_honoured_and_still_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    from fantabot import config

    monkeypatch.setattr(
        config.settings,
        "fantabot_database_url",
        "postgresql+psycopg2://bot:hunter2@db.example.test:6543/otherdb",
    )
    result = runner.invoke(app, ["config-check"])

    assert result.exit_code == 0
    assert "hunter2" not in result.output
    assert "db.example.test" in result.output
    assert "6543" in result.output
    assert "otherdb" in result.output


def test_a_distinctive_dsn_password_never_appears(monkeypatch: pytest.MonkeyPatch) -> None:
    from fantabot import config

    monkeypatch.setattr(
        config.settings,
        "fantabot_database_url",
        "postgresql+psycopg2://u:S3cr3tCanary@localhost:54321/fantabot",
    )
    result = runner.invoke(app, ["config-check"])

    assert "S3cr3tCanary" not in result.output


def test_the_league_password_is_not_printed_either(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-existing leak, closed here rather than left beside a new mask.

    `model_dump(exclude={"stats_source_api_key"})` excluded exactly one secret,
    so `lega_password` was printed verbatim by every run of this command.
    """
    from fantabot import config

    monkeypatch.setattr(config.settings, "lega_password", "LeagueCanary99")
    result = runner.invoke(app, ["config-check"])

    assert "LeagueCanary99" not in result.output


def test_the_encryption_key_is_not_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Fernet key is the credential this whole phase exists to protect.

    `Field(repr=False)` does **not** suppress `model_dump`, which is what
    `cli.py` prints — re-verified 2026-08-26 with a canary. So the exclude set
    is the only thing standing between the key and every cron log.
    """
    from fantabot import config

    monkeypatch.setattr(config.settings, "fantabot_encryption_key", "KeyCanary777")
    result = runner.invoke(app, ["config-check"])

    assert result.exit_code == 0
    assert "KeyCanary777" not in result.output


def test_the_encryption_key_presence_is_reported_without_its_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Masked is not the same as invisible: the operator ran this to find out."""
    from fantabot import config

    monkeypatch.setattr(config.settings, "fantabot_encryption_key", "KeyCanary777")
    assert "fantabot_encryption_key set: True" in runner.invoke(app, ["config-check"]).output

    monkeypatch.setattr(config.settings, "fantabot_encryption_key", "")
    result = runner.invoke(app, ["config-check"])

    assert result.exit_code == 0
    assert "fantabot_encryption_key set: False" in result.output


def test_the_apileague_base_url_is_shown_not_masked() -> None:
    """A non-credential that looks masked is a lie about what is secret."""
    result = runner.invoke(app, ["config-check"])

    assert "apileague.fantacalcio.it" in result.output


def test_the_dead_state_file_setting_is_gone() -> None:
    """`data/state.json` was ported to bot_state/auction_bids a phase ago.

    Printing a path to a file nothing reads, one line above the new key line,
    is the kind of stale output that teaches an operator to skim.
    """
    from fantabot import config

    assert not hasattr(config.settings, "fantabot_state_file")

    output = runner.invoke(app, ["config-check"]).output
    assert "fantabot_state_file" not in output
    # `fantabot_storage_state` survives and is a different thing — asserting on
    # the bare substring "state.json" would match `storage_state.json` and pass
    # for the wrong reason.
    assert "storage_state.json" in output


def test_the_agent_auth_token_is_not_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A gateway bearer token in FANTABOT_AGENT_AUTH_TOKEN must not reach the log.

    On Ollama the value is the documented placeholder "ollama" and harmless.
    Behind LiteLLM or any other gateway it is a real credential, and
    ``config-check`` cannot tell the two apart — so it prints neither.
    """
    from fantabot import config

    monkeypatch.setattr(config.settings, "fantabot_agent_auth_token", "TokenCanary777")

    result = runner.invoke(app, ["config-check"])

    assert "TokenCanary777" not in result.output
    assert "fantabot_agent_auth_token set: True" in result.output


def test_the_agent_base_url_is_printed_in_full(monkeypatch: pytest.MonkeyPatch) -> None:
    """Routing, not a credential — and the fastest answer to "which backend ran?"."""
    from fantabot import config

    monkeypatch.setattr(config.settings, "fantabot_agent_base_url", "http://localhost:11434")
    assert "http://localhost:11434" in runner.invoke(app, ["config-check"]).output

    monkeypatch.setattr(config.settings, "fantabot_agent_base_url", "")
    assert "fantabot_agent_base_url: (subscription)" in runner.invoke(app, ["config-check"]).output


# --- T28: the DSN line is the one an operator copies ----------------------------------
#
# `config-check` exists to answer "which database am I on?", and the answer is only
# useful if it can be *used*: pasted into `alembic.ini`, into a `psql` line, into a
# `FANTABOT_DATABASE_URL=` export. Three things stood between the printed line and that.

_SOCKET_DSN = "postgresql+psycopg2://postgres:@/fantabot?host=/Users/me/.fantabot/pgdata"


def test_the_dsn_is_not_percent_encoded(monkeypatch: pytest.MonkeyPatch) -> None:
    """`%2F` is not a display detail — it makes the line unusable where it matters.

    `config.bundled_database_url` is explicit that the derived DSN is *never*
    percent-encoded, because alembic's config is a `ConfigParser`: it interpolates `%`
    and rejects a `%2F`-encoded socket path with `ValueError: invalid interpolation
    syntax`. `config-check` rendered through SQLAlchemy's `render_as_string`, which
    encodes the query string — so the one screen that tells an operator which database
    they are on printed a DSN that fails when they act on it.
    """
    from fantabot import config

    monkeypatch.setattr(config.settings, "fantabot_database_url", _SOCKET_DSN)
    result = runner.invoke(app, ["config-check"])

    assert result.exit_code == 0
    assert "%2F" not in result.output
    assert "host=/Users/me/.fantabot/pgdata" in result.output


def test_the_dsn_is_not_broken_across_lines_by_a_narrow_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rich hard-wraps mid-token at the console width, so a copied DSN is a broken one.

    Not cosmetic and not truncation: at 40 columns the line came back as three fragments
    split at arbitrary character positions (`.../fant` / `abot?host=...`).

    **The width is set on the `Console`, not through `COLUMNS`.** The first version of
    this test used `monkeypatch.setenv("COLUMNS", "40")` and was a test that could not
    fail: Rich reads `COLUMNS` in `Console.__init__` and caches it as `_width`, and
    `interface/console.py` builds its one console at import — so the environment variable
    arrived far too late and the console stayed at the suite's 200. It went red anyway,
    for the *encoding* defect the test above already covers, and green again when that was
    fixed. Caught by mutation: dropping `soft_wrap` left it passing.
    """
    from fantabot import config
    from fantabot.interface.console import console

    monkeypatch.setattr(config.settings, "fantabot_database_url", _SOCKET_DSN)
    monkeypatch.setattr(console, "width", 40)
    assert console.width == 40, "the console was not actually narrowed"

    result = runner.invoke(app, ["config-check"])

    assert result.exit_code == 0
    assert _SOCKET_DSN in result.output, (
        "the DSN did not survive a 40-column terminal in one piece:\n" + result.output
    )


def test_an_unparseable_dsn_is_reported_by_name_and_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The remedy is editing `.env`, and this is the command run to find that out.

    Deferring it to the first connect reports a misconfiguration as a database being
    down, which sends the operator to `fantabot-app db start`. The exit code matters
    because this runs from cron, where the sentence is in a log nobody reads and the code
    is the only signal — and because the rest of the screen still prints, one unparseable
    field must not cost the operator every other setting.
    """
    from fantabot import config

    monkeypatch.setattr(config.settings, "fantabot_database_url", "::not a dsn::")
    result = runner.invoke(app, ["config-check"])

    assert result.exit_code == 1
    assert "fantabot_database_url: INVALID" in result.output
    assert "fantabot_encryption_key set:" in result.output, (
        "the rest of the report was lost to one bad field"
    )


def test_an_absent_password_is_not_masked_as_if_it_existed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`postgres:***@` where there is no password is a lie in the safe direction.

    The bundled server is trust-authenticated and its DSN carries an empty password, but
    `render_as_string(hide_password=True)` masks `""` exactly like a real secret. An
    operator reading `***` concludes a password is set and configured, and goes looking
    for the wrong problem. Masking protects a credential; it must not invent one.
    """
    from fantabot import config

    monkeypatch.setattr(config.settings, "fantabot_database_url", _SOCKET_DSN)
    result = runner.invoke(app, ["config-check"])

    assert "postgres:***@" not in result.output
    assert "postgres:@" in result.output
