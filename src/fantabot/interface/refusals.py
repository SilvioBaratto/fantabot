"""The two answers a command gives *instead of* doing its work, in one place.

`interface/` is allowed to repeat presentation — a Rich table built twice is two tables and
nothing worse. Neither of these is presentation. Which lega a command acts on, and what a
command does when Postgres is not there, are decisions, and both carry an **exit code** —
which is the part a cron wrapper reads and the part a second copy is free to disagree
about.

**`resolve_league`** was verbatim in `interface/lineup.py` and `interface/lega.py` and
inlined a third time in `interface/app.py`: same fallback, same sentence, same code 1,
across nine commands.

**`database_unreachable`** was twelve copies of the same two statements, in eleven
functions behind ten commands, across three modules. The property worth stating once is the
one `lineup submit` stated at exactly one of the twelve: **the type and a fixed sentence, never `str(exc)`** — a
driver's message can carry the connection string, and this line ends up in a file the app
renders.

Two near-copies are deliberately *not* folded in here.

* `interface/harvest.py`'s `harvest load` opens its outage branch with the same sentence and
  then says two more things, and under `--follow` it **retries** rather than exiting. Same
  first line, different decision; folding it in would make this module's "print one line and
  exit 1" untrue of one of its callers.
* `app/fantabot_app/schedule.py` refuses an install with no lega in its own words. It raises
  `ScheduleRefused` rather than printing and exiting, it does not read
  `FANTABOT_LEAGUE_ID` itself, and it adds the sentence that says what a plist carrying
  neither would do. A FastAPI service reaching into the CLI for a `typer.Exit` would be the
  worse arrangement.
"""

from __future__ import annotations

from typing import NoReturn

import typer

from fantabot.interface.console import console


def resolve_league(league: int) -> int:
    """The lega this command acts on: the flag, else `FANTABOT_LEAGUE_ID`, else refuse.

    `fantabot.config` is imported in the body and not at module scope, which is what all
    three copies did and what every command body in this package does — `Settings` is built
    when `fantabot.config` is first imported, and `interface/app.py` imports the command
    modules at import time in order to register them.

    Not every fallback is this one. `auth status` (`interface/app.py`) and the asta
    planning path's `--lega` (`interface/asta.py`) read the same setting and then
    *degrade* — an unset lega means "show every token" and "plan on the built-in band".
    They are not missing a refusal; they have nothing to refuse.
    """
    from fantabot.config import settings

    league_id = league or settings.fantabot_league_id
    if not league_id:
        console.print("[red]no lega id: pass --league or set FANTABOT_LEAGUE_ID[/red]")
        raise typer.Exit(code=1)
    return league_id


def database_unreachable(exc: BaseException) -> NoReturn:
    """Name the failure's *type* and exit 1. Never returns.

    Never `str(exc)`: a driver's message can carry the connection string, and this line is
    read back off the terminal and out of the run record the app renders.

    `from exc` is kept, so the driver's exception is still the `__cause__` of the
    `typer.Exit` rather than only its `__context__`. Typer swallows an `Exit` into a return
    code and prints neither, so this costs the operator nothing and leaves the original
    reachable to anything that does look.
    """
    console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
    raise typer.Exit(code=1) from exc
