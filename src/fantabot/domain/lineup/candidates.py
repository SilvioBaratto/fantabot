"""The outer loop's shortlist: which elevens the evaluator is allowed to choose between. Pure.

`build.py` answers "the best XI for this module" under one number per player. The projection
asks a harder question — a player who gets no vote is replaced by the auto-sub, and how much
that costs depends on **which slot** he left empty — so the surrogate here weighs (slot,
player) pairs, and one matcher run is no longer enough to see the field.

**The surrogate (SPEC A17(3)).** `w[s,i] = p_i*mu_i + (1 - p_i)*v_s`, where `v_s` is the best
`p*mu` among the reserves whose roles cover slot s. It is `value.sub_aware` made per-slot: the
single "next man up" there shifts every module's total alike and so cancels out of the
ranking, while `v_s` does not — it says a risky man is worth more where the bench covers him
and less where it does not, which is the whole reason to bench anyone.

**Two solves, and why it cannot be one.** `v_s` is read off the *reserves*, and who the
reserves are is what the solve decides. So the first solve runs on `p*mu`, which needs no
`v_s`; its complement is the reserve pool; `v_s` follows; and the second solve runs on the
full matrix. The fixed point is not chased further: a third solve would move `v_s` again and
the sequence has no reason to converge, while the evaluator — not the surrogate — is what
actually scores these elevens. The surrogate's whole job is to propose a shortlist worth
scoring.

**`v_s` is untilted, on purpose.** lambda tilts only the player's own term. `v_s` is a
property of the roster (what the bench covers), not of the risk appetite being tried, and
A17(3) pins it to `p*mu` in as many words. Keeping it fixed across the grid also makes the
families comparable — a lambda-dependent `v_s` would shift every candidate's total and blur
the very effect the tilt is there to explore — and costs one solve per module instead of five.

**The families.** `w_i(lambda) = p_i*mu_i + lambda*sigma_i^2` on a small symmetric grid
(SPEC): lambda > 0 seeks variance, lambda < 0 avoids it, and the evaluator decides which pays
against the opponent distribution — the Haugh-Singal sign flip, made concrete. Plus a **p = 1**
family, which is the same surrogate with availability switched off: `(1 - p)*v_s` vanishes and
the weight is plain `mu`, so that family leads with the XI a manager who ignored injuries
would field. It is in the shortlist so the evaluator can reject it on the evidence rather than
the surrogate rejecting it by construction.

**k counts distinct starter sets** [stats-4]. With `v_s` in the weight, two arrangements of
the same eleven score differently, so Murty enumerates permutations of one set before reaching
a second. The dedup is therefore inside the enumeration, not after it, or k = 10 would buy ten
orderings of one team. What survives per set is its cheapest arrangement, which — because
Murty emits in cost order — is the first one seen.

**Natural roles only**, exactly as `build.py`: an ineligible (slot, player) pair costs
`INELIGIBLE`, so no candidate can carry a `-1` or `-1*` cell, and the pinned slot order (A22)
means the positional guard passes at every position. That is the guard the live `LUP009` broke.
`INELIGIBLE` has to dominate any real score for the matcher to *prefer* a feasible assignment
at all, and even then the branch is judged edge by edge rather than by its total — see
`_constrained`, where summing was wrong in a way no threshold fixes.

Pure Python, and no numpy: it runs on `build.solve_assignment`, the same matcher the default
path uses, so the shortlist's optimum is the builder's optimum (AD3, and see
`solve_assignment`'s own note).
"""

from __future__ import annotations

import heapq
import itertools
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from fantabot.domain.lineup import schema
from fantabot.domain.lineup.build import INELIGIBLE, SlotsProvider, solve_assignment
from fantabot.domain.lineup.models import RosterPlayer

#: Distinct starter sets per (module, family). SPEC's k.
DEFAULT_K = 10

#: The tilt grid: symmetric, and including 0 so the untilted sub-aware solve is always tried.
#: A declared prior, not a fitted one — sigma^2 is on the squared vote scale (~1-4), so 0.25
#: moves a player by a few tenths of a vote, the order of the gaps the matcher decides on.
#: Which sign pays is the evaluator's answer, which is why the grid is symmetric.
DEFAULT_LAMBDAS: tuple[float, ...] = (-0.25, -0.125, 0.0, 0.125, 0.25)

#: Constrained re-solves one Murty run may spend before it returns what it has. The work-unit
#: budget of AD6, at the one place in this module that can run long: a roster whose players
#: are role-flexible has many arrangements per set, and without a bound the search for the
#: k-th *set* is unbounded in the number of assignments it steps over. Bounding it costs
#: candidates, never correctness — what is returned is still best-first.
DEFAULT_NODE_BUDGET = 1_200

