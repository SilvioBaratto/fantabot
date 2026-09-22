"""The Mantra auto-sub engine: who comes on when starters have no vote. Pure.

`rules/sistema-mantra.md` §Substitution System. This module holds what all three modes share;
the Efficient and Adapted tiers and the modes themselves are T20's.

**Replacements are a block, not a queue.** With N starters missing, the engine tests
*combinations* of N bench players, in an order driven by bench position: for A-B-C-D-E and
three vacancies, `ABC, ABD, ABE, ACD, ACE, ADE, BCD, BCE, BDE, CDE` — plain lexicographic
order over the bench, which is what `bench_combinations` is. So the bench is an ordered
preference and not a set, and the second man on is whoever *pairs* with the first, never the
next name down: a forward and a centre-back missing takes `(1, 3)` over `(1, 2)` even though
2 is the earlier player.

**The keeper goes first**, before any combination is searched, and the earliest reserve
keeper on the bench takes the shirt. He spends one of the substitutions a lega allows, which
is why `max_subs` (`ssnum`, if it is a cap at all — Open Question 1) counts him.

**Every position is reallocated, not patched.** The eleven on the pitch are matched to the
schema's slots afresh, so surviving starters move: a `C/M` slides into the pure `C` slot to
let an incoming `M` take the one he left. That is also why a malus can land on a player
nobody expected — the rules doc's own gotcha, and T20's problem.

**Nothing that fits means one fewer.** The whole search restarts with one replacement fewer
until it does fit, down to none, and the team plays a man short: `fielded` carries `None` in
the slot nobody filled. A lineup is never refused for being un-fillable.

The Optimal tier is all of this under the **original schema with natural roles** — no malus,
so `mantra_compat.json` is not read here yet.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import TypeVar

from fantabot.domain.lineup import schema
from fantabot.domain.lineup.bench import GK_ROLE
from fantabot.domain.lineup.build import SlotsProvider, place_all

Bench = TypeVar("Bench")


@dataclass(frozen=True, slots=True)
class Substitution:
    """The XI that actually takes the field, after the engine has run."""

    module: str
    #: Slot order, `None` where nobody could be placed (the team plays a man short).
    fielded: tuple[int | None, ...]
    #: Who came on, keeper first and then bench order.
    entered: tuple[int, ...]
    #: Slots left empty.
    short: int


def bench_combinations(bench: Sequence[Bench], size: int) -> list[tuple[Bench, ...]]:
    """Every `size`-man combination of the bench, in bench-position order."""
    return list(combinations(bench, size))


def substitute(
    *,
    module: str,
    starts: Sequence[int],
    bench: Sequence[int],
    voted: Collection[int],
    roles: Mapping[int, frozenset[str]],
    slots_provider: SlotsProvider = schema.slots,
    max_subs: int | None = None,
) -> Substitution:
    """Field the XI `starts` after the players outside `voted` are replaced from `bench`.

    `voted` is who got a vote this matchday — starters and bench alike, since a bench player
    without one cannot come on either.
    """
    slot_sets = slots_provider(module)
    if len(starts) != len(slot_sets):
        raise ValueError(
            f"a lineup is eleven players in {module}'s slots; got {len(starts)}"
        )
    if max_subs is not None and max_subs < 0:
        raise ValueError(f"max_subs is a count of substitutions; got {max_subs}")

    present = set(voted)
    survivors = [pid for pid in starts if pid in present]
    if len(survivors) == len(starts):
        return Substitution(module, tuple(starts), (), 0)

    available = [pid for pid in bench if pid in present]
    budget = len(available) if max_subs is None else max_subs
    entered: list[int] = []
    if starts[0] not in present and budget > 0:
        keeper = next((pid for pid in available if GK_ROLE in roles.get(pid, frozenset())), None)
        if keeper is not None:
            entered.append(keeper)
            available.remove(keeper)
            budget -= 1

    vacancies = len(slot_sets) - len(survivors) - len(entered)
    for size in range(min(vacancies, budget, len(available)), -1, -1):
        for combination in bench_combinations(available, size):
            fielded = _field([*survivors, *entered, *combination], slot_sets, roles)
            if fielded is not None:
                return Substitution(
                    module=module,
                    fielded=fielded,
                    entered=(*entered, *combination),
                    short=fielded.count(None),
                )
    raise AssertionError(  # pragma: no cover - the empty combination always places nobody
        "the search must terminate at no replacements at all"
    )


def _field(
    players: Sequence[int],
    slot_sets: Sequence[frozenset[str]],
    roles: Mapping[int, frozenset[str]],
) -> tuple[int | None, ...] | None:
    """`players` laid into the slots, or `None` when they do not all fit in distinct ones."""
    placement = place_all([roles.get(pid, frozenset()) for pid in players], slot_sets)
    if placement is None:
        return None
    fielded: list[int | None] = [None] * len(slot_sets)
    for player, slot in zip(players, placement, strict=True):
        fielded[slot] = player
    return tuple(fielded)
