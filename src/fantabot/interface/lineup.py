"""`fantabot lineup` — read, plan and submit the weekly Mantra formazione.

Typer only, like the rest of `interface/`. The network calls go through
`adapters/http/apileague`'s `gaming/v1` client; the value model, schema and matcher live in
`domain/lineup` and are composed by `application/lineup_planner`. This module holds the
commands, the presentation, and the one clock read (`_now`) — nothing here decides a lineup.

Submitting is gated by two opt-in locks (`FANTABOT_AUTO_ACT` **and** `--arm`) and is a dry
run by default, matching the auction side. The deadline is a *warning*, not a block: `mstr`
is not confirmed to be the lineup deadline (`docs/leghe-api.md`), so the platform stays the
authority — it rejects a truly-closed submit and we surface that.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

import typer

from fantabot.domain.lineup.deadline import is_past_deadline as _is_past_deadline
from fantabot.interface.console import console

if TYPE_CHECKING:
    from fantabot.domain.lineup.models import PlannedLineup


#: Re-exported: it moved to `domain/lineup/deadline.py` in 3.2 so `application/` could use
#: it, and this name is what `interface/lineup.py`'s own tests and its one call site have
#: always imported. A lift is verified by those tests passing *unchanged*.
is_past_deadline = _is_past_deadline


def _now() -> datetime:
    """The one clock read for the lineup feature — isolated so tests can reason about it."""
    return datetime.now()




def format_lineup(dto: Mapping[str, Any]) -> list[str]:
    """Render a `teamLineupDto` for the console. Pure — takes the parsed body, no I/O."""
    if not dto:
        return ["no lineup set for this competition"]
    module = dto.get("mdl", "?")
    starts = list(dto.get("starts", []))
    bench = list(dto.get("bench", []))
    return [
        f"module {module}",
        f"starters ({len(starts)}): {' '.join(str(p) for p in starts)}",
        f"bench ({len(bench)}): {' '.join(str(p) for p in bench)}",
    ]


def format_plan(plan: PlannedLineup, names: Mapping[int, str]) -> list[str]:
    """Render a `PlannedLineup` with player names for the console. Pure."""

    def nm(pid: int) -> str:
        return names.get(pid, str(pid))

    return [
        f"module {plan.module}  (league matchday {plan.mday}, Serie A {plan.cmday})",
        "XI:    " + ", ".join(nm(p) for p in plan.starts),
        "bench: " + ", ".join(nm(p) for p in plan.bench),
    ]


def _resolve_league(league: int) -> int:
    from fantabot.config import settings

    league_id = league or settings.fantabot_league_id
    if not league_id:
        console.print("[red]no lega id: pass --league or set FANTABOT_LEAGUE_ID[/red]")
        raise typer.Exit(code=1)
    return league_id


def _show(
    league: int = typer.Option(0, "--league", help="Lega id. Defaults to FANTABOT_LEAGUE_ID."),
    competition: int = typer.Option(0, "--competition", help="Competition id (required)."),
) -> None:
    """Print the current lineup for a competition. Read-only."""
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot.adapters.http import apileague
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.config import settings
    from fantabot.domain.tokens.crypto import TokenCipher
    from fantabot.domain.tokens.errors import TokenError

    league_id = _resolve_league(league)
    if not competition:
        console.print("[red]no competition id: pass --competition[/red]")
        raise typer.Exit(code=1)

    try:
        cipher = TokenCipher(settings.fantabot_encryption_key)
        with database_manager.get_session() as session:
            store = TokenStore(session, cipher)
            body = apileague.teamLineup_read(league_id, competition, store=store)
    except TokenError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    except SQLAlchemyError as exc:
        console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
        raise typer.Exit(code=1) from exc

    for line in format_lineup(body.get("teamLineupDto", {})):
        console.print(line)


def _plan(
    league: int = typer.Option(0, "--league", help="Lega id. Defaults to FANTABOT_LEAGUE_ID."),
    competition: int = typer.Option(
        0, "--competition", help="Competition id. Auto-resolved when omitted."
    ),
) -> None:
    """Build and print the best legal formation for the current matchday. **No submit.**"""
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.application.lineup_submit import build_plans
    from fantabot.config import settings
    from fantabot.domain.lineup.errors import LineupError
    from fantabot.domain.tokens.crypto import TokenCipher
    from fantabot.domain.tokens.errors import TokenError

    league_id = _resolve_league(league)
    try:
        cipher = TokenCipher(settings.fantabot_encryption_key)
        with database_manager.get_session() as session:
            store = TokenStore(session, cipher)
            # `application/`'s, not a private copy. There was one here — the seven reads,
            # the `sroles` format detection and the `tid` source, duplicated — and it was
            # what `lineup plan` ran while `lineup submit`, `GET /lineup/plan` and
            # `POST /lineup/submit` all ran the other. Identical when written, and nothing
            # made them stay that way: a fix to the format detection would have landed in
            # one, leaving the operator previewing an XI built the old way and submitting a
            # different one, with the whole suite green.
            plans, names, _ = build_plans(store, league_id, competition)
    except (TokenError, LineupError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    except SQLAlchemyError as exc:
        console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
        raise typer.Exit(code=1) from exc

    for line in format_plan(plans[0], names):
        console.print(line)


def _submit(
    league: int = typer.Option(0, "--league", help="Lega id. Defaults to FANTABOT_LEAGUE_ID."),
    competition: int = typer.Option(
        0, "--competition", help="Competition id. Auto-resolved when omitted."
    ),
    arm: bool = typer.Option(
        False, "--arm", help="Second, positive lock. Submit is OFF without it (and AUTO_ACT)."
    ),
) -> None:
    """Build the formation and submit it — **behind two locks, dry run by default.**

    Prints the plan always. Submits only when `FANTABOT_AUTO_ACT=true` **and** `--arm`; then
    warns if the matchday looks started and confirms by reading the lineup back.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.application.arming import CLI_SENTENCES
    from fantabot.application.lineup_submit import (
        ALL_MODULES_REFUSED,
        NO_MATCHDAY,
        NOT_ARMED,
        submit_lineup,
    )
    from fantabot.config import settings
    from fantabot.domain.lineup.errors import LineupError
    from fantabot.domain.tokens.crypto import TokenCipher
    from fantabot.domain.tokens.errors import TokenError

    league_id = _resolve_league(league)

    try:
        cipher = TokenCipher(settings.fantabot_encryption_key)
        with database_manager.get_session() as session:
            outcome = submit_lineup(
                TokenStore(session, cipher),
                league_id=league_id,
                competition=competition,
                arm=arm,
                now=_now,
            )
    except (TokenError, LineupError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    except SQLAlchemyError as exc:
        console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
        raise typer.Exit(code=1) from exc

    # Everything from here is presentation and an exit code. What happened is decided in
    # `application/lineup_submit.py`, so the app can reach the same eight decisions.
    if outcome.plan is not None:
        for line in format_plan(outcome.plan, outcome.names):
            console.print(line)

    if outcome.refused == NO_MATCHDAY:
        console.print(
            "[red]no matchday context for this competition (the lineup has no saved "
            "coordinates yet) — refusing to submit. Try once the matchday opens.[/red]"
        )
        raise typer.Exit(code=1)

    if outcome.refused == NOT_ARMED:
        console.print(
            f"[yellow]dry run ({outcome.arming.because(CLI_SENTENCES)}) — not submitted. "
            "Arm with FANTABOT_AUTO_ACT=true and --arm.[/yellow]"
        )
        raise typer.Exit(code=0)

    if outcome.past_deadline:
        console.print(
            f"[yellow]warning: past {outcome.past_deadline} (looks like kickoff) — "
            "submitting anyway; the platform will refuse if it is truly closed.[/yellow]"
        )
    for module, code in outcome.rejected:
        console.print(f"[yellow]{module} refused ({code}) — trying the next module.[/yellow]")

    if outcome.refused == ALL_MODULES_REFUSED:
        console.print("[red]every fieldable module was refused by the platform.[/red]")
        raise typer.Exit(code=1)

    assert outcome.submitted is not None
    if outcome.unconfirmed:
        # Exit 0, and deliberately: the POST returned 200, so the lineup is on the platform
        # and a non-zero code would send a cron wrapper back to re-POST the very lineup it
        # just saved. That retry is idempotent right up until the round closes, at which
        # point it is refused and the operator ends the matchday believing nothing was
        # fielded. The warning is loud instead, and names what could not be read.
        console.print(
            f"[green]submitted {outcome.submitted.module}[/green] "
            f"[yellow]— but the read-back failed, so this is unconfirmed: "
            f"{outcome.unconfirmed}[/yellow]"
        )
        console.print(
            "[yellow]check it with [bold]fantabot lineup show[/bold] before re-running; "
            "re-submitting is safe while the round is open and refused once it closes."
            "[/yellow]"
        )
        raise typer.Exit(code=0)

    console.print(
        f"[green]submitted {outcome.submitted.module} — saved "
        f"{len(outcome.saved.get('starts', []))} starters, "
        f"ldate {outcome.saved.get('ldate', '?')}[/green]"
    )


def register(app: typer.Typer) -> None:
    """Attach the lineup commands to the `lineup` group (called from `interface/app`)."""
    app.command("show")(_show)
    app.command("plan")(_plan)
    app.command("submit")(_submit)
