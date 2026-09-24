"""How a use case says what it is doing, without knowing what it is saying it to.

Seven of these modules take one -- `auth_login`, `fantalab_login`, `login_wait`,
`lega_sync`, `lineup_backtest`, `lineup_refresh` and `news_roster` -- because they narrate
as they run, and that narration is not decoration. `auth login` opens a real browser and
waits for a human; `news_roster` fans out over the roster for a job nobody is watching. A
run with no end must speak while it runs, or the summary it prints at exit is the one thing
an interrupted run never reaches.

`pricing` was named here as a third of them until 2026-09-24 and is not one: it takes
no `Reporter` at all and its presentation is entirely in `interface/app.py`. The same
correction had already landed in CLAUDE.md and not here, which is the drift this module
exists to make impossible in the other direction.

They did it by importing `interface.console`, which points the dependency the wrong way:
the application layer knew about the command layer, and every test touching them acquired
a Rich Console and a terminal width. The interface passes a `Reporter` in now, and what
each of these modules knows is that something accepts markup-bearing lines.

`Protocol` rather than a base class, so `rich.console.Console` satisfies it as it already
is -- no adapter, no wrapper, and `console` keeps being passed straight through.
"""

from __future__ import annotations

from typing import Any, Protocol


class Reporter(Protocol):
    """Anything that can be told a line. `rich.console.Console` is one."""

    def print(self, *objects: Any, **kwargs: Any) -> None: ...


class SilentReporter:
    """Says nothing. For tests that assert on return values rather than output.

    Not the default anywhere: a login that silently waits for a browser that never
    opened is the failure this whole narration exists to prevent, so the parameter is
    required and the interface supplies it.
    """

    def print(self, *objects: Any, **kwargs: Any) -> None:
        return None
