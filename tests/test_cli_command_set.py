"""The exact set of commands the CLI exposes, pinned by introspection.

**Why not by parsing `--help`.** `tests/test_cli_entrypoints.py` does that, and it
has to — its subject is that two *ways of invoking* the CLI agree, which is a
subprocess question. But its regex takes the first word of every boxed row, and a
boxed row can be an option description: it reports 18 commands, one of which is
`ledger`, a word from `asta live`'s help text. A check that hallucinates a command
cannot be the thing that tells you a command went missing.

Walking Typer's Click tree asks the question directly, and reaches nested groups,
which a flat help page does not show at all. That matters from here on: the CLI is
being reorganised into sub-apps, and this list is the single constant every step of
that reorganisation edits — so a step that silently drops or renames a command fails
here, on the line that names it, rather than in whatever runs it next.
"""

from __future__ import annotations

import click
import typer

from fantabot.cli import app

#: Every command reachable from the root, as the user types it. Sub-app commands
#: appear space-separated (`asta optimize`) once the groups land.
EXPECTED: set[str] = {
    "asta bid",
    "asta legality",
    "asta live",
    "asta optimize",
    "auth auth fantalab-login",
    "auth forget",
    "auth login",
    "auth status",
    "config-check",
    "db backfill-teams",
    "db check",
    "harvest backfill",
    "harvest collect",
    "harvest load",
    "harvest scan",
    "mantra-grid",
    "news fetch",
}


def _leaves(command: click.Command, prefix: tuple[str, ...] = ()) -> set[str]:
    """Every runnable command under `command`, as a space-joined path."""
    subcommands = getattr(command, "commands", None)
    if not subcommands:
        return {" ".join(prefix)}
    return {
        leaf
        for name, sub in subcommands.items()
        for leaf in _leaves(sub, (*prefix, name))
    }


def command_set() -> set[str]:
    return _leaves(typer.main.get_command(app))


def test_the_command_set_is_exactly_what_is_declared() -> None:
    actual = command_set()
    assert actual == EXPECTED, (
        f"missing: {sorted(EXPECTED - actual)}\nunexpected: {sorted(actual - EXPECTED)}\n"
        "If this is a deliberate rename, update EXPECTED in the same commit."
    )


def test_no_group_is_empty() -> None:
    """A group with no commands still shows in `--help` and exits 2 when run.

    Typer renders it with a blank description, so it reads as a capability that
    exists and does nothing — the failure mode of registering a sub-app and
    forgetting to attach its commands.
    """
    root = typer.main.get_command(app)
    empty = [
        name
        for name, sub in (getattr(root, "commands", None) or {}).items()
        if getattr(sub, "commands", None) == {}
    ]
    assert not empty, f"groups with no commands: {empty}"


def test_every_command_has_help_text() -> None:
    """An undocumented command is invisible in the only place users look."""
    root = typer.main.get_command(app)

    def walk(cmd: click.Command, prefix: tuple[str, ...] = ()) -> list[str]:
        subs = getattr(cmd, "commands", None)
        if not subs:
            return [] if (cmd.help or cmd.short_help) else [" ".join(prefix)]
        return [bad for name, sub in subs.items() for bad in walk(sub, (*prefix, name))]

    assert not walk(root), f"commands with no help: {walk(root)}"
