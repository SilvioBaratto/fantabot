"""Building a lineup and submitting it — every decision on that path, in one place.

`interface/lineup.py::submit` held eight of them inside a Typer body: the two locks, the
matchday refusal, the dry-run exit, the kickoff warning, the `LUP009` walk-down, the
confirming read-back, and the rule that the report is sourced from the read-back rather than
from the submit response. *"`interface/` holds no decision the app also needs"*, and this is
the path where being wrong submits a lineup.

**They lift together or not at all.** Each one is load-bearing against a different failure
and several are ordered against each other:

* the **matchday refusal fires before the arm check**, so a *dry run* refuses too. A dry run
  that printed a plan the armed run would have refused is a rehearsal of the wrong thing;
* the **kickoff warning warns and never blocks**. `mstr` is not confirmed to be the lineup
  deadline, so the platform stays the authority — a guess that blocked would lose a matchday
  to our own caution;
* the **`LUP009` walk-down** tries every fieldable module best-first, because
  `mantra_schemi.json`'s 4-1-4-1 was wrong and the platform said so live on 2026-09-02;
* the **read-back is the report's source**. The submit response is what we sent; the
  read-back is what the platform kept, and only the second is evidence.

Returns a frozen `SubmitOutcome` — every field a fact, not a message. The two surfaces word
it themselves, which is why nothing here is a sentence: the CLI prints Rich markup and the
app returns JSON, and a shared string would have to pick one.

The clock is injected. `interface/lineup.py::_now` is the seam and
`tests/domain/asta/test_asta_clock.py` counts it; a module that read the clock here would put
a second one where the first is already enforced to be alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fantabot.application.arming import Arming, decide_arming

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

    from fantabot.adapters.files.lineup_runs import LineupRun
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.domain.lineup.models import PlannedLineup

#: Why a submit did not happen. Names, not sentences — see the module docstring.
#:
#: `NO_MATCHDAY` is not a failure of ours: the lineup has no saved coordinates yet, which is
#: what the first matchday of a competition looks like before it opens.
NO_MATCHDAY = "no-matchday"
NOT_ARMED = "not-armed"
ALL_MODULES_REFUSED = "all-modules-refused"
#: The code a plan skipped by the positional guard is recorded under in `rejected`, beside
#: the platform's own `LUP0xx`: ours, raised before any POST, with the cell that failed.
GUARD = "GUARD"
#: The model this path plans with: the platform's own `indexCompare` value. Written into
#: every run record, so a line says which model chose it.
INDEXCOMPARE = "indexcompare"


@dataclass(frozen=True, slots=True)
class Rejection:
    """One plan the walk did not keep, and why: the platform's `LUP0xx`, or our guard."""

    plan: PlannedLineup
    code: str
    #: The platform's own sentence; `""` for a guard skip and when it sent none.
    message: str = ""


@dataclass(frozen=True, slots=True)
class SubmitOutcome:
    """What happened, as facts. Both surfaces render it; neither is named in it."""

    #: Every fieldable module, best first. Non-empty whenever a plan could be built at all.
    plans: tuple[PlannedLineup, ...]
    names: Mapping[int, str]
    competition: int
    arming: Arming
    #: `None` when the lineup was submitted. One of the three constants above otherwise.
    refused: str | None = None
    #: The `mstr` the platform reported when it looks past kickoff — a **warning**, carried
    #: alongside a successful submit rather than instead of one.
    past_deadline: str | None = None
    #: Each plan refused before one stuck, in walk order. Empty on a first-try success, and
    #: the record of a `LUP009` walk-down otherwise.
    rejections: tuple[Rejection, ...] = ()
    submitted: PlannedLineup | None = None
    #: The lineup **read back** after submitting — the evidence, not the request. Falls back
    #: to what the POST echoed when the confirming read could not be had; `unconfirmed` is
    #: then non-empty and says so.
    saved: Mapping[str, Any] = field(default_factory=dict)
    #: Why the confirming read failed, when it did. Empty on every other path — including a
    #: refusal, which is a *known* negative and needs no caveat. Non-empty means "this went
    #: to the platform and came back 200, and we could not then read it back to prove it":
    #: an unknown, not a negative.
    unconfirmed: str = ""
    #: The sentence behind a scheduled run's refusal (`domain.lineup.deadline`'s codes) — the
    #: start, the matchday, and why that meant not acting. Empty everywhere else.
    detail: str = ""

    @property
    def rejected(self) -> tuple[tuple[str, str], ...]:
        """`(module, code)` for each refusal — what both surfaces print."""
        return tuple((r.plan.module, r.code) for r in self.rejections)

    @property
    def plan(self) -> PlannedLineup | None:
        """The module this run is about: what was submitted, or what would have been.

        "Would have been" skips a plan the positional guard refuses, because the armed walk
        skips it too: a dry run that showed it would rehearse a lineup nobody sends.
        """
        if self.submitted is not None:
            return self.submitted
        return next((p for p in self.plans if not p.guard), self.plans[0] if self.plans else None)


