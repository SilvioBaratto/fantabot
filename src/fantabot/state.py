"""Where the browser session is stored.

This module used to hold ``data/state.json`` — an untyped ``dict[str, Any]`` with
three keys, loaded with a defaults-first merge and saved with ``default=str``.
Runtime state lives in Postgres now: ``bot_state`` for what has been done and
``auction_bids`` for what has been spent, both keyed by lega, which the flat file
could not represent.

What is left is one path, read by ``login.py`` alone. It stays a plain function
over ``settings`` and imports nothing from ``fantabot.db`` on purpose: ``login.py``
sits on this import chain and ``cli.py`` sits on that one, so ``fantabot --help``
has to work before a database exists. ``login.py`` does reach the database —
deliberately — but only inside its command body, so it can *report* an unreachable
one rather than fail to import.

(Until 2026-08-30 the chain named here ran through ``browser.py``, which imported
this module for ``context()``. That function and its two callers are gone; the
constraint is unchanged, only the route to it.)
"""

from pathlib import Path

from fantabot.config import settings


def storage_state_path() -> Path:
    """Where Playwright's cookies and localStorage snapshot is kept.

    Not in the database. It holds live session cookies and the league-scoped
    bearer token, so putting it there needs an encryption decision that SPEC
    leaves open (assumption 6, open question 1).
    """
    return settings.fantabot_storage_state
