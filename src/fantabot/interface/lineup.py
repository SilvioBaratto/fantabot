"""`fantabot lineup` — read, plan and submit the weekly formazione (Classic or Mantra).

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

from fantabot.domain.lineup.activity import Activity
from fantabot.interface.console import console

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.domain.lineup.models import PlannedLineup
    from fantabot.domain.lineup.predict import Prediction
    from fantabot.domain.lineup.rules import LeagueRules

_NO_PREDICT_HELP = "Rank on the platform's indexCompare only: no predictor, no captain/switch."


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

    lines = [
        f"module {plan.module}  (league matchday {plan.mday}, Serie A {plan.cmday})",
        "XI:    " + ", ".join(nm(p) for p in plan.starts),
        "bench: " + ", ".join(nm(p) for p in plan.bench),
    ]
    if plan.captains:
        worth = (
            f"  (captain modifier worth {plan.captain_bonus_ev:+.2f})"
            if plan.captain_bonus_ev is not None
            else ""
        )
        lines.append("captain: " + " / vice ".join(nm(p) for p in plan.captains) + worth)
    if plan.switch:
        into = f" (module becomes {plan.switch_module})" if plan.switch_module else ""
        lines.append(f"switch: {nm(plan.switch[0])} -> {nm(plan.switch[1])}{into}")
    if plan.defence_bonus_p is not None:
        cap = f" (subs cap {plan.max_subs})" if plan.max_subs is not None else ""
        worth = ""
        if plan.defence_avg_vote is not None and plan.defence_bonus_ev is not None:
            worth = (
                f" · avg vote ~{plan.defence_avg_vote:.2f} -> worth "
                f"+{plan.defence_bonus_ev:.2f}"
            )
        lines.append(
            f"defence modifier: P(4 defenders vote) {plan.defence_bonus_p:.0%}{cap}{worth}"
        )
    return lines


def format_predictions(
    plan: PlannedLineup, names: Mapping[int, str], predictions: Mapping[int, Prediction]
) -> list[str]:
    """One line per rosa player, XI first: play probability, fantavoto if he plays, score and
    the factors that moved it. Pure."""

    def line(tag: str, pid: int) -> str:
        name = names.get(pid, str(pid))
        p = predictions.get(pid)
        if p is None:
            return f"  {tag} {name:<18} (no prediction)"
        f = p.factors
        return (
            f"  {tag} {name:<18} play {p.p_play:4.0%}  fv {p.fv_if_plays:4.2f}"
            f"  score {p.score:5.2f}  [base {f['baseline']:.2f} opp {f['opponent']:.2f}"
            f" venue {f['venue']:.2f} ex {f['ex_team']:.2f} ic {f['index_compare']:.2f}]"
        )

    return (
        ["forecast (XI, then bench):"]
        + [line("XI", pid) for pid in plan.starts]
        + [line("  ", pid) for pid in plan.bench]
    )


def _load_predictions(
    session: Session, lineup_info: list[Mapping[str, Any]], cmday: int
) -> dict[int, Prediction]:
    """Run `domain/lineup/predict` over a Classic `lineUpInfo`, with the database's history.

    An empty history (a fresh database) is not an error: every factor it feeds goes neutral
    and the forecast rests on the row's own percent, fantamedia and indexCompare.
    """
    from fantabot.adapters.persistence.repositories.lineup_history import (
        LineupHistoryRepository,
    )
    from fantabot.domain.lineup.predict import (
        predict,
        previous_season,
        signals_from_row,
        team_rates,
    )

    rows = [signals_from_row(row) for row in lineup_info]
    ids = [r.pid for r in rows]
    repo = LineupHistoryRepository(session)
    season = repo.latest_season()
    if season is None:
        return predict(rows, cmday=cmday, prior_fantamedia={}, past_clubs={}, rates={})
    last = previous_season(season)
    return predict(
        rows,
        cmday=cmday,
        prior_fantamedia=repo.prior_fantamedia(ids, last),
        prior_vote=repo.prior_media_voto(ids, last),
        past_clubs=repo.past_clubs(ids, season),
        rates=team_rates(repo.fixtures(last)),
    )


def _build_plans(
    store: TokenStore,
    league_id: int,
    competition: int,
    *,
    session: Session | None = None,
    use_predictor: bool = True,
) -> tuple[list[PlannedLineup], dict[int, str], int, dict[int, Prediction]]:
    """Gather roster/settings/coords via `apileague` and compose the ranked `PlannedLineup`s.

    Roster, roles and value come from `teamLineup_read`'s `lineUpInfo`; the competition is
    auto-resolved when `competition` is 0. A Classic lega with a `session` is ranked by the
    predictor (and gets captain/switch); otherwise by `indexCompare`. Returns
    `(plans_best_first, id->name, comp_id, predictions)` — predictions empty when unused.
    """
    import dataclasses

    from fantabot.adapters.http import apileague
    from fantabot.application.lineup_planner import inputs_from_lineup, plan_lineups, with_rules
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
    # Format is detected, never configured: sroles=1 is Classic (P/D/C/A), sroles=2 is Mantra.
    # This is the cron path, so a flag the operator must remember per-lega would be a footgun.
    rosters = apileague.roster_settings(league_id, store=store)
    fmt = "classic" if int(rosters.get("sroles", 2)) == 1 else "mantra"
    lineup_info = body.get("lineUpInfo", []) or []
    inputs, names = inputs_from_lineup(
        body.get("teamLineupDto", {}), lineup_info, lineup_conf, comp,
        tid=tid, fmt=fmt,
    )
    if fmt == "classic":
        rules = _league_rules(store, league_id)
        if rules is not None:
            inputs = with_rules(inputs, rules)
    predictions: dict[int, Prediction] = {}
    if use_predictor and fmt == "classic" and session is not None:
        predictions = _load_predictions(session, lineup_info, inputs.cmday)
        inputs = dataclasses.replace(inputs, predictions=predictions)
    return plan_lineups(inputs), names, comp, predictions


def _league_rules(store: TokenStore, league_id: int) -> LeagueRules | None:
    """The lega's `settings/calculate`, parsed. A failed read or an unreadable table is said
    out loud and the plan carries on with the fallback: a missing bonus table must not stop
    the lineup from going in."""
    from fantabot.adapters.http import apileague
    from fantabot.domain.lineup.rules import rules_from_calculate
    from fantabot.domain.tokens.errors import ApiTimeout, ApiUnavailable

    try:
        rules = rules_from_calculate(apileague.calculate_settings(league_id, store=store))
    except (ApiUnavailable, ApiTimeout) as exc:
        console.print(
            f"[yellow]{league_id}: scoring rules unreadable ({exc}) — any back four is "
            "preferred, without weighing the bonus.[/yellow]"
        )
        return None
    for problem in rules.problems:
        console.print(f"[yellow]{league_id}: {problem}[/yellow]")
    return rules


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
    no_predict: bool = typer.Option(False, "--no-predict", help=_NO_PREDICT_HELP),
    explain: bool = typer.Option(False, "--explain", help="Print every player's forecast."),
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
            plans, names, _, predictions = _build_plans(
                store, league_id, competition, session=session, use_predictor=not no_predict
            )
    except (TokenError, LineupError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    except SQLAlchemyError as exc:
        console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
        raise typer.Exit(code=1) from exc

    for line in format_plan(plans[0], names):
        console.print(line)
    if explain and predictions:
        for line in format_predictions(plans[0], names, predictions):
            console.print(line, markup=False)


def _submit(
    league: int = typer.Option(0, "--league", help="Lega id. Defaults to FANTABOT_LEAGUE_ID."),
    competition: int = typer.Option(
        0, "--competition", help="Competition id. Auto-resolved when omitted."
    ),
    arm: bool = typer.Option(
        False, "--arm", help="Second, positive lock. Submit is OFF without it (and AUTO_ACT)."
    ),
    no_predict: bool = typer.Option(False, "--no-predict", help=_NO_PREDICT_HELP),
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
    from fantabot.domain.lineup.errors import LineupError
    from fantabot.domain.tokens.crypto import TokenCipher
    from fantabot.domain.tokens.errors import TokenError

    league_id = _resolve_league(league)
    auto_act = bool(settings.fantabot_auto_act)
    armed = auto_act and arm

    try:
        cipher = TokenCipher(settings.fantabot_encryption_key)
        with database_manager.get_session() as session:
            store = TokenStore(session, cipher)
            plans, names, comp, _ = _build_plans(
                store, league_id, competition, session=session, use_predictor=not no_predict
            )

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

            _warn_if_past_deadline(store, league_id)
            submitted = _post_first_accepted(store, league_id, plans)
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


def _past_deadline(store: TokenStore, league_id: int) -> str | None:
    """The lega's `mstr` when `now` is past it, else None."""
    from fantabot.adapters.http import apileague

    status = apileague.league_status(league_id, store=store)
    mstr = str(status.get("mstr", ""))
    return mstr if mstr and is_past_deadline(mstr, _now()) else None


