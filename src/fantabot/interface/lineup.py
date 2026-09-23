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

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import typer
from rich.markup import escape

from fantabot.application.containment import contained
from fantabot.domain.lineup.deadline import is_past_deadline as _is_past_deadline
from fantabot.interface.console import console

if TYPE_CHECKING:
    from fantabot.adapters.tokens.store import TokenStore

    # `Projector` is `Callable[[int], Projected]` and both live in `application/
    # lineup_submit`, which is on the default path — so naming it here costs no import edge
    # into the projection branch. That is the whole reason the seam is typed that way.
    from fantabot.application.lineup_projection import ProjectionOutcome, ProjectionReport
    from fantabot.application.lineup_submit import Projected, Projector
    from fantabot.domain.lineup.freshness import Freshness
    from fantabot.domain.lineup.models import PlannedLineup


#: Re-exported: it moved to `domain/lineup/deadline.py` in 3.2 so `application/` could use
#: it, and this name is what `interface/lineup.py`'s own tests and its one call site have
#: always imported. A lift is verified by those tests passing *unchanged*.
is_past_deadline = _is_past_deadline


def _now() -> datetime:
    """The one clock read for the lineup feature — isolated so tests can reason about it."""
    return datetime.now()


def wall_stop(seconds: float, *, now: Callable[[], datetime] = _now) -> Callable[[], bool]:
    """`should_stop` from a wall clock — the **only** way a clock reaches the pure chain.

    A hard abort, never a budget: the plan's size is decided in work units (AD6) precisely
    so the same lega on the same matchday fields the same XI on a fast machine and a slow
    one. This is the backstop for the case that reasoning cannot cover — a machine so loaded
    that the counted work takes longer than the hour the job runs in — and what it produces
    is a **fallback**: `choose_plan` returns the best plan it had and records the stop.

    `now` is injected rather than read here so the test does not have to wait out a deadline.
    """
    deadline = now() + timedelta(seconds=seconds)
    return lambda: now() >= deadline


def format_chosen(outcome: ProjectionOutcome) -> list[str]:
    """The evaluated plan's own numbers, or the named reason there are none.

    E[pts] is the objective and is printed first; P(W/D/L) is what it is made of; E[fp] and
    its spread are reported beside it, never instead of it. Without an opponent there is no
    E[pts] to print, and the line says so rather than showing a zero that reads as a loss.
    """
    mode = f"sub mode {outcome.sub_mode}" + (
        " (assumed — FANTABOT_LINEUP_SUB_MODE is unset)" if outcome.sub_mode_assumed else ""
    )
    if outcome.chosen is None:
        return [f"[yellow]no evaluated plan: {outcome.fallback}[/yellow]", f"[dim]{mode}[/dim]"]
    plan = outcome.chosen
    e = plan.evaluation
    head = (
        f"module {plan.module} · E[fp] {e.fantapunti:.2f} ± {e.fantapunti_sd:.2f} · "
        f"E[goals] {e.goals:.2f}"
    )
    if e.points is None or e.win is None or e.drawn is None or e.loss is None:
        result = f"no opponent ({outcome.opponent}) — ranked on E[fp]"
    else:
        result = (
            f"E[pts] {e.points:.3f} · P(W/D/L) "
            f"{e.win:.3f}/{e.drawn:.3f}/{e.loss:.3f} · opponent {outcome.opponent}"
        )
    spent = (
        f"{plan.candidates} candidate(s), {plan.evaluated} evaluated, "
        f"{plan.benched} bench search(es), {plan.draws} draws"
    )
    lines = [head, result, f"[dim]{spent} · {mode}[/dim]"]
    if plan.cuts:
        lines.append(f"[yellow]budget: {', '.join(plan.cuts)}[/yellow]")
    return lines




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
#: Re-exported from `application/lineup_submit`, not restated: the app asks the same
#: question and two spellings of one answer is how the format detection came to differ
#: between `lineup plan` and `GET /lineup/plan`.
from fantabot.application.lineup_submit import (  # noqa: E402
    INDEXCOMPARE as INDEXCOMPARE,
)
from fantabot.application.lineup_submit import (  # noqa: E402
    PROJECTION as PROJECTION,
)
from fantabot.application.lineup_submit import (  # noqa: E402
    parse_model as resolve_model,
)

