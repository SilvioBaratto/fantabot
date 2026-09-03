"""F1 — the launcher CLI skeleton and its per-user paths.

These are small tests: no I/O, no Postgres, no network. They pin the public shape
of `fantabot_app` — the Typer app exposes the four commands, and paths resolve under
a per-user `~/.fantabot` home — so later tasks (F2 provisioner, F4 server) can build
on a stable skeleton.
"""

from typer.testing import CliRunner

from fantabot_app import paths
from fantabot_app.cli import app

runner = CliRunner()


def test_help_exits_clean_and_lists_the_four_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("setup", "up", "stop", "doctor"):
        assert command in result.output


def test_pgdata_lives_under_fantabot_home() -> None:
    directory = paths.pgdata()
    assert directory.name == "pgdata"
    assert directory.parent.name == ".fantabot"


def test_logs_live_under_fantabot_home() -> None:
    directory = paths.logs()
    assert directory.name == "logs"
    assert directory.parent.name == ".fantabot"


def test_home_is_the_fantabot_dir() -> None:
    assert paths.home().name == ".fantabot"
