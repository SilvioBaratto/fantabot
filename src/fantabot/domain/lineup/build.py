"""Assign the roster to a module's slots to maximise `sum(value)`, and pick the best module.

Because the objective is a sum of per-player scores over the started players, it is linear,
so for a fixed module the best XI is a **max-weight bipartite matching** that saturates the
11 slots — solved exactly here by the Hungarian algorithm, no dependency added (11 slots x
~30 players resolves in microseconds). `ranked_lineups` runs it for each allowed module and
sorts, best first, so the caller can walk down the list when the platform refuses one.

Slots come from `schema.slots`: the natural ("ok") roles of `mantra_schemi.json`, laid out
in the platform's own slot order (`mantra_starts_order.json`, pinned from its JS bundle).
**Natural roles alone never made a lineup legal.** The matcher sees role *sets*, so they
say each starter fits *a* slot; the platform judges `starts[i]` at *its* slot i. Laid out
in the PDF's row order, a C-only player landed in a pure-M slot: on 2026-09-21 seven of the
lega's eleven modules were refused with `LUP009` before one stuck, and a `-1` there is
worse — accepted, and scored as a malus. So a lineup built here is malus-free and
submission-legal only under the pinned order, and `domain/lineup/positional.py` checks it
at every position before any POST rather than trusting it. `asta.legality`, which does
admit the `-1` cells, confirms every result (cross-checked in the tests).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from functools import lru_cache

from fantabot.domain.lineup import schema
from fantabot.domain.lineup.models import RosterPlayer

#: A module code -> its ordered slot role-sets (GK first). `schema.slots` for Mantra,
#: `schema.classic_slots` for Classic; injected so the builder itself is format-neutral.
SlotsProvider = Callable[[str], tuple[frozenset[str], ...]]

#: Cost of placing a player in a slot his roles do not cover. Large enough to dominate any
#: real score, so the matcher uses such an edge only when no feasible assignment exists — a
#: state the caller then detects and rejects. Public because that detection is the *caller's*:
#: `candidates` asks it of **each chosen edge** (`cost[slot][column] >= INELIGIBLE`), never of
#: the assignment's total, and a private copy of the threshold in each reader is a copy that
#: can drift out of agreement. Reading the total was the T22 defect, fixed 2026-09-22 — the
#: reasoning is in `place_all_with_malus` below and in `candidates._constrained`.
INELIGIBLE = 1e9


def lineup_for_module(
    roster: Sequence[RosterPlayer],
    module_code: str,
    *,
    value: Mapping[int, float],
    slots_provider: SlotsProvider = schema.slots,
) -> list[int] | None:
    """The max-value `starts[]` (11 ids, GK first) for one module, or `None` if the module
    is unknown or the roster cannot field it with natural roles.

    An unknown code (the platform's `mods` diverged from the shipped schemi, or a wrong-mode
    league) is treated as infeasible and dropped, not raised — the caller ranks over what is
    fieldable, and a bad code must not crash an unattended run.
    """
    try:
        slot_sets = slots_provider(module_code)
    except ValueError:
        return None
    players = list(roster)
    n, m = len(slot_sets), len(players)
    if m < n:
        return None

    cost = [
        [
            -value.get(players[j].id, 0.0) if (players[j].roles & slot_sets[i]) else INELIGIBLE
            for j in range(m)
        ]
        for i in range(n)
    ]
    assignment = solve_assignment(cost)

    starts: list[int] = []
    for slot_index, player_index in enumerate(assignment):
        player = players[player_index]
        if not (player.roles & slot_sets[slot_index]):
            return None  # a slot had to borrow an ineligible player — module not fieldable
        starts.append(player.id)
    return starts


def ranked_lineups(
    roster: Sequence[RosterPlayer],
    modules: Sequence[str],
    *,
    value: Mapping[int, float],
    slots_provider: SlotsProvider = schema.slots,
) -> list[tuple[str, list[int]]]:
    """Every fieldable module's `(module, starts[])`, best `sum(value)` first.

    Infeasible modules are dropped. Ties keep the order of `modules` (stable sort). The
    caller submits down this list, falling to the next module if the platform rejects one,
    so a refusal is survived rather than fatal. It was read on 2026-09-02 as a wrong
    4-1-4-1 schema; the role sets were right, and the refusal was the slot order above.
    """
    scored: list[tuple[float, str, list[int]]] = []
    for code in modules:
        starts = lineup_for_module(roster, code, value=value, slots_provider=slots_provider)
        if starts is None:
            continue
        total = sum(value.get(pid, 0.0) for pid in starts)
        scored.append((total, code, starts))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [(code, starts) for _total, code, starts in scored]


def place_all_with_malus(
    role_sets: Sequence[frozenset[str]],
    natural_sets: Sequence[frozenset[str]],
    admitted_sets: Sequence[frozenset[str]],
) -> tuple[list[int], int] | None:
    """`(slot per player, how many of them are out of position)`, or `None` when they do not
    all fit in distinct admitted slots.

    A player placed in a slot whose `natural_sets` entry his roles cover costs nothing; one
    whose roles only reach `admitted_sets` costs the platform's **-1** and is counted; one
    who reaches neither cannot take that slot at all. Minimising the total cost therefore
    minimises the number of maluses, which is what the Adapted tier is defined to do
    (`rules/sistema-mantra.md`: "the least-total-malus Adapted fit").

    Which of several interchangeable players carries the malus is not decided here and must
    not be read off the result — the rules doc's own gotcha is that the platform picks
    "in no particular order" among a same-role group. Only the **count** is meaningful.

    ⚠ **More players than slots must be refused here**, not left to the matcher:
    `solve_assignment` assumes rows <= columns, and with more rows its loop never terminates —
    it hangs rather than returning something wrong. Measured 2026-09-22, when a mutation of
    this guard stopped a whole test run dead instead of failing it.

    ⚠ **Feasibility is judged edge by edge, never off the total.** With 11 slots the maluses
    sum to at most 11, so a single `INELIGIBLE` edge would still dominate — but that is an
    accident of the numbers, and `candidates.k_best_assignments` already shipped the same
    reasoning as a defect: a total read as infeasible-or-not accepted a branch holding one
    ineligible edge (T22, 2026-09-22).
    """
    if len(role_sets) > len(natural_sets):
        return None
    if not role_sets:
        return [], 0
    # Canonicalised before the matcher, so the memo below sees one key for every ordering of
    # the same men. That is most of its value: the auto-sub engine asks this question once
    # per bench *combination*, and swapping which of two centre-backs is in the combination
    # is the same matching problem twice.
    order = sorted(range(len(role_sets)), key=lambda i: sorted(role_sets[i]))
    solved = _matched(
        tuple(role_sets[i] for i in order), tuple(natural_sets), tuple(admitted_sets)
    )
    if solved is None:
        return None
    canonical, malus = solved
    placement = [0] * len(role_sets)
    for position, slot in zip(order, canonical, strict=True):
        placement[position] = slot
    return placement, malus


@lru_cache(maxsize=1 << 17)
def _matched(
    role_sets: tuple[frozenset[str], ...],
    natural_sets: tuple[frozenset[str], ...],
    admitted_sets: tuple[frozenset[str], ...],
) -> tuple[tuple[int, ...], int] | None:
    """The matching itself, memoised on the question rather than on the asker.

    Pure: the same role sets against the same slots are the same matching, whichever lega
    asked — which is why a module-level cache is safe here and is not in `SubstitutionEngine`,
    whose key is a roster's own player ids. Measured 2026-09-23 on a realistic 23-man Mantra
    board: 6.5 ms per absence pattern without it, because a pattern costs one Hungarian per
    bench combination per module and the Adapted tier scans all of them.

    ⚠ Which of several interchangeable players carries the malus is not stable under the
    canonicalisation, and must not be read — the platform picks "in no particular order"
    (`rules/sistema-mantra.md`), and only the **count** was ever meaningful.
    """
    slots = range(len(natural_sets))
    cost = [
        [
            0.0
            if (roles & natural_sets[j])
            else (1.0 if (roles & admitted_sets[j]) else INELIGIBLE)
            for j in slots
        ]
        for roles in role_sets
    ]
    assignment = solve_assignment(cost)
    if any(not (role_sets[i] & admitted_sets[slot]) for i, slot in enumerate(assignment)):
        return None
    malus = sum(
        1 for i, slot in enumerate(assignment) if not (role_sets[i] & natural_sets[slot])
    )
    return tuple(assignment), malus


def solve_assignment(cost: list[list[float]]) -> list[int]:
    """Min-cost assignment of every row to a distinct column (`n` rows <= `m` cols).

    The standard O(n^2 m) Jonker-Volgenant/Hungarian shortest-augmenting-path form. Returns
    `assignment[row] = col`. Used here to *maximise* score by passing negated values as cost.

    Public, and deliberately the **only** matcher in the phase: `candidates` runs Murty's
    k-best on top of it, one constrained re-solve per branch. A second implementation there —
    numpy's, say — would make the outer loop's optimum a different one from the builder's, so
    the XI the model proposes and the XI the default path would build could disagree for no
    reason anyone could name. It stays pure Python for the same reason `build` is on the
    default path at all: nothing here may pull numpy in (AD3).
    """
    n = len(cost)
    m = len(cost[0])
    inf = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)  # p[col] = row matched to col (0 = unmatched)
    way = [0] * (m + 1)

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [inf] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = inf
            j1 = -1
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1

    assignment = [0] * n
    for j in range(1, m + 1):
        if p[j] != 0:
            assignment[p[j] - 1] = j - 1
    return assignment