#: The hard abort for a read-only plan. Well inside launchd's hourly `StartInterval`, and
#: comfortably above what the counted budget takes on the operator's own machine — 250,000
#: work units at a measured 0.34 ms each is about 85 s. It exists for the machine that is
#: not this one, and it was 60 s until 2026-09-23, when it fired on the real lega at
#: "stopped at 4/177" — a wall tighter than the budget is a wall that decides the plan.
PLAN_WALL_SECONDS = 300.0

#: The hard abort for the projection **inside a submit**, and **looser** than the read-only
#: preview's on purpose. An abort in a preview costs a table nobody was waiting for; an
#: abort here costs the model's lineup and falls back to `indexCompare`, so a wall tight
#: enough to fire regularly would make the projection unreachable rather than safe — and
#: the measured plan takes 192 s on the operator's own machine. The hour has the room:
#: 600 here plus `REFRESH_WALL_SECONDS` is 2,400 of launchd's 3,600.
SUBMIT_WALL_SECONDS = 600.0

#: The hard wall on the refresh child, sized against what it bounds rather than against a
#: round number. The sources run **serially**, and the worst case of each is:
#:
#: * **voti** — 8 giornate, each up to 3 attempts at a 30 s timeout with a 2 s + 4 s
#:   backoff, plus the politeness delay between them: about 13 minutes;
#: * **lega** — 8 authenticated reads, one of them megabytes: a couple of minutes;
#: * **news** — off until T40, and the one that would blow any wall: ~30 players at a
#:   180 s per-query timeout and concurrency 4 is 22 minutes on its own.
#:
#: Forty minutes covers the two that run today with room to spare, and launchd's interval
#: has room for it: `SUBMIT_WALL_SECONDS` + this is 3,000 of 3,600, leaving ten minutes for
#: the submit's own reads and the next tick to start clean. ⚠ **Re-measure this when news is
#: switched on** — that source alone can approach the whole of it.
REFRESH_WALL_SECONDS = 2400.0

