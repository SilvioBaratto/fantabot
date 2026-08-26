"""T7: the two news-fetch behaviours that live in the CLI rather than the pipeline."""

import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fantabot.cli import app

runner = CliRunner()


def test_scope_roster_errors_instead_of_silently_fetching_the_whole_pool() -> None:
    # Falling back to `pool` would spend 523 queries for someone who asked for ~25,
    # and look like it worked.
    result = runner.invoke(app, ["news-fetch", "--scope", "roster"])

    assert result.exit_code != 0
    output = result.output.lower()
    assert "roster" in output
    assert "api" in output  # names the league-API work as the blocker


def test_scope_pool_is_accepted() -> None:
    result = runner.invoke(app, ["news-fetch", "--scope", "pool", "--limit", "1", "--no-run"])

    assert result.exit_code == 0


def test_print_prompt_with_no_run_spends_nothing() -> None:
    result = runner.invoke(app, ["news-fetch", "--limit", "1", "--print-prompt", "--no-run"])

    assert result.exit_code == 0
    assert "GIOCATORE" in result.output
    assert "Fonti preferite" in result.output


def test_no_run_stays_offline_now_that_resume_is_a_database_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--no-run must work with the stack down.

    The resume filter used to read a CSV; it is a query now, and it sits before
    this branch. Running it here would make a dry run require Postgres and
    would make every test in this module need a database to collect.
    """

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("--no-run opened a connection")

    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)

    result = runner.invoke(app, ["news-fetch", "--limit", "1", "--print-prompt", "--no-run"])

    assert result.exit_code == 0
    assert "GIOCATORE" in result.output


def test_no_run_says_its_count_is_unfiltered() -> None:
    """Silence would be worse than the caveat: someone checking "how many will
    today's run query" would read a number that has not been filtered."""
    result = runner.invoke(app, ["news-fetch", "--limit", "3", "--no-run"])

    assert result.exit_code == 0
    assert "resume filter was not applied" in result.output


def test_the_pipeline_never_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLAUDE.md's rule and nine tests depend on fetch_all returning a result
    rather than persisting one. Pushing inserts into it would make the fan-out
    untestable without a database."""
    source = Path("src/fantabot/news/pipeline.py").read_text()

    assert "fantabot.db" not in source
    assert "upsert" not in source
    assert "append_rows" not in source
