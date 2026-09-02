"""The auction state the optimizer plans against, and the roster it returns. Pure.

``AstaState`` is what we hold right now: the players already bought (``owned``, their cost
sunk in ``spent``), the players anyone has bought (``taken``, unavailable), and the total
budget. ``RosterRules`` is the league's composition — for lega 4103937, 30 players with the
``minrl=[2,28]`` split. ``Roster`` and ``OptimizationResult`` are what the optimizer emits.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fantabot.domain.asta.roles import MantraPlayer


@dataclass(frozen=True)
class RosterRules:
    """League roster composition. **Two goalkeepers, confirmed twice.**

    Two super-roles (Mantra ``sroles=2``): goalkeepers and everyone else.

    * **The platform's rule.** A Mantra rosa is "minimum 23 players including 2
      goalkeepers, no per-role slot constraints at all" —
      ``rules/leghe-private.md`` §Mantra, from fantacalcio.it/regolamenti/leghe-private.
      Two is the floor for every Mantra league, not a per-league choice.
    * **This league's setting.** The roster settings endpoint returns
      ``minrl = maxrl = [2, 28]`` for lega 4103937 (``docs/leghe-api.md``, fetched
      2026-08-26). ``minrl`` and ``maxrl`` are *equal*, so the split is fixed rather than
      ranged: exactly 2 goalkeepers and exactly 28 movement players in a 30-man rosa.

    Either source alone gives 2, and they agree, so ``max_goalkeepers()`` deriving 2 from
    ``size - min_movement`` is not a coincidence of the arithmetic — it is the setting.

    It stays here as data rather than baked into the algorithm because the *other* lega
    (3584692) is Classic with ``[3, 8, 8, 6]``, a different shape entirely.
    """

    size: int = 30
    goalkeeper_roles: frozenset[str] = frozenset({"POR"})
    min_goalkeepers: int = 2
    min_movement: int = 28

    def max_goalkeepers(self) -> int:
        return self.size - self.min_movement

    def max_movement(self) -> int:
        return self.size - self.min_goalkeepers


@dataclass(frozen=True)
class AstaState:
    """What we hold at a point in the auction."""

    owned: tuple[str, ...] = ()
    spent: float = 0.0
    taken: frozenset[str] = field(default_factory=frozenset)
    total_budget: float = 500.0

    @property
    def remaining_budget(self) -> float:
        return self.total_budget - self.spent


#: What `rules_for_room` returns when it derived a real band from the room, versus when it
#: fell back to today's default because the room said nothing usable. The exact strings, not
#: prose composed at each call site — a provenance an operator cannot grep for consistently is
#: one they stop trusting.
ROOM_DECLARED = "read from the room"
ASSUMED_NOTHING = "assumed — the room declared nothing"


def rules_for_room(
    *,
    selection: str | None,
    min_player: int | None,
    max_player: int | None,
    min_goalkeepers: int | None = None,
    min_others: int | None = None,
    target_size: int | None = None,
) -> tuple[RosterRules, str]:
    """`RosterRules`, derived from what a room actually declares, with a stated provenance.

    **Reading the room too literally is the named risk (`tasks/plan.md` §2).** A room under
    `"no-limit-per-role"` — the common case — has no per-role floor to read at all, and
    `min_player`/`max_player` alone say only "at least this many total," never how many must
    be goalkeepers. Deriving `min_goalkeepers=0` from that silence would be a room-declared
    zero-keeper floor no room actually stated, not the honest "unknown" it is. So only
    `selection == "min-max-goalie-others"` with both halves of the band present counts as
    "the room said something"; everything else — nothing parsed, the wrong selection mode,
    only one half of the band — returns today's default (`RosterRules()`:
    `size=30, min_goalkeepers=2, min_movement=28`) labelled `ASSUMED_NOTHING`.

    Measured over the live registry (`tests/golden/seed_live_sample.json`, a trimmed real
    slice of `data/seed_live.json`): **no Mantra room declares `min_player == 30`** — the
    values that exist range from 19 to 29, and 153 of 247 rooms declare none at all. The old
    hard-coded `size=30` was never "what rooms actually say"; it was lega 4103937's own
    setting, generalised to every room that follows this bot's default.

    `min_goalkeepers + min_others` is the size read from the room — matching the platform's
    own invariant that this sum equals `min_player` (`docs/fantalab/04:485`) — unless
    `target_size` overrides it (an operator's explicit choice to target `max_player` or
    something else within the declared band, not this function's call to make).
    """
    if selection == "min-max-goalie-others" and min_goalkeepers is not None and min_others is not None:
        size = target_size if target_size is not None else min_goalkeepers + min_others
        return (
            RosterRules(size=size, min_goalkeepers=min_goalkeepers, min_movement=min_others),
            ROOM_DECLARED,
        )
    return RosterRules(), ASSUMED_NOTHING


def drop_unvaluable(
    state: AstaState, pool: Sequence[MantraPlayer], rules: RosterRules
) -> tuple[AstaState, RosterRules, list[str]]:
    """Set aside owned players the pool cannot name, and shrink the band to match. Pure.

    ``optimize_roster`` refuses a state whose ``owned`` holds an id absent from the pool,
    and it is right to — a roster it cannot value is not a roster. But the bidder rebuilds
    ``AstaState`` from the ``purchases/`` ledger every cycle and a purchase record is never
    removed, so the offending id returns every two seconds. Catching the exception without
    removing its cause is not a hold for one lot; it is a stop for the rest of the evening.

    **Both halves move together.** He is genuinely ours: the credits stay spent, and the
    roster band shrinks by one, because a slot he fills is a slot we must not plan to buy
    again. Dropping him from ``owned`` alone would ask the optimiser for a replacement and
    end the night one player over the limit.

    The band that shrinks is the movement one, even for a goalkeeper: his role is exactly
    what we do not know, since not being in the pool is what put him here. That is a
    deliberate approximation, and it is why the caller names him — a human reading the
    heartbeat can correct an assumption a silent fallback would hide.

    One live instance on 2026-09-01: fantacalcio id 7581, Konaté A., in FantaLab's listone
    and absent from our ``quotazioni``.
    """
    known = {player.id for player in pool}
    dropped = [pid for pid in state.owned if pid not in known]
    if not dropped:
        return state, rules, []

    kept = replace(state, owned=tuple(pid for pid in state.owned if pid in known))
    shrunk = replace(
        rules,
        size=max(0, rules.size - len(dropped)),
        min_movement=max(0, rules.min_movement - len(dropped)),
    )
    return kept, shrunk, dropped


@dataclass(frozen=True)
class Roster:
    """A completed roster: its players, its total cost, and its objective value."""

    player_ids: tuple[str, ...]
    total_cost: float
    objective: float

    def __len__(self) -> int:
        return len(self.player_ids)


@dataclass(frozen=True)
class OptimizationResult:
    """The current optimal roster, and the next-best plans if a target is lost."""

    optimal: Roster
    fallbacks: tuple[Roster, ...] = ()
