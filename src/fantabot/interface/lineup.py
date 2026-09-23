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
from rich.markup import escape

from fantabot.domain.lineup.deadline import is_past_deadline as _is_past_deadline
from fantabot.interface.console import console

if TYPE_CHECKING:
    from fantabot.application.lineup_projection import ProjectionReport
    from fantabot.domain.lineup.freshness import Freshness
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


#: The two value models `plan` can rank on. `indexcompare` is the platform's own rating and
#: the default everywhere; `projection` is phase `lineup-theory`'s μ/p model, which no submit
#: path uses yet — `plan --model projection` is a preview, and T36 is what wires it.
INDEXCOMPARE, PROJECTION = "indexcompare", "projection"


def format_projection_rows(report: ProjectionReport) -> list[tuple[str, ...]]:
    """The μ/p/sigma_tilde table as strings, header first. Pure."""
    rows: list[tuple[str, ...]] = [("player", "role", "mu", "p", "sigma~", "value", "XI")]
    starting = set(report.projection.plans[0].starts) if report.projection.plans else set()
    for line in report.projection.lines:
        rows.append(
            (
                report.names.get(line.player_id, str(line.player_id)),
                line.role,
                f"{line.mu:.2f}",
                f"{line.p:.2f}",
                f"{line.sigma_tilde:.2f}",
                f"{line.value:.2f}",
                "XI" if line.player_id in starting else "",
            )
        )
    return rows


