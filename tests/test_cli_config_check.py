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


def test_dsn_host_port_and_database_are_still_shown() -> None:
    """Masking is worthless if it hides what the operator came to check."""
    result = runner.invoke(app, ["config-check"])

    assert result.exit_code == 0
    assert "localhost" in result.output
    assert "54321" in result.output
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
