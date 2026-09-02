"""Assign the roster to a module's slots to maximise `sum(value)`, and pick the best module.

Because the objective is a sum of per-player scores over the started players, it is linear,
so for a fixed module the best XI is a **max-weight bipartite matching** that saturates the
11 slots — solved exactly here by the Hungarian algorithm, no dependency added (11 slots x
~30 players resolves in microseconds). `best_lineup` runs it for each allowed module and
takes the argmax.

Slots come from `schema.slots`, i.e. the natural ("ok") roles of `mantra_schemi.json`. That
is a subset of what the platform accepts at submission (which also allows out-of-position
`-1` cells with a malus), so a lineup built here never takes a malus and is always
submission-legal — the guard against the live `LUP009`. `asta.legality`, which does admit
the `-1` cells, therefore confirms every result (crossed-checked in the tests).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from fantabot.domain.lineup import schema
from fantabot.domain.lineup.errors import NoFieldableModule
from fantabot.domain.lineup.models import RosterPlayer

#: Cost of placing a player in a slot his roles do not cover. Large enough to dominate any
#: real score, so the matcher uses such an edge only when no feasible assignment exists — a
#: state the caller then detects and rejects.
_INELIGIBLE = 1e9


def lineup_for_module(
    roster: Sequence[RosterPlayer],
    module_code: str,
    *,
    value: Mapping[int, float],
) -> list[int] | None:
    """The max-value `starts[]` (11 ids, GK first) for one module, or `None` if the roster
    cannot field it with natural roles."""
    slot_sets = schema.slots(module_code)
    players = list(roster)
    n, m = len(slot_sets), len(players)
    if m < n:
        return None

    cost = [
        [
            -value.get(players[j].id, 0.0) if (players[j].roles & slot_sets[i]) else _INELIGIBLE
            for j in range(m)
        ]
        for i in range(n)
    ]
    assignment = _hungarian(cost)

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
) -> list[tuple[str, list[int]]]:
    """Every fieldable module's `(module, starts[])`, best `sum(value)` first.

    Infeasible modules are dropped. Ties keep the order of `modules` (stable sort). The
    caller submits down this list, falling to the next module if the platform rejects one —
    which is how a wrong schema (`mantra_schemi.json`'s 4-1-4-1 was, live 2026-09-02) is
    survived rather than fatal.
    """
    scored: list[tuple[float, str, list[int]]] = []
    for code in modules:
        starts = lineup_for_module(roster, code, value=value)
        if starts is None:
            continue
        total = sum(value.get(pid, 0.0) for pid in starts)
        scored.append((total, code, starts))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [(code, starts) for _total, code, starts in scored]


def best_lineup(
    roster: Sequence[RosterPlayer],
    modules: Sequence[str],
    *,
    value: Mapping[int, float],
) -> tuple[str, list[int]]:
    """`(module, starts[])` maximising `sum(value)`. Raises `NoFieldableModule` when the
    roster fields none of the allowed modules."""
    ranked = ranked_lineups(roster, modules, value=value)
    if not ranked:
        raise NoFieldableModule(tuple(modules))
    return ranked[0]


def _hungarian(cost: list[list[float]]) -> list[int]:
    """Min-cost assignment of every row to a distinct column (`n` rows <= `m` cols).

    The standard O(n^2 m) Jonker-Volgenant/Hungarian shortest-augmenting-path form. Returns
    `assignment[row] = col`. Used here to *maximise* score by passing negated values as cost.
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
