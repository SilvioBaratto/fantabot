"""Where the browser session is stored.

This module used to hold ``data/state.json`` — an untyped ``dict[str, Any]`` with
three keys, loaded with a defaults-first merge and saved with ``default=str``.
Runtime state lives in Postgres now: ``bot_state`` for what has been done and
``auction_bids`` for what has been spent, both keyed by lega, which the flat file
could not represent.

What is left is one path. It stays a plain function over ``settings`` and imports
nothing from ``fantabot.db`` on purpose: ``auth.py`` and ``browser.py`` sit on
this import chain, and ``fantabot auth`` has to work before a database exists.
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
