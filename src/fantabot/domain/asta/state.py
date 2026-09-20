"""The auction state the optimizer plans against, and the roster it returns. Pure.

``AstaState`` is what we hold right now: the players already bought (``owned``, their cost
sunk in ``spent``), the players anyone has bought (``taken``, unavailable), and the total
budget. ``RosterRules`` is the league's composition — for lega 4103937, 30 players with the
``minrl=[2,28]`` split. ``Roster`` and ``OptimizationResult`` are what the optimizer emits.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from fantabot.domain.classic.state import ROLE_ORDER, ClassicRosterRules, classic_rules

if TYPE_CHECKING:
    from fantabot.domain.asta.roles import MantraPlayer
    from fantabot.domain.classic.roles import ClassicPlayer


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
#: Source-neutral since 2.1. It read "assumed — the room declared nothing", which was true
#: while `rules_for_room` was the only caller and false the moment `rules_for_lega` joined
#: it: `asta optimize` reads a *lega* and there is no room in sight. A provenance that names
#: the wrong source is worse than a vague one.
ASSUMED_NOTHING = "assumed — nothing was declared"
#: The third source, and it is not either of the other two. A *room* declares its band
#: tonight; a *lega* declares one at its last sync, and the two can disagree — a riparazione
#: room runs a different band from the lega's season settings. Naming both "read from the
#: room" would make 1.8's provenance column lie about where the number came from.
SNAPSHOT_DECLARED = "read from the lega's last sync"
#: The fourth, and it is not any of the other three: an operator passed `--size`. Kept apart
#: for the reason the first three are — a room declares a band tonight, a lega declares one
#: at its last sync, and a person types one. Folding this into `ROOM_DECLARED` would make
#: 1.8's provenance column say the room stated a number the room was never asked.
OPERATOR_DECLARED = "given with --size"


def rules_for_lega(
    *,
    role_groups: int | None,
    roster_size: int | None,
    min_roles: Sequence[int] | None,
    max_roles: Sequence[int] | None = None,
) -> tuple[RosterRules | ClassicRosterRules, str]:
    """The roster band a lega declares, and where it came from. Pure.

    `asta optimize` and `asta bid` built a bare `RosterRules()` — **size 30, whatever the
    lega declares** — while `GET /asta/plan` read the snapshot. On 2026-08-26
    `settings/rosters` read 30/30 with `minrl = maxrl = [2, 28]`; on 2026-09-02 it read
    **25/32 with `minrl=[2, 23]`, `maxrl=[4, 28]`**. A plan built on 30 against a 25-man lega
    is not slightly wrong, it is unbuyable: the command exits with *"cannot complete the
    roster: 19/30 filled"*.

    Takes the four primitives rather than a `LeagueSnapshot`, so `domain/` keeps knowing
    nothing about persistence — the same reason `rules_for_room` takes a room's fields
    instead of its JSON.

    **`role_groups` decides the type even when the band is unknown**, and that matters more
    than the band does: falling back to `RosterRules()` for a Classic lega would plan it
    against the eleven Mantra schemi. `sroles=1` is Classic and `sroles=2` is Mantra —
    `interface/lineup.py`'s detection, which the lineup path settled first.

    An incomplete snapshot is **assumed, never invented**: the default band under
    `ASSUMED_NOTHING`, because a band nobody declared and a band the lega stated are
    different facts and only one is worth planning on.
    """
    classic = role_groups == 1
    default: RosterRules | ClassicRosterRules = ClassicRosterRules() if classic else RosterRules()

    if roster_size is None or not min_roles:
        return default, ASSUMED_NOTHING

    if classic:
        if len(min_roles) < len(ROLE_ORDER):
            return default, ASSUMED_NOTHING
        highs = max_roles if max_roles and len(max_roles) >= len(ROLE_ORDER) else min_roles
        bands = tuple(
            (role, int(min_roles[index]), int(highs[index]))
            for index, role in enumerate(ROLE_ORDER)
        )
        return ClassicRosterRules(size=int(roster_size), bands=bands), SNAPSHOT_DECLARED

    if len(min_roles) < 2:
        return default, ASSUMED_NOTHING
    return (
        RosterRules(
            size=int(roster_size),
            min_goalkeepers=int(min_roles[0]),
            min_movement=int(min_roles[1]),
        ),
        SNAPSHOT_DECLARED,
    )


def rules_for_room(
    *,
    selection: str | None,
    min_player: int | None,
    max_player: int | None,
    min_goalkeepers: int | None = None,
    min_others: int | None = None,
    target_size: int | None = None,
    classic_band: Mapping[str, int] | None = None,
) -> tuple[RosterRules | ClassicRosterRules, str]:
    """`RosterRules`, derived from what a room actually declares, with a stated provenance.

    **Reading the room too literally is the named risk (`tasks/archive/parity-plan.md` §2).** A room under
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
    # Classic (`sroles=1`): a `static` selection carries a four-role P/D/C/A band that the
    # two-super-role RosterRules cannot express, so it becomes a ClassicRosterRules.
    if selection == "static" and classic_band:
        return classic_rules(classic_band), ROOM_DECLARED
    if selection == "min-max-goalie-others" and min_goalkeepers is not None and min_others is not None:
        size = target_size if target_size is not None else min_goalkeepers + min_others
        return (
            RosterRules(size=size, min_goalkeepers=min_goalkeepers, min_movement=min_others),
            ROOM_DECLARED,
        )
    return RosterRules(), ASSUMED_NOTHING


