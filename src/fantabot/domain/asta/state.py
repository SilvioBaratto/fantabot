"""The auction state the optimizer plans against, and the roster it returns. Pure.

``AstaState`` is what we hold right now: the players already bought (``owned``, their cost
sunk in ``spent``), the players anyone has bought (``taken``, unavailable), and the total
budget. ``RosterRules`` is the league's composition — for lega 4103937, 30 players with the
``minrl=[2,28]`` split. ``Roster`` and ``OptimizationResult`` are what the optimizer emits.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RosterRules:
    """League roster composition.

    Two super-roles (Mantra ``sroles=2``): goalkeepers and everyone else. ``minrl=[2,28]``
    for lega 4103937 → at least 2 goalkeepers and at least 28 movement players in a 30-man
    rosa, which (since they sum to the size) pins it to exactly 2 + 28. The exact split is a
    league setting still to be confirmed — see the plan's open questions — so it lives here
    as data, not baked into the algorithm.
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