#: The bootstrap's seed. A constant, so a gate run is a gate run: the interval must not
#: move between two readings of the same replay, or "the CI is above zero" is a coin.
_GATE_SEED = 20260923


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
        "",
        "--model",
        help=f"Value model: {INDEXCOMPARE} or {PROJECTION}. Empty reads FANTABOT_LINEUP_MODEL.",
    ),
) -> None:
    """Build and print the best legal formation for the current matchday. **No submit.**"""
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.application.lineup_submit import build_plans
    from fantabot.config import LINEUP_MODEL_VAR, live_setting, settings
    from fantabot.domain.lineup.errors import LineupError
    from fantabot.domain.tokens.crypto import TokenCipher
    from fantabot.domain.tokens.errors import TokenError

    league_id = _resolve_league(league)
    if model and model not in (INDEXCOMPARE, PROJECTION):
        console.print(f"[red]unknown model {model!r}: {INDEXCOMPARE} or {PROJECTION}[/red]")
        raise typer.Exit(code=1)
    chosen_model = model or resolve_model(live_setting(LINEUP_MODEL_VAR))
    if chosen_model == PROJECTION:
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
    from fantabot.config import LINEUP_SUB_MODE_VAR, live_setting, settings
    from fantabot.domain.lineup.errors import LineupError
    from fantabot.domain.lineup.substitution import parse_sub_mode
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
                sub_mode=parse_sub_mode(live_setting(LINEUP_SUB_MODE_VAR)),
                should_stop=wall_stop(PLAN_WALL_SECONDS),
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
    # `plans[0]` *is* the evaluated XI when there is one: the application puts it at the
    # head so the submit path walks it first, and the interface only prints what it is given.
    for line in format_plan(outcome.plans[0], report.names):
        console.print(line)
    for line in format_chosen(outcome):
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
    shadow: bool = typer.Option(
        False,
        "--shadow",
        help="Also compute the projection and record it beside the lineup that was sent.",
    ),
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="After the record is written, bring the history up to date in a bounded child.",
    ),
) -> None:
    """Build the formation and submit it — **behind two locks, dry run by default.**

    Prints the plan always. Submits only when `FANTABOT_AUTO_ACT=true` **and** `--arm`; then
    warns if the matchday looks started and confirms by reading the lineup back.

    `--refresh` is **off unless passed** too, runs **after** the record is written, and runs
    as a bounded child process: it imports the agent SDK and the whole persistence stack,
    talks to a live site, and can hang in a way no `try` catches. A refresh that wedged
    in-process would hold the job past its next hourly tick, and the tick after that is a
    matchday. Nothing it does changes the record or the exit code.

    `--shadow` is **off unless passed** (A19(1)) and `fantabot-app schedule run` passes it.
    Under `indexcompare` it costs the run nothing that matters: the POST happens first and
    the projection after, so a chain that hangs or raises cannot delay, change or lose the
    lineup that was going out anyway. Under `FANTABOT_LINEUP_MODEL=projection` it is not a
    shadow at all — it is the projector the submit *plans* with, and without it that surface
    refuses rather than sending an `indexCompare` XI under a record that says `projection`.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot.adapters.files.lineup_runs import LineupRun, append_run
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.application.arming import CLI_SENTENCES
    from fantabot.application.lineup_submit import (
        ALL_MODULES_REFUSED,
        MODEL_NOT_ON_SURFACE,
        NO_MATCHDAY,
        NOT_ARMED,
        PROJECTION,
        chosen_model,
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
            store = TokenStore(session, cipher)
            # Read **once**, here, and passed down: `submit_lineup` would otherwise read it
            # again and the projector is built from it, so an edit to `.env` between the two
            # would build a shadow projector for a submit that plans with the projection.
            model = chosen_model()
            outcome = submit_lineup(
                store,
                league_id=league_id,
                competition=competition,
                arm=arm,
                now=_now,
                scheduled=scheduled,
                model=model,
                projector=(
                    _projector(store, league_id, for_submit=model == PROJECTION)
                    if shadow
                    else None
                ),
            )
    except (TokenError, LineupError) as exc:
        # Recorded before anything else: these stop the run before a plan exists, which is
        # exactly the Saturday the record has to speak for.
        record(failed_run(type(exc).__name__, str(exc), league_id=league_id,
                          scheduled=scheduled, at=at, model=chosen_model()))
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    except SQLAlchemyError as exc:
        # The type and a fixed sentence, never `str(exc)`: a driver's message can carry the
        # connection string, and this line ends up in a file the app renders.
        record(failed_run(
            "database-unreachable",
            f"the bundled Postgres is not reachable ({type(exc).__name__}) — the token lives "
            "there. Start it with `fantabot-app db start`.",
            league_id=league_id, scheduled=scheduled, at=at, model=chosen_model(),
        ))
        console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
        raise typer.Exit(code=1) from exc

    record(run_record(outcome, league_id=league_id, scheduled=scheduled, at=at))

    # **After the record, and never before it.** The submit's own work is done and written
    # down; everything from here is optional and may not be able to change either.
    if refresh and outcome.plan is not None:
        note = _refresh_child(league_id, cmday=outcome.plan.cmday, competition=outcome.competition)
        if note:
            console.print(f"[dim]refresh: {escape(note)}[/dim]")

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

    if outcome.refused == MODEL_NOT_ON_SURFACE:
        console.print(
            "[red]FANTABOT_LINEUP_MODEL asks for the projection and this run has no "
            "projector.[/red] Pass [bold]--shadow[/bold], or unset the variable. "
            "Refusing rather than sending an indexCompare lineup under a record that says "
            "projection."
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

    # Every refusal has a branch above — `application.lineup_submit.REFUSALS` is the list
    # and `test_lineup_cli.py` holds this surface to it. `MODEL_NOT_ON_SURFACE` reached
    # this line once (2026-09-23) and the scheduled job died on the assertion.
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


def _refresh_child(league_id: int, *, cmday: int, competition: int) -> str:
    """Spawn `lineup refresh` when this matchday still owes something. Never raises.

    **The due check is in-process and reads only the marker** (`domain/lineup/refresh.due`).
    It deliberately is not `run_refresh`: that one reads the lega calendar and the voti
    table to decide *what* to fetch, and doing it here would pay the read in the submit's
    own hour to find out there was nothing to do. The marker answers "does this matchday owe
    anything at all" from one small file, which is the only question worth asking twice.

    It also costs **no second `league_status` read**: `cmday` comes from the plan the submit
    already built, which `test_the_status_is_read_once` is there to keep true.
    """
    import sys

    from fantabot.adapters.files.refresh_marker import FileMarkerStore
    from fantabot.adapters.process import run_grouped
    from fantabot.domain.lineup.refresh import due

    owed, failure = contained(lambda: due(FileMarkerStore().read(), cmday=cmday))
    if owed is None:
        return f"marker unreadable ({failure})"
    if not owed:
        return ""

    command = [
        sys.executable, "-m", "fantabot", "lineup", "refresh",
        "--league", str(league_id), "--cmday", str(cmday),
    ]
    if competition:
        command += ["--competition", str(competition)]
    code, note = run_grouped(command, timeout=REFRESH_WALL_SECONDS)
    owes = ", ".join(owed)
    if note:
        return f"{owes}: {note}"
    return f"{owes}: exit {code}"


def _malus_starters(store: object, *, league_id: int, run: object, matchday: int,
                    giornata: int, sides: tuple[int, int] | None) -> tuple[int, ...]:
    """The starters the platform flagged with a positional malus (SPEC A18).

    Read from the match detail, which is the only place the flag lives: `match_grain` has no
    column for it and a score a point short is not evidence — two men can be a point apart
    for a dozen reasons. Contained, because a matchday whose detail cannot be read is a
    matchday whose malus check is unknown rather than clean, and the report says which.
    """
    from fantabot.adapters.http import apileague
    from fantabot.domain.lineup.scoring import malus_starters

    competition = int(getattr(run, "competition", 0) or 0)
    tid = int(getattr(run, "tid", 0) or 0)
    if not competition or not tid or sides is None:
        return ()
    home, away = sides

    def read() -> tuple[int, ...]:
        body = apileague.match_detail(
            league_id, competition, mday=matchday, cmday=giornata,
            home=home, away=away, store=store,  # type: ignore[arg-type]
        )
        for side in ("home", "away"):
            block = body.get(side) or {}
            if int(block.get("tid") or 0) == tid:
                return malus_starters(block)
        return ()

    flagged, _failure = contained(read)
    return flagged or ()


def _voti_refreshed(cmday: int) -> bool:
    """Whether the marker records a voti success for this matchday (A20).

    Read through the same `FileMarkerStore` the refresh writes, so "fresh" means the thing
    the refresh actually did rather than a flag somebody set. Unreadable is **not fresh**:
    fail closed, because a stale history that reads as fresh is a projection submitted on
    voti that can still change.
    """
    from fantabot.adapters.files.refresh_marker import FileMarkerStore

    ok, _failure = contained(lambda: FileMarkerStore().read().succeeded("voti", cmday))
    return bool(ok)


def _projector(store: TokenStore, league_id: int, *, for_submit: bool) -> Projector:
    """The projection, bound to this lega — imported **inside the returned callable**.

    The one edge from the hourly submit into the projection branch, taken only when
    `--shadow` was passed. `tests/domain/lineup/test_lineup_imports.py` declares it; without
    the flag this function is never called and neither library is loaded, which is what
    guard 1b runs a fresh interpreter to prove.

    ⚠ **The import is deferred into the call, not done here.** This function is evaluated as
    an *argument* to `submit_lineup`, so an `ImportError` raised at this level lands outside
    the containment boundary and outside the `except` clauses the command has — a scheduled
    run with a broken numpy died with no record at all, which is the one thing A19(3) exists
    to prevent. Inside the callable it is `contained` like every other failure and becomes a
    named fallback to the lineup that was going out anyway. Measured 2026-09-23.
    """
    deadline = wall_stop(SUBMIT_WALL_SECONDS)
    as_of = _now().date()

    def build(competition: int) -> Projected:
        from fantabot.application.lineup_projection import projector_for
        from fantabot.config import LINEUP_SUB_MODE_VAR, live_setting
        from fantabot.domain.lineup.substitution import parse_sub_mode

        return projector_for(
            store,
            league_id=league_id,
            as_of=as_of,
            sub_mode=parse_sub_mode(live_setting(LINEUP_SUB_MODE_VAR)),
            should_stop=deadline,
            for_submit=for_submit,
            voti_refreshed=_voti_refreshed,
        )(competition)

    return build


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


def _backtest(
    league: int = typer.Option(0, "--league", help="Lega id. Defaults to FANTABOT_LEAGUE_ID."),
    season: list[str] = typer.Option(
        [], "--season", help="A graded season. Repeatable. Gate 1 needs both of them."
    ),
    sweep_season: str = typer.Option(
        "2023/24", "--sweep-season", help="The season H is picked on, and never graded."
    ),
    sub_mode: str = typer.Option(
        "", "--sub-mode", help="basic | easy | master. Required: the three field different XIs."
    ),
    bench: int = typer.Option(
        0, "--bench", help="Bench to replay at. 0 reads the gate's own (3), not the lega's 12."
    ),
    rooms: int = typer.Option(0, "--rooms", help="Replay only the first N rooms. 0 = all."),
    last: int = typer.Option(38, "--last-giornata", help="Stop after this giornata."),
    draws: int = typer.Option(2000, "--draws", help="Bootstrap resamples."),
) -> None:
    """Replay the corpus under both arms and print Gate 1's verdict. Read-only, and long.

    `--sub-mode` has **no default** and the command refuses without it. The three modes field
    different XIs — measured on the platform's own rounds 1-3 (T20) — so a gate run under an
    assumed mode grades a game the operator has not confirmed is the one being played.

    `--rooms` and `--last-giornata` exist for the smoke run. The full corpus is 148 rosters
    over 33 giornate in each of two seasons, plus the sweep, and every one of them is a
    plan: this is an overnight command, and the report says what it actually covered.

    `--bench` defaults to the **gate's** three and not the lega's twelve, which is the
    operator's call of 2026-09-23 on a measurement: at twelve the sweep season can field no
    roster at all, because a 2026/27 rosa has few players with a 2023/24 `quotazioni` row.
    The report prints the bench and names the limitation it buys.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories.lineup_history import (
        BacktestCorpusRepository,
        LineupHistoryRepository,
    )
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.application.lineup_backtest import GATE_BENCH, ReplaySettings, run_gate
    from fantabot.application.lineup_projection import substitution_cap
    from fantabot.config import settings
    from fantabot.domain.lineup.backtest_corpus import admit
    from fantabot.domain.lineup.errors import LineupError
    from fantabot.domain.lineup.substitution import SUB_MODES, parse_sub_mode
    from fantabot.domain.tokens.crypto import TokenCipher
    from fantabot.domain.tokens.errors import TokenError

    league_id = _resolve_league(league)
    mode = parse_sub_mode(sub_mode)
    if mode is None:
        console.print(
            f"[red]--sub-mode is required: one of {', '.join(sorted(SUB_MODES))}.[/red]\n"
            "The three field different XIs, so a gate run under an assumed mode grades a "
            "game nobody has confirmed is the one being played."
        )
        raise typer.Exit(code=2)
    graded = list(season) or ["2024/25", "2025/26"]
    if sweep_season in graded:
        console.print(
            f"[red]{sweep_season} is both swept and graded.[/red] H would be tuned on the "
            "data it is then graded on, which is a model choosing its own exam."
        )
        raise typer.Exit(code=2)

    from fantabot.adapters.http import apileague
    from fantabot.domain.lineup.scoring import ScoringRules

    try:
        cipher = TokenCipher(settings.fantabot_encryption_key)
        with database_manager.get_session() as session:
            store = TokenStore(session, cipher)
            calculate = apileague.calculate_settings(league_id, store=store)
            lega = apileague.lineup_settings(league_id, store=store)
        rules = ScoringRules.from_settings(calculate["bnMls"], calculate["step"])
        with database_manager.get_session() as session:
            corpus_rows = BacktestCorpusRepository(session).corpus_rows()
            corpus = admit(*corpus_rows)
            report = run_gate(
                LineupHistoryRepository(session),
                corpus,
                ReplaySettings(
                    rules=rules,
                    sub_mode=mode,
                    modules=tuple(str(m) for m in lega.get("mods") or ()),
                    # The gate's bench, not the lega's. At the lega's twelve the sweep
                    # season fields no roster at all — see `GATE_BENCH` for the measurement.
                    bench_size=bench or GATE_BENCH,
                    max_subs=substitution_cap(calculate),
                    rooms=rooms,
                    last=last,
                ),
                seasons=graded,
                sweep_season=sweep_season,
                seed=_GATE_SEED,
                reporter=console,
                draws=draws,
            )
    except TokenError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    except (LineupError, ValueError) as exc:
        console.print(f"[red]the gate could not run: {exc}[/red]")
        raise typer.Exit(code=1) from None
    except SQLAlchemyError as exc:
        console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
        raise typer.Exit(code=1) from exc

    for line in report.lines():
        console.print(escape(line))
    if not report.passes:
        raise typer.Exit(code=1)