def format_freshness(freshness: Freshness) -> list[str]:
    """The staleness verdict (T25) as the operator reads it. Pure."""
    head = "fresh" if freshness.fresh else "STALE"
    lines = [f"data: {head}" + ("" if freshness.fresh else ": " + "; ".join(freshness.reasons))]
    lines += [f"  warning: {warning}" for warning in freshness.warnings]
    return lines


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
    model: str = typer.Option(
        INDEXCOMPARE,
        "--model",
        help=f"Value model: {INDEXCOMPARE} (the default) or {PROJECTION} (preview).",
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
    if model not in (INDEXCOMPARE, PROJECTION):
        console.print(f"[red]unknown model {model!r}: {INDEXCOMPARE} or {PROJECTION}[/red]")
        raise typer.Exit(code=1)
    if model == PROJECTION:
        _plan_projection(league_id, competition)
        return
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


def _plan_projection(league_id: int, competition: int) -> None:
    """Both plans, the μ/p/sigma_tilde table and the staleness verdict. Read-only.

    The one import of the projection branch on this path, and the edge
    `tests/domain/lineup/test_lineup_imports.py` declares: numpy and scipy load here and
    nowhere the hourly `indexcompare` submit can reach.
    """
    from rich.table import Table
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.application.lineup_projection import projection_for_league
    from fantabot.config import settings
    from fantabot.domain.lineup.errors import LineupError
    from fantabot.domain.tokens.crypto import TokenCipher
    from fantabot.domain.tokens.errors import TokenError

    try:
        cipher = TokenCipher(settings.fantabot_encryption_key)
        with database_manager.get_session() as session:
            report = projection_for_league(
                TokenStore(session, cipher),
                league_id=league_id,
                competition=competition,
                as_of=_now().date(),
            )
    except (TokenError, LineupError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    except ValueError as exc:
        # The projection refuses rather than guessing when the history cannot carry it
        # (`project`: no rows, or nobody with two appearances to vary between).
        console.print(f"[red]no projection: {exc}[/red]")
        raise typer.Exit(code=1) from None
    except SQLAlchemyError as exc:
        console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
        raise typer.Exit(code=1) from exc

    outcome = report.projection
    console.print(
        f"as of {outcome.as_of} over {outcome.seasons[0]}..{outcome.seasons[-1]}, "
        f"replacement level {outcome.replacement:.2f}"
    )
    for line in format_freshness(outcome.freshness):
        console.print(line)
    console.print(f"\n[bold]{INDEXCOMPARE}[/bold] (what a submit would field today)")
    for line in format_plan(report.baseline[0], report.names):
        console.print(line)
    console.print(f"\n[bold]{PROJECTION}[/bold]")
    for line in format_plan(outcome.plans[0], report.names):
        console.print(line)

    header, *rows = format_projection_rows(report)
    table = Table(*header, title=None)
    for row in rows:
        table.add_row(*row)
    console.print(table)


def _submit(
    league: int = typer.Option(0, "--league", help="Lega id. Defaults to FANTABOT_LEAGUE_ID."),
    competition: int = typer.Option(
        0, "--competition", help="Competition id. Auto-resolved when omitted."
    ),
    arm: bool = typer.Option(
        False, "--arm", help="Second, positive lock. Submit is OFF without it (and AUTO_ACT)."
    ),
    scheduled: bool = typer.Option(
        False,
        "--scheduled",
        help="The unattended run (launchd): once the matchday has started, skip instead of "
        "warning — never reshuffle a lineup in play.",
    ),
) -> None:
    """Build the formation and submit it — **behind two locks, dry run by default.**

    Prints the plan always. Submits only when `FANTABOT_AUTO_ACT=true` **and** `--arm`; then
    warns if the matchday looks started and confirms by reading the lineup back.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot.adapters.files.lineup_runs import LineupRun, append_run
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.application.arming import CLI_SENTENCES
    from fantabot.application.lineup_submit import (
        ALL_MODULES_REFUSED,
        NO_MATCHDAY,
        NOT_ARMED,
        failed_run,
        run_record,
        submit_lineup,
    )
    from fantabot.config import lineup_runs_path, settings
    from fantabot.domain.lineup.deadline import (
        MATCHDAY_MISMATCH,
        MATCHDAY_STARTED,
        NO_START_TIME,
    )
    from fantabot.domain.lineup.errors import LineupError
    from fantabot.domain.tokens.crypto import TokenCipher
    from fantabot.domain.tokens.errors import TokenError

    league_id = _resolve_league(league)

    # The run record — `--scheduled` only: the history is the automation's, and a person at
    # the terminal has already read the answer. Stamped once, by `_now`, the lineup
    # surface's one clock read; `astimezone` attaches this machine's offset without a
    # second one.
    at = _now().astimezone().isoformat(timespec="seconds")

    def record(run: LineupRun) -> None:
        if not scheduled:
            return
        if not append_run(lineup_runs_path(), run):
            console.print(f"[yellow]could not write the run record at {lineup_runs_path()}[/yellow]")

    try:
        cipher = TokenCipher(settings.fantabot_encryption_key)
        with database_manager.get_session() as session:
            outcome = submit_lineup(
                TokenStore(session, cipher),
                league_id=league_id,
                competition=competition,
                arm=arm,
                now=_now,
                scheduled=scheduled,
            )
    except (TokenError, LineupError) as exc:
        # Recorded before anything else: these stop the run before a plan exists, which is
        # exactly the Saturday the record has to speak for.
        record(failed_run(type(exc).__name__, str(exc), league_id=league_id,
                          scheduled=scheduled, at=at))
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    except SQLAlchemyError as exc:
        # The type and a fixed sentence, never `str(exc)`: a driver's message can carry the
        # connection string, and this line ends up in a file the app renders.
        record(failed_run(
            "database-unreachable",
            f"the bundled Postgres is not reachable ({type(exc).__name__}) — the token lives "
            "there. Start it with `fantabot-app db start`.",
            league_id=league_id, scheduled=scheduled, at=at,
        ))
        console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
        raise typer.Exit(code=1) from exc

    record(run_record(outcome, league_id=league_id, scheduled=scheduled, at=at))

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

    # A scheduled run past its matchday's start is doing its job, not failing: exit 0, so a
    # `launchd` log full of Saturday-night runs does not read as a broken job. An unreadable
    # start is different — nothing could be verified, and that is worth a non-zero code.
    if outcome.refused in (MATCHDAY_STARTED, MATCHDAY_MISMATCH):
        console.print(f"[dim]scheduled run skipped: {outcome.detail}[/dim]")
        raise typer.Exit(code=0)
    if outcome.refused == NO_START_TIME:
        console.print(f"[red]scheduled run refused: {outcome.detail}[/red]")
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


def _refresh(
    league: int = typer.Option(0, "--league", help="Lega id. Defaults to FANTABOT_LEAGUE_ID."),
    competition: int = typer.Option(
        0, "--competition", help="Competition id. Needed only to read the roster for news."
    ),
    cmday: int = typer.Option(
        0, "--cmday", help="Serie A giornata being planned. 0 = read it from the platform."
    ),
    season: str = typer.Option("2026/27", "--season", help="Which stagione to refresh."),
    source: list[str] = typer.Option(
        [], "--source", help="Only these sources: voti, lega, news. Repeatable."
    ),
    force: bool = typer.Option(False, "--force", help="Re-run a source already done today."),
) -> None:
    """Bring the history up to date for this matchday. Both the child process and the tool.

    `lineup submit --refresh` runs exactly this, as a child process in its own group with a
    hard timeout — so the command an operator types to recover by hand and the one the job
    runs are the same command, and a fix to one is a fix to both [coverage-9].

    It never fails the way a source does: a site down or a token expired is one source
    reported failed and owed again next hour, and the exit code is 1 only so a wrapper can
    tell a clean hour from a dirty one.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot.adapters.files.refresh_marker import FileMarkerStore
    from fantabot.application.lineup_refresh import (
        LiveRefreshSources,
        read_refresh_inputs,
        run_refresh,
    )
    from fantabot.domain.tokens.errors import TokenError

    league_id = _resolve_league(league)
    matchday = cmday or _read_cmday(league_id, competition)
    if matchday <= 0:
        console.print(
            "[red]no matchday: pass --cmday, or --competition so it can be read[/red]"
        )
        raise typer.Exit(code=2)

    try:
        inputs = read_refresh_inputs(
            league_id,
            season=season,
            cmday=matchday,
            day=_now().date(),
            competition=competition,
        )
    except TokenError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    except SQLAlchemyError as exc:
        console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
        raise typer.Exit(code=1) from exc

    report = run_refresh(
        inputs,
        sources=LiveRefreshSources(reporter=console),
        marker_store=FileMarkerStore(),
        reporter=console,
        now=_now(),
        only=tuple(source),
        force=force,
    )
    for line in report.lines():
        console.print(escape(line))
    if not report.ok:
        raise typer.Exit(code=1)


def _read_cmday(league_id: int, competition: int) -> int:
    """The Serie A giornata the lega is on, from the lineup DTO. 0 when it cannot be read.

    `--cmday` exists so the child process can pass what its parent already read, rather
    than spending a second authenticated GET a minute after the first."""
    if not competition:
        return 0
    from fantabot.adapters.http import apileague
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.config import settings
    from fantabot.domain.tokens.crypto import TokenCipher

    cipher = TokenCipher(settings.fantabot_encryption_key)
    with database_manager.get_session() as session:
        body = apileague.teamLineup_read(league_id, competition, store=TokenStore(session, cipher))
    return int(body.get("teamLineupDto", {}).get("cmday", 0) or 0)


def register(app: typer.Typer) -> None:
    """Attach the lineup commands to the `lineup` group (called from `interface/app`)."""
    app.command("show")(_show)
    app.command("plan")(_plan)
    app.command("submit")(_submit)
    app.command("refresh")(_refresh)