def _warn_if_past_deadline(store: TokenStore, league_id: int) -> None:
    """Warn, never block: `mstr` is not confirmed to be the deadline; the platform decides."""
    mstr = _past_deadline(store, league_id)
    if mstr:
        console.print(
            f"[yellow]warning: past {mstr} (looks like kickoff) — submitting anyway; "
            "the platform will refuse if it is truly closed.[/yellow]"
        )


def _post_first_accepted(
    store: TokenStore, league_id: int, plans: list[PlannedLineup]
) -> PlannedLineup | None:
    """POST the best plan; if the platform refuses its module (LUP009 — a wrong schema), fall
    to the next-best rather than failing the run. None when every module is refused."""
    from fantabot.adapters.http import apileague
    from fantabot.domain.lineup import payload as payload_module
    from fantabot.domain.lineup.errors import LineupRejected

    for plan in plans:
        try:
            apileague.teamLineup_submit(league_id, payload_module.build(plan), store=store)
            return plan
        except LineupRejected as exc:
            console.print(
                f"[yellow]{plan.module} refused ({exc.code}) — trying the next module.[/yellow]"
            )
    return None


def _excluded_leagues(raw: str) -> set[int]:
    """`FANTABOT_LEAGUES_EXCLUDE` (comma-separated ids) as a set; junk entries are ignored."""
    return {int(part) for part in raw.replace(" ", "").split(",") if part.isdigit()}


