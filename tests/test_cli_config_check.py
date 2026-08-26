"""T3: config-check must resolve the DSN without printing a credential.

`fantabot config-check` is run interactively and from cron. Cron captures
stdout, so anything this command prints ends up in a log file that outlives the
run. Every secret in Settings has to be masked here, not just described.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from fantabot.cli import app

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