def resize_band(
    rules: RosterRules | ClassicRosterRules, size: int
) -> RosterRules | ClassicRosterRules:
    """The same band, resized to `size`, kept coherent. Pure.

    **`asta bid` is unauthenticated**, so the band it plans and caps against comes from
    `--lega` — which defaults to `settings.fantabot_league_id`, a *leghe.fantacalcio* league
    id with no relation to the FantaLab room being bid in. `--size` is how the room's own
    total reaches it, and this is the rule that applies it. `RoomTracker` computes
    `max_bid(credits_left, rules.size - len(owned))`, so the size is the divisor of the MAX
    cap, and the band also sizes the plan.

    **A bare `replace(rules, size=...)` is the trap.** `max_goalkeepers()` is
    `size - min_movement`, so a 25 over the built-in `30/2/28` yields **-3 keepers**.

    **And the floors cannot be re-derived from the size**, which is the second trap. The
    obvious invariant — `size == min_goalkeepers + min_movement`, which `rules_for_room`
    really does hold to — is not general: measured on the live database, lega 4103937's last
    sync reads `size=32, min_goalkeepers=2, min_movement=23`, because `rules_for_lega` takes
    the size from `roster_size` and the floors from `minrl`, and those are a maximum and two
    minimums. Deriving `min_movement = size - min_goalkeepers` there would invent a floor
    seven players above what the lega declared.

    So: **keep the floors, and clamp only as far as the new size forces.** On a shrink that
    reproduces `drop_unvaluable` exactly — 30 → 25 gives `min_movement=23`, which is its
    `28 - 5` — and on a growth it leaves the declared floor alone. Classic goes through
    `ClassicRosterRules.shrunk`, which already trims floors largest-first to keep
    `sum(min) <= size`; a second copy of that arithmetic would be a second answer.

    Refusals rather than nonsense, because the alternative surfaces much later and from
    inside a live loop: `optimize_roster` raising once per two-second cycle for an evening.
    """
    if size < 1:
        raise ValueError(f"--size {size} is not a roster")
    if isinstance(rules, ClassicRosterRules):
        # Under the `static` selection every band is pinned (`min == max`), so the roles can
        # supply exactly `sum(max)` players and no more. A size above that is a roster the
        # room can never fill, and the optimizer would hunt for a player no role may add.
        ceiling = sum(rules.max_of(role) for role in rules.roles())
        if size > ceiling:
            raise ValueError(
                f"--size {size} cannot be filled: this band allows at most {ceiling} players"
            )
        return rules.shrunk(rules.size - size)
    if size < rules.min_goalkeepers:
        raise ValueError(
            f"--size {size} is below the {rules.min_goalkeepers} goalkeepers this band requires"
        )
    return replace(rules, size=size, min_movement=min(rules.min_movement, size - rules.min_goalkeepers))


def drop_unvaluable(
    state: AstaState,
    pool: Sequence[MantraPlayer | ClassicPlayer],
    rules: RosterRules | ClassicRosterRules,
) -> tuple[AstaState, RosterRules | ClassicRosterRules, list[str]]:
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
    if isinstance(rules, ClassicRosterRules):
        return kept, rules.shrunk(len(dropped)), dropped
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