def _classify(store: TokenStore, league_id: int) -> Activity:
    """One lega's `Activity`, from four reads. Token trouble is `relogin`, a 4xx is `gone`;
    any other failure is `error` and names itself, so one bad lega never hides the rest."""
    from fantabot.adapters.http import apileague
    from fantabot.domain.lineup.activity import competition_activity, lineup_activity
    from fantabot.domain.tokens.errors import (
        ApiUnavailable,
        TokenError,
        TokenExpired,
        TokenMissing,
        TokenRejected,
    )

    try:
        status = apileague.league_status(league_id, store=store)
        tid = int(apileague.my_team(league_id, store=store)["id"])
        pending = competition_activity(
            apileague.competitions(league_id, store=store),
            tid=tid,
            serie_a_matchday=int(status.get("mday") or 0),
        )
        if pending.state != "idle" or pending.competition is None:
            return pending
        body = apileague.teamLineup_read(league_id, pending.competition, store=store)
        return lineup_activity(pending, body.get("teamLineupDto") or {})
    except (TokenExpired, TokenMissing, TokenRejected) as exc:
        return Activity("relogin", str(exc))
    except ApiUnavailable as exc:
        if 400 <= exc.status < 500:
            return Activity("gone", str(exc))
        return Activity("error", str(exc))
    except TokenError as exc:
        return Activity("error", str(exc))


_STATE_STYLE = {"open": "green", "idle": "cyan", "relogin": "yellow", "error": "red"}


def _stored_leagues(store: TokenStore) -> list[tuple[int, str]]:
    return [(row.league_id, row.league_name or "?") for row in store.status()]


def _leagues() -> None:
    """Every stored lega, classified: which are active, which are not, and why. Read-only."""
    from rich.table import Table
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.config import settings
    from fantabot.domain.tokens.crypto import TokenCipher

    excluded = _excluded_leagues(settings.fantabot_leagues_exclude)
    table = Table("lega", "name", "state", "why", "competition")
    try:
        cipher = TokenCipher(settings.fantabot_encryption_key)
        with database_manager.get_session() as session:
            store = TokenStore(session, cipher)
            for league_id, name in _stored_leagues(store):
                a = _classify(store, league_id)
                state = f"{a.state} (excluded)" if league_id in excluded else a.state
                style = _STATE_STYLE.get(a.state, "dim")
                table.add_row(
                    str(league_id), name, f"[{style}]{state}[/{style}]", a.reason,
                    str(a.competition or ""),
                )
    except SQLAlchemyError as exc:
        console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(table)