def build_plans(
    store: TokenStore, league_id: int, competition: int
) -> tuple[list[PlannedLineup], dict[int, str], int]:
    """Roster, settings and coordinates, composed into ranked `PlannedLineup`s.

    Lifted out of `interface/lineup.py` as a **third** call site, not a second:
    `GET /lineup/plan` already reimplemented it by hand, which is how the app came to read
    the format from a different place than the command did.

    The format is detected, never configured — `sroles=1` is Classic, `sroles=2` is Mantra.
    This is the cron path, so a flag the operator must remember per lega is a footgun.
    """
    from fantabot.adapters.http import apileague
    from fantabot.application.lineup_planner import inputs_from_lineup, plan_lineups
    from fantabot.domain.lineup.competition import resolve_competition

    # `my_team` is the authoritative team id — the submit payload's `tid` (the lineup DTO is
    # empty first-of-season, and read there it submits tid=0) and, with no competition
    # given, the way to resolve one. Always needed, so never a wasted read.
    tid = int(apileague.my_team(league_id, store=store)["id"])
    comp = competition or resolve_competition(
        apileague.competitions(league_id, store=store), tid=tid
    )
    body = apileague.teamLineup_read(league_id, comp, store=store)
    lineup_conf = apileague.lineup_settings(league_id, store=store)
    rosters = apileague.roster_settings(league_id, store=store)
    fmt = "classic" if int(rosters.get("sroles", 2)) == 1 else "mantra"
    inputs, names = inputs_from_lineup(
        body.get("teamLineupDto", {}), body.get("lineUpInfo", []), lineup_conf, comp,
        tid=tid, fmt=fmt,
    )
    return plan_lineups(inputs), names, comp