def _shadow_report(
    league: int = typer.Option(0, "--league", help="Lega id. Defaults to FANTABOT_LEAGUE_ID."),
    season: str = typer.Option("2026/27", "--season", help="Which stagione the votes are in."),
    sub_mode: str = typer.Option(
        "", "--sub-mode", help="basic | easy | master. Defaults to FANTABOT_LINEUP_SUB_MODE."
    ),
) -> None:
    """Grade what was fielded, and what the shadow would have fielded. Read-only.

    Phase 7's evidence. For each matchday it takes the **last** gradable record — an hourly
    job re-submits while the round is open, so the newest is the XI that was in play — and
    recomputes it from the **ids**: lega-scored votes plus the auto-sub engine. Three lines
    per matchday: our number against the platform's own, the shadow's number, and the malus
    check (A18).

    A recompute that disagrees with the platform by more than 0.01 is a **finding**, not a
    rounding: the model is being graded by a scorer that does not match the one paying out,
    and nothing downstream is worth reading until it is explained.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot.adapters.files.lineup_runs import read_runs
    from fantabot.adapters.http import apileague
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories.lineup_history import (
        LineupHistoryRepository,
        ShadowRepository,
    )
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.application.lineup_projection import substitution_cap
    from fantabot.application.lineup_shadow import Ungradable, grade_run, report, votes_at
    from fantabot.config import LINEUP_SUB_MODE_VAR, lineup_runs_path, live_setting, settings
    from fantabot.domain.asta.roles import normalize_roles
    from fantabot.domain.lineup.scoring import ScoringRules
    from fantabot.domain.lineup.substitution import SUB_MODES, parse_sub_mode
    from fantabot.domain.tokens.crypto import TokenCipher
    from fantabot.domain.tokens.errors import TokenError

    league_id = _resolve_league(league)
    mode = parse_sub_mode(sub_mode or live_setting(LINEUP_SUB_MODE_VAR))
    if mode is None:
        console.print(
            f"[red]no substitution mode: pass --sub-mode ({', '.join(sorted(SUB_MODES))}) "
            "or set FANTABOT_LINEUP_SUB_MODE.[/red]\nThe three field different XIs, so a "
            "grade under an assumed mode grades a game nobody has confirmed is being played."
        )
        raise typer.Exit(code=2)

    runs, _skipped = read_runs(lineup_runs_path())
    if not runs:
        console.print(f"[yellow]no run records at {lineup_runs_path()}[/yellow]")
        raise typer.Exit(code=0)

    try:
        cipher = TokenCipher(settings.fantabot_encryption_key)
        with database_manager.get_session() as session:
            store = TokenStore(session, cipher)
            calculate = apileague.calculate_settings(league_id, store=store)
            lega = apileague.lineup_settings(league_id, store=store)
            rules = ScoringRules.from_settings(calculate["bnMls"], calculate["step"])
            modules = tuple(str(m) for m in lega.get("mods") or ())
            cap = substitution_cap(calculate)
            shadow_repo = ShadowRepository(session)
            history = LineupHistoryRepository(session)
            roles = {
                pid: normalize_roles(codes)
                for pid, codes in history.roles(season).items()
            }

            def grade(run: object) -> object:
                giornata = getattr(run, "serie_a_matchday", None)
                if not giornata:
                    return Ungradable(getattr(run, "at", "?"), "no Serie A matchday recorded")
                rows = shadow_repo.appearances_at(season, int(giornata))
                if not rows:
                    return Ungradable(
                        getattr(run, "at", "?"),
                        f"no votes recorded for {season} giornata {giornata}",
                    )
                tid = getattr(run, "tid", None)
                matchday = int(getattr(run, "matchday", 0) or 0)
                found = (
                    None
                    if not tid
                    else shadow_repo.fixture_for(league_id, matchday=matchday, tid=int(tid))
                )
                return grade_run(
                    run,  # type: ignore[arg-type]
                    votes=votes_at(rows, giornata=int(giornata), season=season, rules=rules),
                    roles=roles,
                    rules=rules,
                    sub_mode=mode,
                    modules=modules,
                    platform=None if found is None else found[2],
                    # A18's check, read off the platform's own `m` flag. It was defaulted to
                    # empty until 2026-09-23, which made the whole check inert — the report
                    # said "no malus" about a flag it had never looked at.
                    malus_starters=_malus_starters(
                        store, league_id=league_id, run=run, matchday=matchday,
                        giornata=int(giornata),
                        sides=None if found is None else (found[0], found[1]),
                    ),
                    max_subs=cap,
                )

            outcome = report(
                runs, league_id=league_id, sub_mode=mode,
                grade=grade,  # type: ignore[arg-type]
            )
    except TokenError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    except SQLAlchemyError as exc:
        console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
        raise typer.Exit(code=1) from exc

    for line in outcome.lines():
        console.print(escape(line))
    if outcome.mismatches or outcome.maluses:
        raise typer.Exit(code=1)


def register(app: typer.Typer) -> None:
    """Attach the lineup commands to the `lineup` group (called from `interface/app`)."""
    app.command("show")(_show)
    app.command("plan")(_plan)
    app.command("submit")(_submit)
    app.command("refresh")(_refresh)
    app.command("backtest")(_backtest)
    app.command("shadow-report")(_shadow_report)