def _rules() -> None:
    """Every open or idle lega's scoring rules that shape the lineup: the defence modifier's
    range and bonus table, whether the keeper counts, the substitution cap, the captain
    modifier. Read-only."""
    from rich.table import Table
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.config import settings
    from fantabot.domain.lineup.rules import describe_bands
    from fantabot.domain.tokens.crypto import TokenCipher

    table = Table("lega", "name", "defence modifier", "bonus by average vote", "subs", "captain")
    try:
        cipher = TokenCipher(settings.fantabot_encryption_key)
        with database_manager.get_session() as session:
            store = TokenStore(session, cipher)
            for league_id, name in _stored_leagues(store):
                if not _classify(store, league_id).active:
                    continue
                rules = _league_rules(store, league_id)
                if rules is None:
                    table.add_row(str(league_id), name, "[yellow]unreadable[/yellow]", "", "", "")
                    continue
                d = rules.defence
                defence = (
                    f"{d.lower:.2f}-{d.upper:.2f}, "
                    f"{'keeper + ' if d.includes_keeper else ''}3 best D, needs 4 D"
                    if d is not None
                    else "not played"
                )
                bands = ", ".join(describe_bands(d)) if d is not None else ""
                subs = (
                    f"max {rules.subs.max_subs} (type {rules.subs.kind})"
                    if rules.subs is not None
                    else "?"
                )
                captain = (
                    ", ".join(describe_bands(rules.captain)) if rules.captain is not None else "-"
                )
                table.add_row(str(league_id), name, defence, bands, subs, captain)
    except SQLAlchemyError as exc:
        console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(table)


def _submit_all(
    arm: bool = typer.Option(
        False, "--arm", help="Second, positive lock. Submit is OFF without it (and AUTO_ACT)."
    ),
    no_predict: bool = typer.Option(False, "--no-predict", help=_NO_PREDICT_HELP),
) -> None:
    """Field every stored lega with an open matchday — **behind two locks, dry run by default.**

    Each lega is classified first (see `lineup leagues`); only `open` ones not listed in
    `FANTABOT_LEAGUES_EXCLUDE` are planned and, when armed, submitted. Failure is per lega: one
    that fails names itself and the rest still run. Exit 1 if any open lega failed.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot.adapters.http import apileague
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.config import settings
    from fantabot.domain.lineup.errors import LineupError
    from fantabot.domain.tokens.crypto import TokenCipher
    from fantabot.domain.tokens.errors import TokenError

    auto_act = bool(settings.fantabot_auto_act)
    armed = auto_act and arm
    excluded = _excluded_leagues(settings.fantabot_leagues_exclude)
    failed: list[int] = []
    try:
        cipher = TokenCipher(settings.fantabot_encryption_key)
        with database_manager.get_session() as session:
            store = TokenStore(session, cipher)
            for league_id, name in _stored_leagues(store):
                a = _classify(store, league_id)
                head = f"[bold]{league_id} {name}[/bold] — {a.state}: {a.reason}"
                if league_id in excluded:
                    console.print(f"{head} [dim](excluded, skipped)[/dim]")
                    continue
                console.print(head)
                if a.state == "relogin":
                    failed.append(league_id)
                    continue
                if a.state != "open" or a.competition is None:
                    continue
                try:
                    plans, names, comp, _ = _build_plans(
                        store, league_id, a.competition,
                        session=session, use_predictor=not no_predict,
                    )
                    for line in format_plan(plans[0], names):
                        console.print(f"  {line}")
                    if not armed:
                        continue
                    # Unattended, so stricter than `submit`: past the first kickoff the saved
                    # lineup stands, rather than a resubmit racing matches already under way.
                    mstr = _past_deadline(store, league_id)
                    if mstr:
                        console.print(f"  [yellow]past {mstr} — not resubmitting.[/yellow]")
                        continue
                    submitted = _post_first_accepted(store, league_id, plans)
                    if submitted is None:
                        console.print("  [red]every fieldable module was refused.[/red]")
                        failed.append(league_id)
                        continue
                    saved = apileague.teamLineup_read(league_id, comp, store=store).get(
                        "teamLineupDto", {}
                    )
                    console.print(
                        f"  [green]submitted {submitted.module} — saved "
                        f"{len(saved.get('starts', []))} starters, capt {saved.get('capt')}, "
                        f"switch {saved.get('swtcA')}->{saved.get('swtcB')}, "
                        f"ldate {saved.get('ldate', '?')}[/green]"
                    )
                except (TokenError, LineupError) as exc:
                    console.print(f"  [red]{exc}[/red]")
                    failed.append(league_id)
    except SQLAlchemyError as exc:
        console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
        raise typer.Exit(code=1) from exc

    if not armed:
        why = "--arm not given" if auto_act else "FANTABOT_AUTO_ACT is false"
        console.print(f"[yellow]dry run ({why}) — nothing submitted.[/yellow]")
    if failed:
        console.print(f"[red]failed: {', '.join(str(i) for i in failed)}[/red]")
        raise typer.Exit(code=1)


def register(app: typer.Typer) -> None:
    """Attach the lineup commands to the `lineup` group (called from `interface/app`)."""
    app.command("show")(_show)
    app.command("plan")(_plan)
    app.command("submit")(_submit)
    app.command("leagues")(_leagues)
    app.command("submit-all")(_submit_all)
    app.command("rules")(_rules)