def submit_lineup(
    store: TokenStore,
    *,
    league_id: int,
    competition: int = 0,
    arm: bool,
    now: Callable[[], datetime],
    auto_act: bool | None = None,
    scheduled: bool = False,
) -> SubmitOutcome:
    """Build the XI and submit it — behind two locks, a dry run by default.

    `arm` has no default here either: a caller that does not say does not act. `now` is the
    injected clock; `auto_act` is read from settings when not given, per call.

    `scheduled` is the unattended run. It turns the kickoff *warning* into a *refusal*, via
    `domain.lineup.deadline.scheduled_cutoff`: nobody reads a `launchd` job's warnings, and
    the operator asked for it never to reshuffle a lineup once its matchday has started. A
    human at the keyboard keeps the warning.
    """
    from fantabot.adapters.http import apileague
    from fantabot.domain.lineup import payload as payload_module
    from fantabot.domain.lineup.deadline import is_past_deadline, scheduled_cutoff
    from fantabot.domain.lineup.errors import LineupRejected
    from fantabot.domain.tokens.errors import TokenError

    plans, names, comp = build_plans(store, league_id, competition)
    arming = decide_arming(arm=arm, auto_act=auto_act)

    def outcome(**over: Any) -> SubmitOutcome:
        """The four facts every outcome carries, so a return site states only its own."""
        return SubmitOutcome(
            plans=tuple(plans), names=names, competition=comp, arming=arming, **over
        )

    # **Before the arm check**, so a dry run refuses too. A dry run that printed a plan the
    # armed run would have refused is a rehearsal of the wrong thing.
    if not plans or plans[0].mday == 0 or plans[0].cmday == 0:
        return outcome(refused=NO_MATCHDAY)

    # The scheduled cutoff, **before the arm check** for the matchday refusal's reason: a
    # scheduled dry run that printed a plan the armed one would refuse rehearses the wrong
    # thing. Read once and reused below, so the cutoff and the warning see one status.
    status: Mapping[str, Any] | None = None
    if scheduled:
        status = apileague.league_status(league_id, store=store)
        cut = scheduled_cutoff(
            mstr=str(status.get("mstr") or ""),
            status_mday=int(status.get("mday") or 0),
            plan_cmday=plans[0].cmday,
            now=now(),
        )
        if cut is not None:
            return outcome(refused=cut.code, detail=cut.reason)

    if not arming.armed:
        return outcome(refused=NOT_ARMED)

    # Warns, never blocks — for a manual run. `mstr` is not confirmed to be the lineup
    # deadline, so the platform stays the authority; a guess that blocked would lose a
    # matchday to caution. A scheduled run has already been refused above if it was late.
    if status is None:
        status = apileague.league_status(league_id, store=store)
    mstr = str(status.get("mstr", ""))
    past = mstr if mstr and is_past_deadline(mstr, now()) else None

    # Best-first, walking down on a refusal: `mantra_schemi.json`'s 4-1-4-1 was wrong and
    # the platform said so live on 2026-09-02.
    rejected: list[Rejection] = []
    for plan in plans:
        # The positional guard, **before** the POST. A `-1` cell is accepted by the platform
        # and scored as a malus, so its answer cannot be the check for one.
        if plan.guard:
            rejected.append(Rejection(plan, f"{GUARD} {plan.guard}"))
            continue
        try:
            sent = apileague.teamLineup_submit(
                league_id, payload_module.build(plan), store=store
            )
        except LineupRejected as exc:
            rejected.append(Rejection(plan, str(exc.code), exc.message))
            continue

        # Past this line the lineup **is on the platform**, and nothing after it may say
        # otherwise. The read-back is still the source of the report — the submit response
        # is what we sent, the read-back is what was kept, and only the second is evidence —
        # but it used to be an unguarded precondition of returning at all, so a timeout on
        # the confirming GET discarded the outcome and both surfaces reported a saved lineup
        # as unsubmitted. Missing evidence is an unknown, not a negative.
        #
        # `TokenError` is the whole family the network raises, and the net is deliberately
        # that wide: the 401 case is the worst of them, because it surfaced as "run
        # `fantabot auth login`" about a credential the POST had used successfully one call
        # earlier, sending the operator to re-authenticate over a lineup already saved.
        try:
            saved = apileague.teamLineup_read(league_id, comp, store=store).get(
                "teamLineupDto", {}
            )
            unconfirmed = ""
        except TokenError as exc:
            # `dict`, not `Mapping`: the latter is imported only under `TYPE_CHECKING`
            # here, and `teamLineup_submit` is typed to return a plain dict anyway. The
            # guard is for the fakes, which are free to return nothing at all.
            saved = sent.get("teamLineupDto", {}) if isinstance(sent, dict) else {}
            unconfirmed = str(exc)

        return outcome(
            past_deadline=past,
            rejections=tuple(rejected),
            submitted=plan,
            saved=saved,
            unconfirmed=unconfirmed,
        )

    return outcome(
        refused=ALL_MODULES_REFUSED, past_deadline=past, rejections=tuple(rejected)
    )


