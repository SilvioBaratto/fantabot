"""`fantabot harvest collect` registration and flags.

The command itself opens a socket by design, so what is checked here is the
surface: that it exists, that it demands the shard, and that its help says so.
Behaviour lives in test_aste_stream.py, against a fake transport.
"""

from __future__ import annotations

import re

from typer.testing import CliRunner

from fantabot.cli import app

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(output: str) -> str:
    return ANSI.sub("", output)


def test_the_command_is_registered_with_its_flags() -> None:
    result = runner.invoke(app, ["harvest", "collect", "--help"])
    assert result.exit_code == 0
    plain = _plain(result.output)
    for flag in ("--one", "--shard", "--out"):
        assert flag in plain, f"{flag} is missing from the help"


def test_the_shard_is_required_because_it_cannot_be_derived() -> None:
    """Nineteen namespaces, and nothing in the uuid says which. The list card is
    the only source, so guessing here would mean probing all nineteen."""
    result = runner.invoke(app, ["harvest", "collect", "--one", "abc", "--out", "/tmp/x.jsonl"])
    assert result.exit_code != 0
    assert "shard" in _plain(result.output).lower()


def test_the_seed_reload_is_on_by_default_and_can_be_turned_off() -> None:
    """The default has to say a number, not just exist.

    A flag defaulting to 0 would leave the gap it was added to close: `harvest scan`
    rewrites the seed whenever it runs, and an asta that opens an hour into the
    evening is one the collector never hears about.
    """
    plain = _plain(runner.invoke(app, ["harvest", "collect", "--help"]).output)
    assert "--reload-seed" in plain
    assert "60" in plain and "0 = off" in plain
