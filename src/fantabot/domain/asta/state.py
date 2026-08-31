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
