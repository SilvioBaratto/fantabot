"""`auth forget` and the app's Disconnect are two different acts with two similar names.

The CLI removes one row from `league_tokens`. `DELETE /auth/league/{id}` removes that row
*and* purges the lega across six tables through `LeagueRepository.purge`. Neither is wrong
— the app's operator disconnects a lega to be rid of it, and the CLI's reaches for
`forget` when a token has gone bad — but the names do not say which is which, and the
recovery from picking the wrong one is not symmetric: a re-login restores a token, and a
re-sync restores the six tables only if the lega is still reachable.

So both sides say what they do *and* what the other one does. The app's half is the
disconnect confirmation (`disconnect-dialog.html`); this file is the CLI's half. It
asserts on the rendered help, because a docstring nobody renders is where this fact was
already living when it went unnoticed.

`tests/interface/test_cli_token_forget.py` holds the behaviour, and is deliberately
untouched: the help text changes here, the contract it describes does not.
"""

from __future__ import annotations

import re

from typer.testing import CliRunner

from fantabot.interface.app import app

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _help() -> str:
    """The help as one line: Typer wraps to the terminal, so a phrase spans two rows."""
    result = runner.invoke(app, ["auth", "forget", "--help"])
    assert result.exit_code == 0
    return " ".join(ANSI.sub("", result.output).split())


def test_the_help_says_the_token_row_is_all_that_goes() -> None:
    """`nothing else` and not `only`: the docstring already said "the only recovery", so a
    test looking for `only` passed before a word of this was written."""
    assert "nothing else" in _help()


def test_the_help_names_the_app_disconnect_as_the_other_act() -> None:
    """The one an operator reaches for by mistake, and the one a re-login does not undo."""
    plain = _help()
    assert "Disconnect" in plain
    assert "six tables" in plain


def test_the_six_tables_are_attributed_to_the_app_and_never_to_this_command() -> None:
    """The control. Naming the purge at all is how a help text drifts into claiming it: the
    sentence that says `six tables` has to be the one that says whose act it is."""
    plain = _help()
    around = plain[max(0, plain.index("six tables") - 140) : plain.index("six tables") + 140]
    assert "Disconnect" in around
