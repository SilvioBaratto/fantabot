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
from fantabot.domain.lineup.simulate import DrawBank, Evaluation, build_bank, evaluate
from fantabot.domain.lineup.substitution import SubMode, SubstitutionEngine

if TYPE_CHECKING:
    import numpy as np

    from fantabot.domain.lineup.dependence import Dependence
    from fantabot.domain.lineup.models import RosterPlayer
    from fantabot.domain.lineup.opponent import Opponent
    from fantabot.domain.lineup.scoring import ScoringRules

#: The fewest and most draws the **final** round may take. The floor keeps a big roster from
#: being ranked on noise; the ceiling is where more draws stop moving the argmax.
MIN_DRAWS = 500
MAX_DRAWS = 20_000

#: Draws the screening round takes, for every candidate. Small on purpose: the screen only
#: has to rank, and a difference that 400 paired draws cannot see is one the final round is
#: unlikely to care about either. It is the **prefix** of the same bank, so the two rounds
#: compare the same weeks.
SCREEN_DRAWS = 400

#: Work units one plan may spend. A unit is one candidate evaluated over one draw, and its
#: price is **measured, not declared**: on the real lega — 30-man roster, eleven modules,
#: 177 candidates — 160,000 units took **192 s** end to end on 2026-09-23, so a unit is
#: about 1.2 ms. That is three minutes for a read-only preview and a rounding error against
#: the hourly job's 3600 s, which is the run that matters.
#:
#: ⚠ The price is **not** flat across the rounds, which is why it is stated as a measured
#: average and not as a rate. The screen pays once per candidate and warms the matcher's
#: memo as it goes; the bench search pays again on benches nobody has matched before, and
#: is the dearest round per unit. A budget derived from the screen's own rate would
#: under-price the bench by a factor of two.
DEFAULT_WORK = 160_000


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
    #: Bench positions the greedy actually searches, and reserves it tries at each. With
    #: `ssnum` at 5 and the keeper spending one, at most four outfielders ever come on.
    bench_depth: int = 6
    bench_width: int = 8


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


def draws_for(
    budget: Budget, *, candidates: int, bench_units: int, screen: int = SCREEN_DRAWS
) -> tuple[int, str]:
    """How many draws the **final** round buys, and why it is that many.

    A plan is three rounds and only the last is expensive per candidate:

    * the **screen** evaluates every candidate at `screen` draws — `candidates * screen`;
    * the **bench search** runs on the survivors, at the same `screen` draws, because
      ordering a bench is a comparison between benches and not a measurement of one —
      `m * bench_units * screen`;
    * the **final** re-evaluates those `m` at as many draws as is left over.

    Evaluating all 177 candidates of a real eleven-module board at 20,000 draws was never
    affordable in Python — measured, 0.34 ms a unit — and a budget that pretended otherwise
    spent its whole wall clock on the screen and reported "stopped at 4/177". The clamp is
    reported rather than applied silently.
    """
    spent = candidates * screen + budget.m * bench_units * screen
    left = budget.work - spent
    wanted = left // max(budget.m, 1)
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
    bench_units = work_units(
        pool=reserve_pool,
        size=inputs.bench_size,
        depth=plan_budget.bench_depth,
        width=plan_budget.bench_width,
    )
    draws, cut = draws_for(plan_budget, candidates=len(shortlist), bench_units=bench_units)
    if cut:
        cuts.append(cut)

    bank = build_bank(
        _members(inputs),
        tuple(player.id for player in inputs.roster),
        inputs.p,
        inputs.dependence,
        rng=rng,
        n=max(draws, SCREEN_DRAWS),
    )
    screen = bank.head(SCREEN_DRAWS)
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

    def run(
        board: DrawBank, candidate: Candidate, bench: Sequence[int]
    ) -> Evaluation:
        return evaluate(
            board,
            engine=_engine(inputs, candidate, bench, sub_mode),
            rules=inputs.rules,
            opponent_goals=goals,
        )

    # -- the screen: every candidate, on the prefix ------------------------------------
    scored: list[tuple[float, int, Candidate, tuple[int, ...], Evaluation]] = []
    stopped = False
    for position, candidate in enumerate(shortlist):
        if should_stop is not None and should_stop():
            stopped = True
            cuts.append(f"screen stopped at {position}/{len(shortlist)}")
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
        result = run(screen, candidate, bench)
        scored.append((objective(result), -position, candidate, bench, result))

    if not scored:
        raise NoFieldableModule(inputs.modules)
    scored.sort(reverse=True)
    survivors = scored[: plan_budget.m]

    # -- the bench search, on the same prefix ------------------------------------------
    benched = 0
    for index, (_value, negative, candidate, bench, _result) in enumerate(list(survivors)):
        if should_stop is not None and should_stop():
            stopped = True
            cuts.append(f"bench stopped at {index}/{len(survivors)}")
            break
        reserves = tuple(
            player.id for player in inputs.roster if player.id not in set(candidate.starts)
        )
        engine_for = partial(_engine_for_bench, inputs, candidate, sub_mode)
        try:
            order = order_bench_mc(
                screen,
                engine_for=engine_for,
                reserves=reserves,
                roles=roles,
                size=inputs.bench_size,
                rules=inputs.rules,
                fallback=bench,
                opponent_goals=goals,
                depth=plan_budget.bench_depth,
                width=plan_budget.bench_width,
            )
        except BenchIncomplete:  # pragma: no cover - `order_bench` already refused these
            continue
        benched += 1
        survivors[index] = (
            objective(order.evaluation), negative, candidate, order.bench, order.evaluation
        )

    # -- the final: the survivors, on the whole bank -----------------------------------
    final: list[tuple[float, int, Candidate, tuple[int, ...], Evaluation]] = []
    for index, (_screened, negative, candidate, bench, _result) in enumerate(survivors):
        if should_stop is not None and should_stop():
            stopped = True
            cuts.append(f"final stopped at {index}/{len(survivors)}")
            break
        full = run(bank, candidate, bench)
        final.append((objective(full), negative, candidate, bench, full))
    if not final:
        # Stopped before the first full evaluation: the screen's own ranking is the answer,
        # and a screened plan is still a plan. What it is not is a *measured* one, and the
        # cut says which.
        final = survivors
    final.sort(reverse=True)

    _best_value, _negative, best, bench, result = final[0]
    return ChosenPlan(
        module=best.module,
        starts=tuple(best.starts),
        bench=tuple(bench),
        evaluation=result,
        objective=ranked_on,
        candidates=len(shortlist),
        evaluated=len(scored),
        draws=bank.n,
        cuts=tuple(cuts),
        stopped=stopped,
        benched=benched,
        runners_up=tuple(
            (c.module, tuple(c.starts), value) for value, _n, c, _b, _e in final[1:4]
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
