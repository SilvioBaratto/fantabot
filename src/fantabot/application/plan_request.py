"""What a plan is built from, said once — and the one function that builds it.

**The defect this closes.** `asta optimize` and `GET /asta/plan` each assembled their own
inputs, and they had drifted apart in ten of them (`SPEC.md` §11.1). Two are a pair and
the rest follow from it: the endpoint passed `sentiment=None`, which is not "no opinion"
but the sentiment model's **ablation control** — plain `fvm`, the arm of the experiment
that on the 2026-08-28 data chases a player with a metatarsal fracture to 62 credits — and
`tilt_k=1.0`, four times the CLI's, inert only because there was nothing to tilt. The page
was showing an operator the control arm as advice.

This is the same failure `application/asta_planner.py` was written for one layer down:
three commands each grew a copy of the value model and the one that spends real credits
fell behind the one that advises. The answer there was one door to the *world*; this is
one door to the *plan*.

**Why the request is a frozen value and not a pile of keyword arguments.** A parameter
that a caller can forget is a parameter some caller has forgotten: five of
`read_plan_inputs`' six callers ignore its corpus shape, and `asta calibrate` still passes
a shape to one corpus and none to the other. A dataclass makes "what a plan is built from"
a thing that can be constructed, compared, and pinned — which is exactly what 1.5's
golden-dict test does with it.

**No clock.** `as_of` is a field, not a `date.today()` call, for the reason
`domain/asta/sentiment.py` states: it decays confidence on a 7-day half-life, and a module
that reads the clock has tests that are a coin flip. `tests/domain/asta/test_asta_clock.py`
sweeps `application/plan_*.py` by glob precisely so this file is covered on the day it
exists.

**No typer.** `--format` validation and `--sentiment-run` parsing stay in the Typer body,
because both raise `typer.BadParameter` and `FORBIDDEN_TO_APPLICATION` includes typer. What
crosses this boundary is a parsed `date | None`, never a string the interface has not
looked at. The one error this layer raises for the interface to translate is
`NoSentimentRows`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Protocol

from fantabot.application.plan_inputs import PlanInputs
from fantabot.domain.asta.state import RosterRules
from fantabot.domain.classic.state import ClassicRosterRules

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from fantabot.domain.asta.state import OptimizationResult
    from fantabot.domain.shared.values import SentimentRow


class NoSentimentRows(RuntimeError):
    """Sentiment was asked for and the feed had nothing to give.

    Refused rather than passed through, and that is a decision with a measurement behind
    it: valuing on zero rows is numerically identical to `--no-sentiment` and means
    something entirely different. A run that silently plans on plain `fvm` because a date
    was mistyped is the failure this exists to prevent.
    """


class EmptyPool(RuntimeError):
    """The listone has no rows for this format and season.

    Fail closed. An empty pool is a wrong `--format` or an un-scraped season, not a plan
    over nobody — and the optimizer would happily return an empty roster for it.
    """


class SentimentSource(Protocol):
    """The one method a plan needs from the feed. Narrow so a fake is three lines."""

    def all_latest(self, *, data_run: date | None = ...) -> Mapping[str, SentimentRow]: ...


#: Our league: eight teams, five hundred credits. The corpus is restricted to this shape so
#: the observed clearing prices need no budget normalization.
#:
#: **A default, not a constant** — `docs/fantalab/00 §13` is explicit that a league rule
#: written into the code is a bug, and the next asta is the riparazione in January or a
#: friend's league. It is a default *here* rather than at each call site because five of
#: `read_plan_inputs`' six callers never passed one at all. 1.6 makes every caller state it.
DEFAULT_NUM_TEAMS = 8
DEFAULT_NUM_CREDITS = 500


@dataclass(frozen=True, slots=True)
class PlanRequest:
    """Everything a roster plan is built from. Frozen, hashable-by-value, comparable.

    Comparable is the point: 1.5 pins the request the CLI builds against the one the
    endpoint builds, so the two cannot drift again without a test saying which field moved.
    """

    season: str
    #: `"mantra"` or `"classic"`. Validated in the Typer body, which raises
    #: `typer.BadParameter`; by the time it is here it is one of the two.
    listone: str
    #: The calendar, as a value. See the module docstring.
    as_of: date
    budget: float
    rules: RosterRules | ClassicRosterRules
    owned: frozenset[str] = frozenset()
    lam: float = 0.0
    n_fallbacks: int = 0
    tilt_k: float = 0.25
    #: Whether to consult the feed at all. `False` is the ablation control and is a
    #: deliberate act; `None` rows arrived at by accident is the defect above.
    sentiment: bool = True
    #: A parsed run date, or `None` for "the latest". Never a string: parsing raises
    #: `typer.BadParameter` and that cannot happen in this layer.
    sentiment_run: date | None = None
    #: The fantacalcio ids FantaLab's listone can call, or `None` for "do not filter".
    #: **Never `frozenset()` for "unknown"** — `read_plan_inputs` reads an empty collection
    #: as a real, total exclusion, which would empty the pool. 41 of 570 players are absent
    #: from the listone (Lukaku, 2531, at fvm 41) and can never be called.
    callable_ids: frozenset[str] | None = None
    num_teams: int = DEFAULT_NUM_TEAMS
    num_credits: int = DEFAULT_NUM_CREDITS


@dataclass(frozen=True, slots=True)
class PlannedRoster:
    """The plan, and the world it was computed against.

    The world travels with the result because every renderer needs it — `format_roster`
    annotates drifted players from `world.sentiment`, and the page's price column is
    `world.prices`. Returning only the roster is what made each caller re-read the world
    to display it, which is how they came to read it differently.
    """

    request: PlanRequest
    result: OptimizationResult
    world: PlanInputs
    #: The readings the value model was built from, or `None` under the ablation.
    sentiment: Mapping[str, SentimentRow] | None = field(default=None)


def callable_ids(
    *,
    warn: Callable[[str], None],
    fetch: Callable[[], Mapping[str, int]] | None = None,
) -> frozenset[str] | None:
    """The fantacalcio ids FantaLab's listone can actually call, or `None` if unknown.

    **`None` means "do not filter", and it is deliberately not `frozenset()`.**
    `read_plan_inputs` reads an empty collection as a real, total exclusion — right for the
    bidder, where an unresolved bridge means every lot is unknown — and it would empty the
    planner's pool. A planner that refuses to plan because a CDN was unreachable is worse
    than one that plans over a slightly wider pool and says so: this is what an operator
    reads the night before, and its output is the paper fallback for the evening.

    Measured 2026-09-01: 41 of 570 pool players are absent from the listone. Lukaku (2531)
    is one — priced at fvm 41 in `quotazioni` so the optimiser sees him, absent from the
    listone so the room can never call him. He took a slot in the printed 30-man plan,
    which therefore had 29 fillable places and one that could not be filled.

    Lifted out of `interface/asta.py` because the app needs the same narrowing and the
    same degradation: *"`interface/` holds no decision the app also needs"*. `fetch` is the
    injection seam, so both degradations are covered without a socket.
    """
    from fantabot.adapters.http.fantalab import listone

    reader = fetch or listone.fetch
    try:
        bridge = reader()
    except Exception as exc:
        warn(f"listone unreachable ({type(exc).__name__}); planning over the whole pool")
        return None
    if not bridge:
        warn("listone empty; planning over the whole pool")
        return None
    return frozenset(str(fid) for fid in bridge.values())


def resolve_sentiment(
    source: SentimentSource, *, enabled: bool, run: date | None
) -> Mapping[str, SentimentRow] | None:
    """The readings, or `None` when the caller asked for the ablation.

    An empty result is refused rather than passed through — see `NoSentimentRows`. The
    interface's `sentiment_rows` is a two-line translation of that error into
    `typer.BadParameter`, so the message an operator sees is written once, here.
    """
    if not enabled:
        return None
    rows = source.all_latest(data_run=run)
    if not rows:
        where = f"for data_run {run.isoformat()}" if run else "in the database"
        raise NoSentimentRows(
            f"sentiment is on but there are no rows {where}. "
            "Run `fantabot news fetch --write`, or pass --no-sentiment."
        )
    return rows


def build_plan(session: Session, request: PlanRequest) -> PlannedRoster:
    """Read the world this request describes and solve it. The one door to a plan.

    Everything here was three copies a moment ago: `asta optimize`'s body, the endpoint's,
    and the shape `asta bid` re-derives. `InfeasibleRoster` is deliberately **not** caught
    — it says the rosa cannot be built at all, which is a different screen from every
    other failure, and swallowing it here is what `except Exception -> found=False` does
    on the page today.
    """
    from fantabot.adapters.persistence.news_sentiment import NewsSentimentSource
    from fantabot.application.asta_planner import read_plan_inputs
    from fantabot.domain.asta.optimizer import optimize_roster
    from fantabot.domain.asta.state import AstaState

    rows = resolve_sentiment(
        NewsSentimentSource(session), enabled=request.sentiment, run=request.sentiment_run
    )
    world = read_plan_inputs(
        session,
        season=request.season,
        sentiment=rows,
        as_of=request.as_of,
        tilt_k=request.tilt_k,
        callable_ids=request.callable_ids,
        listone=request.listone,
        num_teams=request.num_teams,
        num_credits=request.num_credits,
    )
    if not world.pool:
        raise EmptyPool(
            f"no {request.listone} players for season {request.season} — nothing to plan."
        )

    result = optimize_roster(
        # `owned` is a tuple on the state and a frozenset on the request: the request
        # is compared for equality (1.5 pins it), and a tuple would make two identical
        # plans differ by the order somebody typed the ids in.
        AstaState(owned=tuple(sorted(request.owned)), total_budget=request.budget),
        world.pool,
        value=world.value,
        prices=world.prices,
        teams=world.teams,
        legality=world.legality,
        rules=request.rules,
        lam=request.lam,
        n_fallbacks=request.n_fallbacks,
    )
    return PlannedRoster(request=request, result=result, world=world, sentiment=rows)


__all__ = [
    "DEFAULT_NUM_CREDITS",
    "DEFAULT_NUM_TEAMS",
    "EmptyPool",
    "NoSentimentRows",
    "PlanRequest",
    "PlannedRoster",
    "SentimentSource",
    "build_plan",
    "callable_ids",
    "resolve_sentiment",
]