#: The family that ignores availability.
P1_FAMILY = "p1"


@dataclass(frozen=True, slots=True)
class Candidate:
    """One eleven the evaluator may choose, and where it came from."""

    module: str
    #: Player ids in the platform's slot order, GK first — a submittable `starts[]`.
    starts: tuple[int, ...]
    #: The family that proposed it first (`tilt+0.125`, `p1`). Provenance, not a ranking key.
    family: str
    #: Its total under that family's surrogate. Comparable **within** a family only: the
    #: tilt term changes the scale, so the evaluator ranks across families, not this.
    surrogate: float

    @property
    def starter_set(self) -> frozenset[int]:
        """The eleven as a set — the identity two families are deduplicated on."""
        return frozenset(self.starts)


def p_one(mu: Mapping[int, float]) -> dict[int, float]:
    """Every projected player at certainty: the p = 1 family's presence map."""
    return dict.fromkeys(mu, 1.0)


def first_solve(
    roster: Sequence[RosterPlayer],
    slot_sets: Sequence[frozenset[str]],
    *,
    expected: Mapping[int, float],
) -> tuple[int, ...] | None:
    """Pass one: the ids, in slot order, maximising `sum(expected)`, or `None` if unfieldable.

    The same problem `build.lineup_for_module` solves, stated on ids rather than on a module
    code, because the caller here already has the slots and wants the reserves — everyone
    this does *not* return.
    """
    cost = [
        [
            -expected.get(player.id, 0.0) if (player.roles & slot_set) else INELIGIBLE
            for player in roster
        ]
        for slot_set in slot_sets
    ]
    if len(cost) > len(roster):
        return None
    assignment = solve_assignment(cost)
    if any(cost[slot][column] >= INELIGIBLE for slot, column in enumerate(assignment)):
        return None
    return tuple(roster[column].id for column in assignment)


def slot_replacement(
    roster: Sequence[RosterPlayer],
    slot_sets: Sequence[frozenset[str]],
    *,
    expected: Mapping[int, float],
    reserves: Collection[int],
) -> tuple[float, ...]:
    """`v_s` per slot: the best `expected` among the `reserves` whose roles cover it.

    `0.0` where no reserve covers the slot — nobody comes on there, so an absent starter is
    worth nothing rather than worth whatever the bench's best man is worth somewhere else.
    That is the case `value.replacement_level`'s single number gets wrong.
    """
    pool = set(reserves)
    bench = [player for player in roster if player.id in pool]
    return tuple(
        max(
            (expected.get(player.id, 0.0) for player in bench if player.roles & slot_set),
            default=0.0,
        )
        for slot_set in slot_sets
    )


def surrogate_matrix(
    roster: Sequence[RosterPlayer],
    slot_sets: Sequence[frozenset[str]],
    *,
    mu: Mapping[int, float],
    p: Mapping[int, float],
    sigma2: Mapping[int, float],
    lam: float,
    replacement: Sequence[float],
) -> list[list[float]]:
    """`w[s][i] = p_i*mu_i + lambda*sigma_i^2 + (1 - p_i)*v_s` — A17(3), tilted.

    Defined for every pair, eligible or not; `assignment_cost` is what refuses the ineligible
    ones. A missing player is `mu = 0`, `p = 0`, `sigma^2 = 0` — worth exactly his replacement,
    which is what a man with no projection is.
    """
    base = [
        p.get(player.id, 0.0) * mu.get(player.id, 0.0) + lam * sigma2.get(player.id, 0.0)
        for player in roster
    ]
    slack = [1.0 - p.get(player.id, 0.0) for player in roster]
    return [
        [base[i] + slack[i] * replacement[s] for i in range(len(roster))]
        for s in range(len(slot_sets))
    ]


def assignment_cost(
    roster: Sequence[RosterPlayer],
    slot_sets: Sequence[frozenset[str]],
    weights: Sequence[Sequence[float]],
) -> list[list[float]]:
    """The matcher's cost: `-w[s][i]` where the roles fit, `INELIGIBLE` where they do not.

    Negated because `solve_assignment` minimises, and the same `INELIGIBLE` the builder uses,
    so "this branch is infeasible" reads the same in both.
    """
    return [
        [
            -weights[s][i] if (player.roles & slot_sets[s]) else INELIGIBLE
            for i, player in enumerate(roster)
        ]
        for s in range(len(slot_sets))
    ]


