"""`fantabot aste-collect` registration and flags.

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
    result = runner.invoke(app, ["aste-collect", "--help"])
    assert result.exit_code == 0
    plain = _plain(result.output)
    for flag in ("--one", "--shard", "--out"):
        assert flag in plain, f"{flag} is missing from the help"


def test_the_shard_is_required_because_it_cannot_be_derived() -> None:
    """Nineteen namespaces, and nothing in the uuid says which. The list card is
    the only source, so guessing here would mean probing all nineteen."""
    result = runner.invoke(app, ["aste-collect", "--one", "abc", "--out", "/tmp/x.jsonl"])
    assert result.exit_code != 0
    assert "shard" in _plain(result.output).lower()
