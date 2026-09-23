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

**And it is bounded twice, because `size x |pool|` was still the whole budget.** Measured on
the real lega (2026-09-23): a 12-man bench over 19 reserves is 164 evaluations per candidate,
which at four candidates was 79% of the plan's work and left the final round on its floor of
500 draws. Two bounds fix that without touching the guarantee:

* **`depth`** — only the first few positions are searched. With `ssnum` at 5 and the keeper
  spending one, at most four outfielders ever come on, so position 8 is a lottery ticket
  priced like a starter. The rest keep `bench.py`'s order.
* **`width`** — each position tries only the best few reserves by that same order. A reserve
  the value model ranks 15th is not the man who covers a flank better than the 3rd.

Both are the budget's to set, and both are reported as the work they cost.

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
    depth: int | None = None,
    width: int | None = None,
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
    searched = size if depth is None else min(depth, size)
    considered = len(pool) if width is None else max(width, 1)
    work = 0

    def score(bench: Sequence[int]) -> tuple[float, Evaluation]:
        nonlocal work
        work += 1
        result = evaluate(
            bank, engine=engine_for(bench), rules=rules, opponent_goals=opponent_goals
        )
        return objective(result), result

    def by_rank(among: Sequence[int]) -> list[int]:
        """`fallback`'s order, then id — the order `width` truncates."""
        return sorted(among, key=lambda pid: (rank.get(pid, len(rank)), pid))

    def best_of(chosen: list[int], among: Sequence[int]) -> int:
        """The reserve that pays most at the next position. Ties: `fallback`'s order, then id."""
        scored = [
            (score([*chosen, pid])[0], -rank.get(pid, len(rank)), -pid, pid)
            for pid in by_rank(among)[:considered]
        ]
        return max(scored)[3]

    chosen = [best_of([], keepers)] if searched else [by_rank(keepers)[0]]
    while len(chosen) < size:
        remaining = [pid for pid in pool if pid not in chosen]
        if len(chosen) < searched:
            chosen.append(best_of(chosen, remaining))
        else:
            # Past the searched positions, `bench.py`'s order stands: these men come on only
            # when four have already been replaced, which `ssnum` usually forbids outright.
            chosen.append(by_rank(remaining)[0])

    greedy_value, greedy = score(chosen)
    baseline_value, baseline = score(fallback)
    if greedy_value >= baseline_value:
        return BenchOrder(tuple(chosen), greedy, "greedy", work)
    return BenchOrder(tuple(fallback), baseline, "value", work)


def work_units(
    *, pool: int, size: int, depth: int | None = None, width: int | None = None
) -> int:
    """An **upper bound** on the evaluations `order_bench_mc` will spend.

    A bound and not a count: the keeper's position iterates over the reserve keepers alone,
    and a roster usually has one or two rather than twelve. Erring high is the safe
    direction — T30 uses this to decide what the *final* round can still afford, and a
    budget that under-counted would spend the whole wall clock on the bench and rank its
    survivors on the draw floor. Which is exactly what it did before `depth` and `width`.
    """
    searched = size if depth is None else min(depth, size)
    per_position = pool if width is None else min(width, pool)
    positions = sum(min(per_position, max(pool - k, 0)) for k in range(searched))
    return positions + 2