def _constrained(
    cost: Sequence[Sequence[float]],
    forced: Sequence[tuple[int, int]],
    banned: frozenset[tuple[int, int]],
) -> tuple[float, tuple[int, ...]] | None:
    """The cheapest full assignment that takes every `forced` edge and no `banned` one.

    `None` when there is none. Forcing shrinks the problem rather than penalising it — the
    forced rows and their columns are removed and the matcher runs on what is left — so the
    deep nodes of the search are the cheap ones. Banning is by cost: a banned edge is handed
    to the matcher at `INELIGIBLE`, exactly as an ineligible one already is.

    ⚠ **Infeasibility is read back per edge, never off the total.** The obvious test — a
    total at or above `INELIGIBLE` — is wrong, and silently: the surrogate is a score, so
    every eligible edge costs `-w` and an eleven's real part is around -70. One ineligible
    edge then totals `INELIGIBLE - 70`, which is *below* the threshold, and the branch is
    accepted with a player in a slot his roles do not cover. Measured 2026-09-22 on a matrix
    whose only assignment used an ineligible edge: it came back as a candidate. No magnitude
    of `INELIGIBLE` repairs that, because the real part scales with it; only asking each
    chosen edge does. `build.lineup_for_module` and `first_solve` already ask per edge — this
    was the one place that summed instead.
    """
    rows, columns = len(cost), len(cost[0])
    fixed = dict(forced)
    taken = set(fixed.values())
    free_rows = [row for row in range(rows) if row not in fixed]
    free_columns = [column for column in range(columns) if column not in taken]
    if len(free_rows) > len(free_columns):
        return None

    total = sum(cost[row][column] for row, column in forced)
    assignment = dict(fixed)
    if free_rows:
        sub = [
            [
                INELIGIBLE if (row, column) in banned else cost[row][column]
                for column in free_columns
            ]
            for row in free_rows
        ]
        for index, choice in enumerate(solve_assignment(sub)):
            assignment[free_rows[index]] = free_columns[choice]
            total += sub[index][choice]

    chosen = tuple(assignment[row] for row in range(rows))
    for row, column in enumerate(chosen):
        if cost[row][column] >= INELIGIBLE or (row, column) in banned:
            return None
    return total, chosen


def k_best_assignments(
    cost: Sequence[Sequence[float]],
    *,
    k: int = DEFAULT_K,
    node_budget: int = DEFAULT_NODE_BUDGET,
) -> list[tuple[float, tuple[int, ...]]]:
    """Up to `k` **distinct column-sets**, cheapest first, as `(total, assignment)`.

    Murty's algorithm. The best assignment is solved, then the space of everything else is
    partitioned by the row it first disagrees on: child q forces the optimum's edges for the
    free rows before q and bans its edge at q. Every other assignment lands in exactly one
    child, so popping the cheapest open node in turn emits assignments in non-decreasing cost.

    Sets, not assignments, is the only departure. A set's first appearance is therefore its
    cheapest arrangement, and later arrangements of it are stepped over — they still have to
    be *generated*, which is what `node_budget` bounds.
    """
    if k <= 0 or not cost or not cost[0]:
        return []
    root = _constrained(cost, (), frozenset())
    if root is None:
        return []

    #: `(total, tie-break, forced prefix, banned set, that node's optimum)`. The counter is
    #: what keeps the order total without ever comparing the payloads, which are not ordered.
    Edges = tuple[tuple[int, int], ...]
    Node = tuple[float, int, Edges, frozenset[tuple[int, int]], tuple[int, ...]]

    order = itertools.count()
    heap: list[Node] = [(root[0], next(order), (), frozenset(), root[1])]
    found: list[tuple[float, tuple[int, ...]]] = []
    seen: set[frozenset[int]] = set()
    spent = 0

    while heap and len(found) < k and spent < node_budget:
        total, _, forced, banned, assignment = heapq.heappop(heap)
        key = frozenset(assignment)
        if key not in seen:
            seen.add(key)
            found.append((total, assignment))
            if len(found) == k:
                break

        settled = {row for row, _ in forced}
        prefix = list(forced)
        for row in range(len(assignment)):
            if row in settled:
                continue
            if spent >= node_budget:
                break
            spent += 1
            # One name, deliberately. Written twice — once for the solve, once for the node —
            # the two can disagree, and the disagreement is invisible: a mutation that dropped
            # the ban from the *solve* alone left it in the pushed node, so every child still
            # explored correctly and only its own cached optimum went stale, into a set already
            # seen. Correct answers, three times the work, and nothing red. Bind them together.
            blocked = banned | {(row, assignment[row])}
            child = _constrained(cost, tuple(prefix), blocked)
            if child is not None:
                heapq.heappush(heap, (child[0], next(order), tuple(prefix), blocked, child[1]))
            prefix.append((row, assignment[row]))

    return found


