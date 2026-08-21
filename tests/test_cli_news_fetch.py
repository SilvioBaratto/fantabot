"""T7: the two news-fetch behaviours that live in the CLI rather than the pipeline."""

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
