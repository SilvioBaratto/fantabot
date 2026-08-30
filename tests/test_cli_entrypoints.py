"""Both ways of running the CLI must expose the same commands.

`if __name__ == "__main__": app()` used to sit in the middle of cli.py, above
the news-fetch and mantra-grid definitions. Typer registers a command when its
decorator executes, so `python src/fantabot/cli.py` ran `app()` before those two
were defined and silently offered a shorter menu than `fantabot` did — 3
commands against 5. Invisible through the console script, which is how it
survived.

Any command added below the guard would inherit the split, so this pins the
invariant rather than the fix.

**This file does not pin the command set**, and its regex could not: it takes the
first word of every boxed row, and a boxed row can be an option description — it
reports eighteen commands, one of which is `ledger`, a word out of `asta-live`'s
help. That is harmless here, because the same regex is applied to both invocations
and the property under test is that they *agree*. The actual command set is pinned
by `tests/test_cli_command_set.py`, which walks Typer's Click tree.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

CLI_PATH = Path(__file__).resolve().parent.parent / "src" / "fantabot" / "cli.py"

# Wide enough that Typer's box never wraps a command name onto a second row.
_WIDE = {**os.environ, "COLUMNS": "200", "TERM": "dumb"}


def _commands(help_text: str) -> set[str]:
    # Typer boxes the command list; the name is the first word of each row.
    return {
        match.group(1)
        for match in re.finditer(r"^\u2502\s+([a-z][a-z-]*)\s", help_text, re.MULTILINE)
    }


def _help(*argv: str) -> str:
    result = subprocess.run(
        [sys.executable, *argv, "--help"], capture_output=True, text=True, env=_WIDE
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_direct_execution_exposes_the_same_commands_as_the_entry_point() -> None:
    # Exactly what the `fantabot` console script does.
    via_entry_point = _commands(_help("-c", "from fantabot.cli import app; app()"))
    direct = _commands(_help(str(CLI_PATH)))

    assert via_entry_point, "parsed no commands at all — the help format changed"
    assert direct == via_entry_point


def test_the_guard_is_the_last_thing_in_the_file() -> None:
    """The structural version of the same rule, and the one that actually
    explains a failure when the help-text parse gets fragile."""
    lines = [line for line in CLI_PATH.read_text().splitlines() if line.strip()]

    assert lines[-2:] == ['if __name__ == "__main__":', "    app()"]