def module_family(
    roster: Sequence[RosterPlayer],
    module_code: str,
    *,
    mu: Mapping[int, float],
    p: Mapping[int, float],
    sigma2: Mapping[int, float],
    lam: float,
    family: str,
    k: int = DEFAULT_K,
    node_budget: int = DEFAULT_NODE_BUDGET,
    slots_provider: SlotsProvider = schema.slots,
) -> list[Candidate]:
    """One (module, family)'s k best elevens, best surrogate first.

    The two solves live here: `first_solve` on `p*mu` to find the reserves, then the k-best on
    the matrix `v_s` makes out of them. An unknown or unfieldable module returns `[]` — the
    builder drops such a module too, and an unattended run must not die of one.
    """
    try:
        slot_sets = slots_provider(module_code)
    except ValueError:
        return []

    expected = {player.id: p.get(player.id, 0.0) * mu.get(player.id, 0.0) for player in roster}
    starters = first_solve(roster, slot_sets, expected=expected)
    if starters is None:
        return []

    fielded = set(starters)
    reserves = [player.id for player in roster if player.id not in fielded]
    replacement = slot_replacement(roster, slot_sets, expected=expected, reserves=reserves)
    weights = surrogate_matrix(
        roster, slot_sets, mu=mu, p=p, sigma2=sigma2, lam=lam, replacement=replacement
    )
    cost = assignment_cost(roster, slot_sets, weights)

    return [
        Candidate(
            module=module_code,
            starts=tuple(roster[column].id for column in assignment),
            family=family,
            surrogate=-total,
        )
        for total, assignment in k_best_assignments(cost, k=k, node_budget=node_budget)
    ]


def _families(
    p: Mapping[int, float], mu: Mapping[int, float], lambdas: Sequence[float]
) -> list[tuple[str, float, Mapping[int, float]]]:
    """`(label, lambda, presence)` per family: the tilt grid, then p = 1."""
    return [(f"tilt{lam:+g}", lam, p) for lam in lambdas] + [(P1_FAMILY, 0.0, p_one(mu))]


def candidates_for_module(
    roster: Sequence[RosterPlayer],
    module_code: str,
    *,
    mu: Mapping[int, float],
    p: Mapping[int, float],
    sigma2: Mapping[int, float],
    lambdas: Sequence[float] = DEFAULT_LAMBDAS,
    k: int = DEFAULT_K,
    node_budget: int = DEFAULT_NODE_BUDGET,
    slots_provider: SlotsProvider = schema.slots,
) -> list[Candidate]:
    """Every family's k-best for one module, deduplicated on the starter set.

    Order is **discovery** order — the tilt grid as given, then p = 1, each family best-first.
    It is deterministic, which AD6 needs, and it is not a ranking: surrogates from different
    lambdas are on different scales, and it is the evaluator that ranks. A set two families
    both propose keeps the first one's label and price, so `family` reads as "who found it",
    never as "who alone would have".
    """
    union: list[Candidate] = []
    seen: set[frozenset[int]] = set()
    for label, lam, presence in _families(p, mu, lambdas):
        for candidate in module_family(
            roster,
            module_code,
            mu=mu,
            p=presence,
            sigma2=sigma2,
            lam=lam,
            family=label,
            k=k,
            node_budget=node_budget,
            slots_provider=slots_provider,
        ):
            if candidate.starter_set in seen:
                continue
            seen.add(candidate.starter_set)
            union.append(candidate)
    return union


def build_candidates(
    roster: Sequence[RosterPlayer],
    modules: Sequence[str],
    *,
    mu: Mapping[int, float],
    p: Mapping[int, float],
    sigma2: Mapping[int, float],
    lambdas: Sequence[float] = DEFAULT_LAMBDAS,
    k: int = DEFAULT_K,
    node_budget: int = DEFAULT_NODE_BUDGET,
    slots_provider: SlotsProvider = schema.slots,
) -> list[Candidate]:
    """The shortlist: every fieldable module's families, unioned in the order `modules` gives.

    Deduplication is per module, never across: the same eleven under two schemas is two
    different candidates, because the schema is what the auto-sub engine substitutes into and
    what the platform judges `starts[i]` against.
    """
    shortlist: list[Candidate] = []
    for code in modules:
        shortlist.extend(
            candidates_for_module(
                roster,
                code,
                mu=mu,
                p=p,
                sigma2=sigma2,
                lambdas=lambdas,
                k=k,
                node_budget=node_budget,
                slots_provider=slots_provider,
            )
        )
    return shortlist