def run_record(
    outcome: SubmitOutcome, *, league_id: int, scheduled: bool, at: str
) -> LineupRun:
    """What one run did, as the history will show it. **Which rows are red is decided here.**

    Four states, and the line between *skipped* and *failed* is the whole point: a skip is a
    run doing its job by not acting; a failure is a run that should have submitted and did
    not. Every reader — the app's panel, anything after it — takes this word for it rather
    than re-deriving it from codes, which is how two screens come to disagree about the same
    Saturday.

    **A scheduled run that is not armed failed.** The job exists to submit; a shut lock at
    18:00 on a Friday is the silent miss the record is for. A manual dry run is a skip.
    """
    from dataclasses import replace

    from fantabot.adapters.files.lineup_runs import (
        FAILED,
        SKIPPED,
        SUBMITTED,
        UNCONFIRMED,
        LineupRejection,
        LineupRun,
    )
    from fantabot.application.arming import CLI_SENTENCES
    from fantabot.domain.lineup.deadline import MATCHDAY_MISMATCH, MATCHDAY_STARTED

    plan = outcome.plan

    def named(ids: Any) -> tuple[str, ...]:
        return tuple(outcome.names.get(pid, str(pid)) for pid in ids)

    base = LineupRun(
        at=at,
        league=league_id,
        scheduled=scheduled,
        status=FAILED,
        module=plan.module if plan else "",
        matchday=plan.mday if plan else None,
        serie_a_matchday=plan.cmday if plan else None,
        starters=named(plan.starts) if plan else (),
        bench=named(plan.bench) if plan else (),
        rejected=tuple(f"{module} ({code})" for module, code in outcome.rejected),
        starter_ids=tuple(plan.starts) if plan else (),
        bench_ids=tuple(plan.bench) if plan else (),
        competition=outcome.competition,
        tid=plan.tid if plan else None,
        model=INDEXCOMPARE,
        rejections=tuple(
            LineupRejection(
                module=r.plan.module,
                code=r.code,
                message=r.message,
                starter_ids=tuple(r.plan.starts),
                starters=named(r.plan.starts),
            )
            for r in outcome.rejections
        ),
    )

    if outcome.refused is None:
        if outcome.unconfirmed:
            return replace(base, status=UNCONFIRMED, detail=outcome.unconfirmed)
        past = f"submitted past {outcome.past_deadline}" if outcome.past_deadline else ""
        return replace(base, status=SUBMITTED, detail=past)

    code = outcome.refused
    if code in (MATCHDAY_STARTED, MATCHDAY_MISMATCH):
        return replace(base, status=SKIPPED, code=code, detail=outcome.detail)
    if code == NO_MATCHDAY:
        return replace(
            base,
            status=SKIPPED,
            code=code,
            detail="the platform has not opened this matchday's lineup yet — no coordinates",
        )
    if code == NOT_ARMED:
        return replace(
            base,
            status=FAILED if scheduled else SKIPPED,
            code=code,
            detail=f"not armed: {outcome.arming.because(CLI_SENTENCES)}",
        )
    if code == ALL_MODULES_REFUSED:
        refused = ", ".join(base.rejected) or "none recorded"
        return replace(
            base,
            status=FAILED,
            code=code,
            detail=f"the platform refused every fieldable module ({refused})",
        )
    # `NO_START_TIME` — and anything a later refusal adds before this learns its name. An
    # unknown refusal is a failure until someone decides otherwise: silence is the risk.
    return replace(base, status=FAILED, code=code, detail=outcome.detail)


def failed_run(code: str, detail: str, *, league_id: int, scheduled: bool, at: str) -> LineupRun:
    """A run that raised before it had an outcome — a dead token, an unreachable database.

    These are the failures most worth recording, because they stop everything else: no
    plan, no module, nothing to name but the reason.
    """
    from fantabot.adapters.files.lineup_runs import FAILED, LineupRun

    return LineupRun(
        at=at, league=league_id, scheduled=scheduled, status=FAILED, code=code, detail=detail,
        model=INDEXCOMPARE,
    )


__all__ = [
    "ALL_MODULES_REFUSED",
    "INDEXCOMPARE",
    "NOT_ARMED",
    "NO_MATCHDAY",
    "Rejection",
    "SubmitOutcome",
    "build_plans",
    "failed_run",
    "run_record",
    "submit_lineup",
]
