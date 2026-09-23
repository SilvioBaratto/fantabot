"""The one pure chain: candidates, Monte Carlo, the bench, the argmax. No I/O, no clock.

AD7. Live planning and the backtest both call `choose_plan`, so the gate grades the model
that ships — a backtest that replayed a second, simpler chain would grade something nobody
submits. Everything this needs arrives as `PlanInputs`; nothing here reads a database, a
setting or the calendar, and the only randomness comes from the caller's `Generator`.

**The budget is counted in work units, never in seconds** (AD6, coverage-7). A wall-clock
budget makes the plan a function of the machine it ran on: the same lega on the same
matchday would field one XI on a quiet laptop and another on a busy one, and neither the
shadow report nor the backtest could be recomputed. A work unit here is **one candidate
evaluated over one draw** — the thing whose cost is flat — and `Budget.work` is how many of
them one plan may spend. The table is indexed by what is known before any of it runs: how
many modules the lega allows and how big the roster is.

**The draw count falls out of the budget, it is not chosen.** With `C` candidates and `M`
of them getting a bench search worth `W` evaluations each, one plan costs
`(C + M*W) * n` units, so `n` is that division — clamped, and a clamp that bit is recorded
in `cuts` rather than swallowed, because "the model wanted 20,000 draws and got 500" is the
first thing to know when a plan looks noisy.

**A stopped run is a plan, not an exception.** `should_stop` is the seam the wall clock
reaches this module through (T31), and the caller's alternative to a half-searched plan is
no plan at all — the hourly job has to field something. What a stop costs is recorded.

**Ties break on discovery order.** `build_candidates` returns modules in the lega's own
order and each module's families in a fixed one, so the earliest candidate wins a tie —
never "whichever index `max` happened to see first", which is the same thing until numpy
changes its mind.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Literal

from fantabot.domain.lineup.bench import order_bench
from fantabot.domain.lineup.bench_mc import objective, order_bench_mc, work_units
from fantabot.domain.lineup.candidates import (
    DEFAULT_K,
    DEFAULT_LAMBDAS,
    DEFAULT_NODE_BUDGET,
    Candidate,
    build_candidates,
)
from fantabot.domain.lineup.dependence import Member
from fantabot.domain.lineup.errors import BenchIncomplete, NoFieldableModule
from fantabot.domain.lineup.opponent import goal_probabilities
from fantabot.domain.lineup.simulate import Evaluation, build_bank, evaluate
from fantabot.domain.lineup.substitution import SubMode, SubstitutionEngine

if TYPE_CHECKING:
    import numpy as np

    from fantabot.domain.lineup.dependence import Dependence
    from fantabot.domain.lineup.models import RosterPlayer
    from fantabot.domain.lineup.opponent import Opponent
    from fantabot.domain.lineup.scoring import ScoringRules

#: The fewest and most draws a plan may take. The floor keeps a big roster from being
#: planned on noise; the ceiling is where more draws stop moving the argmax and start
#: costing the hour.
MIN_DRAWS = 500
MAX_DRAWS = 20_000

#: Work units one plan may spend. Measured against the hourly job's own room: the whole
#: `lineup submit` must finish well inside launchd's 3600 s, and the projection is one step
#: of it. Stated here as a number rather than a duration for AD6's reason.
DEFAULT_WORK = 3_000_000


@dataclass(frozen=True, slots=True)
class Budget:
    """What one plan may spend, before it spends any of it."""

    #: Murty's k per family.
    k: int
    lambdas: tuple[float, ...]
    node_budget: int
    #: Candidates that get the Monte Carlo bench search. The rest keep `order_bench`'s.
    m: int
    #: Work units — candidate-draws — the whole plan may spend.
    work: int = DEFAULT_WORK


@dataclass(frozen=True, slots=True)
class PlanInputs:
    """Everything the chain needs, already read. Nothing here is fetched."""

    roster: tuple[RosterPlayer, ...]
    #: The lega's own `mods`, in its order — which is also the tie-break order.
    modules: tuple[str, ...]
    mu: Mapping[int, float]
    p: Mapping[int, float]
    sigma_tilde: Mapping[int, float]
    #: Each player's macro role and club, for the copula.
    macro: Mapping[int, str]
    club: Mapping[int, str | None]
    dependence: Dependence
    rules: ScoringRules
    bench_size: int
    #: The sub-aware value the default path ranks on — `order_bench`'s fallback, so the two
    #: benches are comparable and the model is never asked to beat a bench of its own making.
    value: Mapping[int, float]
    #: `ssnum`, if it is a cap at all (Open Question 1). `None` is uncapped.
    max_subs: int | None = None

    @property
    def roles(self) -> Mapping[int, frozenset[str]]:
        return {player.id: player.roles for player in self.roster}

    @property
    def sigma2(self) -> Mapping[int, float]:
        return {pid: s * s for pid, s in self.sigma_tilde.items()}


@dataclass(frozen=True, slots=True)
class ChosenPlan:
    """The XI the model would field, and everything the record needs to explain it."""

    module: str
    starts: tuple[int, ...]
    bench: tuple[int, ...]
    evaluation: Evaluation
    #: What the argmax ranked on. `fantapunti` means no opponent could be fitted.
    objective: Literal["points", "fantapunti"]
    #: Candidates built, and how many were evaluated before a stop.
    candidates: int
    evaluated: int
    draws: int
    #: Every budget clamp and every stop, named: `draws 20000 -> 500`, `stopped at 12/97`.
    cuts: tuple[str, ...] = ()
    #: True when `should_stop` ended the search. The plan is still a plan.
    stopped: bool = False
    #: Candidates that got the Monte Carlo bench search.
    benched: int = 0
    runners_up: tuple[tuple[str, tuple[int, ...], float], ...] = field(default=())


def seed_for(league_id: int, competition: int, matchday: int) -> int:
    """The plan's seed. From the coordinates, never from the clock (AD6).

    An hourly job re-plans the same matchday all week; seeding from the clock would make
    every run a different plan and every difference unexplainable. `sha256` rather than
    `hash()`, which is salted per process and would do exactly that.
    """
    digest = hashlib.sha256(f"{league_id}:{competition}:{matchday}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def budget_for(inputs: PlanInputs, *, work: int = DEFAULT_WORK) -> Budget:
    """The budget for these inputs. A pure function of the module count and the roster size.

    Both are known before a single candidate is built, which is what makes "the budget
    depends only on the inputs" a statement a test can make. A budget that depended on the
    candidates would depend on the tilt grid, which the budget itself chooses.
    """
    modules = len(inputs.modules)
    roster = len(inputs.roster)
    if modules <= 2:
        return Budget(k=DEFAULT_K, lambdas=DEFAULT_LAMBDAS, node_budget=DEFAULT_NODE_BUDGET,
                      m=8, work=work)
    if modules <= 6 and roster <= 30:
        return Budget(k=DEFAULT_K, lambdas=DEFAULT_LAMBDAS, node_budget=DEFAULT_NODE_BUDGET,
                      m=6, work=work)
    if roster <= 34:
        return Budget(k=6, lambdas=(-0.125, 0.0, 0.125), node_budget=DEFAULT_NODE_BUDGET,
                      m=4, work=work)
    return Budget(k=4, lambdas=(0.0, 0.125), node_budget=800, m=3, work=work)


def draws_for(budget: Budget, *, candidates: int, bench_units: int) -> tuple[int, str]:
    """How many draws the budget buys, and why it is that many.

    One plan costs `(candidates + m*bench_units)` evaluations, each over every draw. The
    clamp is reported rather than applied silently: a plan built on 500 draws when the model
    asked for 20,000 is the first thing to know when its argmax looks unstable.
    """
    per_draw = max(candidates + budget.m * bench_units, 1)
    wanted = budget.work // per_draw
    draws = max(MIN_DRAWS, min(MAX_DRAWS, wanted))
    if draws == wanted:
        return draws, ""
    return draws, f"draws {wanted} -> {draws}"


def choose_plan(
    inputs: PlanInputs,
    *,
    rng: np.random.Generator,
    budget: Budget | None = None,
    sub_mode: SubMode,
    opponent: Opponent | None,
    should_stop: Callable[[], bool] | None = None,
) -> ChosenPlan:
    """Candidates, Monte Carlo, the bench, the argmax. Pure.

    Raises `NoFieldableModule` when the roster fields none of the allowed modules — there is
    no plan to degrade to, and a caller that got an empty one would POST nothing while
    reporting success.
    """
    plan_budget = budget or budget_for(inputs)
    cuts: list[str] = []
    roles = inputs.roles
    shortlist = build_candidates(
        inputs.roster,
        inputs.modules,
        mu=inputs.mu,
        p=inputs.p,
        sigma2=inputs.sigma2,
        lambdas=plan_budget.lambdas,
        k=plan_budget.k,
        node_budget=plan_budget.node_budget,
    )
    if not shortlist:
        raise NoFieldableModule(inputs.modules)

    reserve_pool = max(len(inputs.roster) - 11, 0)
    bench_units = work_units(pool=reserve_pool, size=inputs.bench_size)
    draws, cut = draws_for(plan_budget, candidates=len(shortlist), bench_units=bench_units)
    if cut:
        cuts.append(cut)

    bank = build_bank(
        _members(inputs),
        tuple(player.id for player in inputs.roster),
        inputs.p,
        inputs.dependence,
        rng=rng,
        n=draws,
    )
    goals = (
        None
        if opponent is None
        else list(
            goal_probabilities(
                opponent, threshold=inputs.rules.threshold, steps=inputs.rules.steps
            )
        )
    )
    ranked_on: Literal["points", "fantapunti"] = "fantapunti" if goals is None else "points"

    scored: list[tuple[float, int, Candidate, tuple[int, ...], Evaluation]] = []
    stopped = False
    for position, candidate in enumerate(shortlist):
        if should_stop is not None and should_stop():
            stopped = True
            cuts.append(f"stopped at {position}/{len(shortlist)}")
            break
        try:
            bench = tuple(
                order_bench(
                    inputs.roster, candidate.starts, value=inputs.value, size=inputs.bench_size
                )
            )
        except BenchIncomplete:
            # A module this roster cannot bench is one the platform would refuse; it is
            # dropped rather than fatal, exactly as an unfieldable module is.
            continue
        result = evaluate(
            bank,
            engine=_engine(inputs, candidate, bench, sub_mode),
            rules=inputs.rules,
            opponent_goals=goals,
        )
        scored.append((objective(result), -position, candidate, bench, result))

    if not scored:
        raise NoFieldableModule(inputs.modules)

    scored.sort(reverse=True)
    benched = 0
    for index, (_value, negative, candidate, bench, _result) in enumerate(scored[: plan_budget.m]):
        if should_stop is not None and should_stop():
            stopped = True
            cuts.append(f"bench stopped at {index}/{min(plan_budget.m, len(scored))}")
            break
        reserves = tuple(
            player.id for player in inputs.roster if player.id not in set(candidate.starts)
        )
        engine_for = partial(_engine_for_bench, inputs, candidate, sub_mode)
        try:
            order = order_bench_mc(
                bank,
                engine_for=engine_for,
                reserves=reserves,
                roles=roles,
                size=inputs.bench_size,
                rules=inputs.rules,
                fallback=bench,
                opponent_goals=goals,
            )
        except BenchIncomplete:  # pragma: no cover - `order_bench` already refused these
            continue
        benched += 1
        scored[index] = (objective(order.evaluation), negative, candidate, order.bench,
                         order.evaluation)

    scored.sort(reverse=True)
    _best_value, _negative, best, bench, result = scored[0]
    return ChosenPlan(
        module=best.module,
        starts=tuple(best.starts),
        bench=tuple(bench),
        evaluation=result,
        objective=ranked_on,
        candidates=len(shortlist),
        evaluated=len(scored),
        draws=draws,
        cuts=tuple(cuts),
        stopped=stopped,
        benched=benched,
        runners_up=tuple(
            (c.module, tuple(c.starts), value) for value, _n, c, _b, _e in scored[1:4]
        ),
    )


def _members(inputs: PlanInputs) -> list[Member]:
    """One copula member per roster player, in roster order — the bank's column order."""
    return [
        Member(
            macro=inputs.macro.get(player.id, ""),
            club=inputs.club.get(player.id),
            mu=inputs.mu.get(player.id, 0.0),
            sigma_tilde=inputs.sigma_tilde.get(player.id, 0.0),
        )
        for player in inputs.roster
    ]


def _engine_for_bench(
    inputs: PlanInputs, candidate: Candidate, sub_mode: SubMode, bench: Sequence[int]
) -> SubstitutionEngine:
    """`_engine` with the bench last, which is the shape `order_bench_mc` calls."""
    return _engine(inputs, candidate, bench, sub_mode)


def _engine(
    inputs: PlanInputs, candidate: Candidate, bench: Sequence[int], sub_mode: SubMode
) -> SubstitutionEngine:
    """One engine per (candidate, bench): its memo is keyed on the absence pattern of those
    eleven, and an engine shared across candidates would answer for the wrong XI."""
    return SubstitutionEngine(
        module=candidate.module,
        starts=tuple(candidate.starts),
        bench=tuple(bench),
        roles=inputs.roles,
        mode=sub_mode,
        modules=inputs.modules,
        max_subs=inputs.max_subs,
    )
