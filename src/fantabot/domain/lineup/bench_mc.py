"""The bench, ordered by what it is actually worth. Pure, seeded, and never worse.

`bench.py` ranks the reserves by the same value as the starting XI — best sub first — which
is the right answer when every vacancy is interchangeable and the wrong one when it is not:
the auto-sub engine tests *combinations* in bench order, so the second-best outfielder is
worth nothing if he cannot cover the slot that actually empties. A better bench is not a
better-ranked bench, it is one that covers.

So the order is built **greedily, position by position**, each position taking the reserve
that maximises the evaluator's own objective given the positions already fixed. That is the
marginal decision the engine itself makes — a player at position k is tried before one at
k+1 — and it costs `size x |pool|` evaluations rather than the `12!` a search would.

**Never worse than `bench.py`, by construction rather than by hope.** A greedy is a
heuristic and this one has no optimality argument, so its answer is evaluated against
`order_bench`'s under the same draws and the better of the two is returned. One extra
evaluation buys a guarantee, and it is what makes the claim testable rather than asserted.

**Slot 0 is the reserve keeper.** The platform settles the keeper before any combination is
searched (`rules/sistema-mantra.md`, §Goalkeeper substitution), and he spends one of the
lega's substitutions. A bench whose first man is not a keeper is not a different strategy,
it is a lineup the platform refuses — so the keeper is chosen first, among keepers only.

Ties break on the fallback value and then on the player id: two reserves the draws cannot
tell apart must still order the same way on every run, or a shadow report cannot be
recomputed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from fantabot.domain.lineup.bench import GK_ROLE
from fantabot.domain.lineup.errors import BenchIncomplete
from fantabot.domain.lineup.simulate import Evaluation, evaluate

if TYPE_CHECKING:
    from fantabot.domain.lineup.scoring import ScoringRules
    from fantabot.domain.lineup.simulate import DrawBank
    from fantabot.domain.lineup.substitution import SubstitutionEngine

#: Builds the engine for one candidate bench. The caller binds the module, the XI, the
#: roles, the mode and the substitution cap; only the bench varies here.
EngineFor = Callable[[Sequence[int]], "SubstitutionEngine"]


def objective(evaluation: Evaluation) -> float:
    """What a bench is ranked on: league points when there is an opponent, fantapunti when
    there is not. Never both — a weighted blend would be a third league nobody plays in."""
    return evaluation.fantapunti if evaluation.points is None else evaluation.points


@dataclass(frozen=True, slots=True)
class BenchOrder:
    """The bench that was chosen, what it scored, and where it came from."""

    bench: tuple[int, ...]
    evaluation: Evaluation
    #: `greedy` when the search beat `bench.py`'s order, `value` when it did not.
    source: Literal["greedy", "value"]
    #: Evaluations spent. The work unit T30's budget counts.
    work: int


def order_bench_mc(
    bank: DrawBank,
    *,
    engine_for: EngineFor,
    reserves: Sequence[int],
    roles: Mapping[int, frozenset[str]],
    size: int,
    rules: ScoringRules,
    fallback: Sequence[int],
    opponent_goals: Sequence[float] | None = None,
    gk_role: str = GK_ROLE,
) -> BenchOrder:
    """The best bench of `size` from `reserves`, never worse than `fallback`.

    `fallback` is `bench.order_bench`'s answer for this XI — passed in rather than rebuilt,
    because it is a function of the value map the caller already has and a second derivation
    here is a second thing that can disagree with the default path.
    """
    if size < 1:
        raise ValueError(f"a bench is at least the keeper; got {size}")
    if len(fallback) != size:
        raise ValueError(f"the fallback bench is {len(fallback)}, not {size}")
    pool = list(reserves)
    keepers = [pid for pid in pool if gk_role in roles.get(pid, frozenset())]
    if not keepers:
        raise BenchIncomplete("no reserve goalkeeper for bench slot 0")
    if len(pool) < size:
        raise BenchIncomplete(f"only {len(pool)} reserves available, need {size}")

    rank = {pid: i for i, pid in enumerate(fallback)}
    work = 0

    def score(bench: Sequence[int]) -> tuple[float, Evaluation]:
        nonlocal work
        work += 1
        result = evaluate(
            bank, engine=engine_for(bench), rules=rules, opponent_goals=opponent_goals
        )
        return objective(result), result

    def best_of(chosen: list[int], among: Sequence[int]) -> int:
        """The reserve that pays most at the next position. Ties: `fallback`'s order, then id."""
        scored = [
            (score([*chosen, pid])[0], -rank.get(pid, len(rank)), -pid, pid) for pid in among
        ]
        return max(scored)[3]

    chosen = [best_of([], keepers)]
    while len(chosen) < size:
        remaining = [pid for pid in pool if pid not in chosen]
        chosen.append(best_of(chosen, remaining))

    greedy_value, greedy = score(chosen)
    baseline_value, baseline = score(fallback)
    if greedy_value >= baseline_value:
        return BenchOrder(tuple(chosen), greedy, "greedy", work)
    return BenchOrder(tuple(fallback), baseline, "value", work)


def work_units(*, pool: int, size: int) -> int:
    """An **upper bound** on the evaluations `order_bench_mc` will spend.

    A bound and not a count: the keeper's position iterates over the reserve keepers alone,
    and a roster usually has one or two rather than twelve. Erring high is the safe
    direction — T30 uses this to refuse a bench search it cannot afford, and a budget that
    under-counted would commit to work it then has to abandon half done.
    """
    positions = sum(max(pool - k, 0) for k in range(size))
    return positions + 2
