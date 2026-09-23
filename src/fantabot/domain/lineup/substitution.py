"""The Mantra auto-sub engine: who comes on when starters have no vote, and at what cost. Pure.

`rules/sistema-mantra.md` §Substitution System, in full: the shared combination search, the
three tiers and the three modes.

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
nobody expected — the rules doc's own gotcha — and it is why only the malus **count** is
meaningful here and never which player carries it.

**Nothing that fits means one fewer.** The whole search — every tier — restarts with one
replacement fewer until it does fit, down to none, and the team plays a man short: `fielded`
carries `None` in the slot nobody filled. A lineup is never refused for being un-fillable.

### The three tiers, and what actually separates the three modes

A tier is a pair: *which modules may be fielded* and *may a placement cost a malus*.

* **Optimal** — the original module, natural roles, no malus.
* **Efficient** — a *different* module, natural roles, still no malus.
* **Adapted** — a module, and one or more `-1` out-of-position placements. `-1*` cells
  are admitted **here and only here**: the platform refuses them at submission and allows
  them once a substitution has been forced.

The modes differ in nothing but the order those are tried, and the difference is real:

* **BASIC** — `(original, no malus)`, then `(another, no malus)`, then `(any, malus)`.
* **EASY** — `(original, no malus)`, then `(original, malus)`. The module never changes.
* **MASTER** — `(any, no malus)`, then `(any, malus)`. Merging Optimal into Efficient is
  exactly what "bench order dominates" means: BASIC will change *who comes on* to keep the
  module, MASTER will change the module to keep the earlier bench player.

Within a tier the combination is the outer key and the module the inner one, so an earlier
bench combination always beats a later one at the same cost. The Adapted tier takes the
**least total malus**, ties broken by that same combination order — `rules/sistema-mantra.md`
again. It may stop at a malus of 1: every zero-malus fit reachable in this mode was already
refused by the tiers before it, so 1 is the best that remains.

The reported `tier` is read off the answer rather than off the loop that found it — malus
first, then whether the module moved — so MASTER's single no-malus tier still reports
`optimal` or `efficient` for what it actually did.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations
from typing import Literal, TypeVar

from fantabot.domain.lineup import schema
from fantabot.domain.lineup.bench import GK_ROLE
from fantabot.domain.lineup.build import place_all_with_malus
from fantabot.domain.lineup.schema import SlotAdmission

Bench = TypeVar("Bench")

#: The lega's substitution mode. Lower case because that is how the setting is written.
SubMode = Literal["basic", "easy", "master"]

#: Every mode the engine knows. `parse_sub_mode` fails closed against exactly this set.
SUB_MODES: frozenset[str] = frozenset({"basic", "easy", "master"})

#: What the engine did, cheapest first. Reported, never used to steer the search.
Tier = Literal["optimal", "efficient", "adapted"]

#: One tier of the search: its modules, whether a placement may cost a malus, and the
#: cheapest it can still be — see `_with_floors`.
Tiering = tuple[tuple[str, ...], bool, int]

def parse_sub_mode(value: str | None) -> SubMode | None:
    """`FANTABOT_LINEUP_SUB_MODE` as a mode, or `None`.

    Fails closed (AD4): an unset, blank or unrecognised value is `None`, which the callers
    read as "the operator has not told us" — not as a default mode. Guessing BASIC here
    would make an unanswered Open Question look answered, and the three modes field
    different XIs.
    """
    if value is None:
        return None
    candidate = value.strip().lower()
    return candidate if candidate in SUB_MODES else None  # type: ignore[return-value]


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
    #: How many of the eleven are out of position. **A count, not an identity** — which of
    #: several interchangeable players carries it is the platform's to pick, "in no
    #: particular order" (`rules/sistema-mantra.md`).
    malus: int = 0
    #: Which tier produced this XI, read off the answer.
    tier: Tier = "optimal"


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
    mode: SubMode,
    modules: Sequence[str] = (),
    max_subs: int | None = None,
) -> Substitution:
    """Field the XI `starts` after the players outside `voted` are replaced from `bench`.

    `voted` is who got a vote this matchday — starters and bench alike, since a bench player
    without one cannot come on either.

    `modules` is the lega's own `mods` list, which is what the Efficient and Adapted tiers
    may move to. It defaults to **empty**, meaning "the original only": a module the lega
    does not allow is one the platform would refuse, so an engine that invented the eleven
    would model a game nobody is playing.

    `mode` has **no default**, for the reason `parse_sub_mode` has none: the operator has
    not answered Open Question 1 and the three modes field different XIs. A `"basic"`
    default here was harmless only while `modules` was also empty — BASIC's Efficient tier
    is guarded by `if others:`, so with no module list the search degenerates to EASY's —
    and it would have started guessing the moment T28 passed the lega's real `mods`. A
    default that is correct only while a second default is also taken is not a default.
    """
    table = _admission_table(module, modules)
    slot_sets = table[module]
    if len(starts) != len(slot_sets):
        raise ValueError(f"a lineup is eleven players in {module}'s slots; got {len(starts)}")
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
        for tier_modules, with_malus, floor in tier_plan(mode, module, tuple(table)):
            best = _best_in_tier(
                size=size,
                module=module,
                floor=floor,
                survivors=survivors,
                entered=entered,
                available=available,
                roles=roles,
                table=table,
                tier_modules=tier_modules,
                with_malus=with_malus,
            )
            if best is not None:
                return best
    raise AssertionError(  # pragma: no cover - the empty combination always places nobody
        "the search must terminate at no replacements at all"
    )


def tier_plan(mode: SubMode, module: str, available: Sequence[str]) -> tuple[Tiering, ...]:
    """`(modules, may it cost a malus, the cheapest it can still be)` per tier, in order.

    Public because it *is* the difference between the three modes, and a search order that
    can only be read by watching which XI comes out is one nothing can assert directly.

    The third field is what makes the Adapted tier's early exit safe. It may stop at the
    first fit costing `floor` because nothing cheaper remains to find — and "nothing cheaper
    remains" is a statement about the *free* tiers that ran before it over the *same*
    modules, not a constant. So it is derived here, from the modules already scanned free,
    rather than written as a literal `1` in the search: add a mode whose malus tier reaches
    a module its free tiers never did and a literal would return a malus-1 XI while a
    malus-0 one sat unexamined.
    """
    others = tuple(code for code in available if code != module)
    if mode == "easy":
        return _with_floors((((module,), False), ((module,), True)))
    if mode == "master":
        every = (module, *others)
        return _with_floors(((every, False), (every, True)))
    basic: list[tuple[tuple[str, ...], bool]] = [((module,), False)]
    if others:
        basic.append((others, False))
    basic.append(((module, *others), True))
    return _with_floors(tuple(basic))


def _with_floors(tiers: Sequence[tuple[tuple[str, ...], bool]]) -> tuple[Tiering, ...]:
    """Each tier, with the least it can still cost given the free tiers already run."""
    scanned_free: set[str] = set()
    out: list[Tiering] = []
    for modules, with_malus in tiers:
        floor = 0 if not with_malus or not set(modules) <= scanned_free else 1
        out.append((modules, with_malus, floor))
        if not with_malus:
            scanned_free.update(modules)
    return tuple(out)


def _best_in_tier(
    *,
    size: int,
    module: str,
    floor: int,
    survivors: Sequence[int],
    entered: Sequence[int],
    available: Sequence[int],
    roles: Mapping[int, frozenset[str]],
    table: Mapping[str, tuple[SlotAdmission, ...]],
    tier_modules: Sequence[str],
    with_malus: bool,
) -> Substitution | None:
    """The cheapest XI this tier can field with exactly `size` replacements, or `None`.

    Combination outer, module inner, so an earlier bench combination wins at equal cost; the
    first module in `tier_modules` wins a tie between modules. A malus-free tier stops at its
    first fit (every fit costs 0); a malus-bearing one stops at `floor`, the least it can
    still cost given what the free tiers already refused (`_with_floors`).
    """
    best: Substitution | None = None
    for combination in bench_combinations(list(available), size):
        players = [*survivors, *entered, *combination]
        role_sets = [roles.get(pid, frozenset()) for pid in players]
        for code in tier_modules:
            admissions = table[code]
            natural = [slot.natural for slot in admissions]
            admitted = natural if not with_malus else [slot.substitution for slot in admissions]
            placed = place_all_with_malus(role_sets, natural, admitted)
            if placed is None:
                continue
            slots, malus = placed
            fielded: list[int | None] = [None] * len(admissions)
            for player, slot in zip(players, slots, strict=True):
                fielded[slot] = player
            found = Substitution(
                module=code,
                fielded=tuple(fielded),
                entered=(*entered, *combination),
                short=fielded.count(None),
                malus=malus,
                tier=_tier_of(malus, code, module),
            )
            if not with_malus:
                return found
            if best is None or found.malus < best.malus:
                best = found
            if best.malus <= floor:
                return best
    return best


def _tier_of(malus: int, code: str, module: str) -> Tier:
    """Which tier this answer is, read off the answer: the cost, then whether it moved.

    `module` is the module the XI was **submitted** in and is passed down rather than read
    off the tier's own list. `tier_modules[0]` was the obvious shortcut and it is right for
    EASY, for MASTER and for two of BASIC's three tiers — and wrong for the one whose entire
    purpose is that the module changed, because BASIC's Efficient tier holds `others` and
    its first entry is another module. It reported `optimal` for a formation change.
    """
    if malus:
        return "adapted"
    return "optimal" if code == module else "efficient"


def _admission_table(
    module: str, modules: Sequence[str]
) -> dict[str, tuple[SlotAdmission, ...]]:
    """The module the XI was submitted in, plus every other module the lega allows.

    An allowed code the shipped schemi do not know is dropped rather than raised: the
    platform's `mods` has diverged before, and an unattended job must field *something*.
    The submitted module is not optional and a bad one raises, as `schema.admissions` does.

    ⚠ **EASY is not filtered here.** It reads the same table as the other two and `_tiers`
    is the one place that holds it to the original module. Skipping the build for EASY was
    the obvious optimisation and it was a second guard on one question: with the table
    already empty of alternatives, a mutation letting EASY's *Adapted* tier change module
    became unobservable, and the test that should have caught it passed. Measured
    2026-09-23 (mutant M01, survived behind M10). The build is `lru_cache`d anyway.
    """
    table = {module: schema.admissions(module)}
    for code in modules:
        if code in table:
            continue
        try:
            table[code] = schema.admissions(code)
        except ValueError:
            continue
    return table


@dataclass(frozen=True, slots=True)
class SubstitutionEngine:
    """`substitute` with everything but the absence pattern already bound, and memoized.

    The evaluator runs the engine once per candidate per draw — tens of thousands of calls
    over a handful of distinct absence patterns, because presence is drawn per player and
    most draws repeat. The cache lives on the instance and nowhere else: a module-level one
    would carry one lega's roster into the next, and `domain/` holds no global state.

    ⚠ **An instance is valid for exactly one (lega, competition, matchday).** The key is the
    absence pattern alone, and `roles`, `starts` and `bench` are held by reference: reuse it
    across matchdays, or mutate the roles mapping under it, and it answers from the cache
    with no error and no warning. Build a new one per plan.
    """

    module: str
    starts: tuple[int, ...]
    bench: tuple[int, ...]
    roles: Mapping[int, frozenset[str]]
    mode: SubMode
    modules: tuple[str, ...] = ()
    max_subs: int | None = None
    _cache: dict[tuple[tuple[int, ...], tuple[int, ...]], Substitution] = field(
        default_factory=dict, repr=False, compare=False
    )

    def field_xi(self, voted: Collection[int]) -> Substitution:
        """The XI for this absence pattern. Same pattern, same object."""
        present = set(voted)
        key = (
            tuple(pid for pid in self.starts if pid not in present),
            tuple(pid for pid in self.bench if pid not in present),
        )
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        result = substitute(
            module=self.module,
            starts=self.starts,
            bench=self.bench,
            voted=present,
            roles=self.roles,
            mode=self.mode,
            modules=self.modules,
            max_subs=self.max_subs,
        )
        self._cache[key] = result
        return result
