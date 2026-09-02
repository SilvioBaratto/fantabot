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

from fantabot.interface.console import console

if TYPE_CHECKING:
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.domain.lineup.models import PlannedLineup


def _now() -> datetime:
    """The one clock read for the lineup feature — isolated so tests can reason about it."""
    return datetime.now()


def is_past_deadline(mstr: str, now: datetime) -> bool:
    """Whether `now` is past the `mstr` timestamp. Pure. Both compared naive (mstr carries no
    zone; a warning does not need zone precision). Unparseable `mstr` is treated as not-past."""
    try:
        deadline = datetime.fromisoformat(mstr)
    except (ValueError, TypeError):
        return False
    return now.replace(tzinfo=None) > deadline.replace(tzinfo=None)


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


def _build_plans(
    store: TokenStore, league_id: int, competition: int
) -> tuple[list[PlannedLineup], dict[int, str], int]:
    """Gather roster/settings/coords via `apileague` and compose the ranked `PlannedLineup`s.

    Roster, roles and value come from `teamLineup_read`'s `lineUpInfo`; the competition is
    auto-resolved when `competition` is 0. Returns `(plans_best_first, id->name, comp_id)`.
    """
    from fantabot.adapters.http import apileague
    from fantabot.application.lineup_planner import inputs_from_lineup, plan_lineups
    from fantabot.domain.lineup.competition import resolve_competition

    # `my_team` is the authoritative team id — used for the submit payload's `tid` (the
    # lineup DTO is empty first-of-season) and, when no --competition is given, to resolve
    # the competition. Always needed, so never a wasted read.
    tid = int(apileague.my_team(league_id, store=store)["id"])
    comp = competition or resolve_competition(
        apileague.competitions(league_id, store=store), tid=tid
    )
    body = apileague.teamLineup_read(league_id, comp, store=store)
    lineup_conf = apileague.lineup_settings(league_id, store=store)
    inputs, names = inputs_from_lineup(
        body.get("teamLineupDto", {}), body.get("lineUpInfo", []), lineup_conf, comp, tid=tid
    )
    return plan_lineups(inputs), names, comp


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
    from fantabot.config import settings
    from fantabot.domain.lineup.errors import LineupError
    from fantabot.domain.tokens.crypto import TokenCipher
    from fantabot.domain.tokens.errors import TokenError

    league_id = _resolve_league(league)
    try:
        cipher = TokenCipher(settings.fantabot_encryption_key)
        with database_manager.get_session() as session:
            store = TokenStore(session, cipher)
            plans, names, _ = _build_plans(store, league_id, competition)
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

    from fantabot.adapters.http import apileague
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.config import settings
    from fantabot.domain.lineup import payload as payload_module
    from fantabot.domain.lineup.errors import LineupError, LineupRejected
    from fantabot.domain.tokens.crypto import TokenCipher
    from fantabot.domain.tokens.errors import TokenError

    league_id = _resolve_league(league)
    auto_act = bool(settings.fantabot_auto_act)
    armed = auto_act and arm

    try:
        cipher = TokenCipher(settings.fantabot_encryption_key)
        with database_manager.get_session() as session:
            store = TokenStore(session, cipher)
            plans, names, comp = _build_plans(store, league_id, competition)

            for line in format_plan(plans[0], names):
                console.print(line)

            if plans[0].mday == 0 or plans[0].cmday == 0:
                console.print(
                    "[red]no matchday context for this competition (the lineup has no saved "
                    "coordinates yet) — refusing to submit. Try once the matchday opens.[/red]"
                )
                raise typer.Exit(code=1)

            if not armed:
                why = "--arm not given" if auto_act else "FANTABOT_AUTO_ACT is false"
                console.print(
                    f"[yellow]dry run ({why}) — not submitted. Arm with "
                    "FANTABOT_AUTO_ACT=true and --arm.[/yellow]"
                )
                raise typer.Exit(code=0)

            status = apileague.league_status(league_id, store=store)
            mstr = str(status.get("mstr", ""))
            if mstr and is_past_deadline(mstr, _now()):
                console.print(
                    f"[yellow]warning: past {mstr} (looks like kickoff) — submitting anyway; "
                    "the platform will refuse if it is truly closed.[/yellow]"
                )

            # Submit the best module; if the platform refuses it (LUP009 — a wrong schema),
            # fall to the next-best rather than failing the whole run.
            submitted: PlannedLineup | None = None
            for plan in plans:
                try:
                    apileague.teamLineup_submit(league_id, payload_module.build(plan), store=store)
                    submitted = plan
                    break
                except LineupRejected as exc:
                    console.print(
                        f"[yellow]{plan.module} refused ({exc.code}) — trying the next "
                        "module.[/yellow]"
                    )
            if submitted is None:
                console.print("[red]every fieldable module was refused by the platform.[/red]")
                raise typer.Exit(code=1)
            saved = apileague.teamLineup_read(league_id, comp, store=store).get(
                "teamLineupDto", {}
            )
    except (TokenError, LineupError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    except SQLAlchemyError as exc:
        console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(
        f"[green]submitted {submitted.module} — saved {len(saved.get('starts', []))} "
        f"starters, ldate {saved.get('ldate', '?')}[/green]"
    )


def register(app: typer.Typer) -> None:
    """Attach the lineup commands to the `lineup` group (called from `interface/app`)."""
    app.command("show")(_show)
    app.command("plan")(_plan)
    app.command("submit")(_submit)
