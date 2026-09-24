"""The auction state the optimizer plans against, and the roster it returns. Pure.

``AstaState`` is what we hold right now: the players already bought (``owned``, their cost
sunk in ``spent``), the players anyone has bought (``taken``, unavailable), and the total
budget. ``RosterRules`` is the league's composition — for lega 4103937 as it last synced, a
32-man rosa with the ``minrl=[2, 23]`` floors and the ``maxrl=[4, 28]`` ceilings.
``Roster`` and ``OptimizationResult`` are what the optimizer emits.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from fantabot.domain.classic.state import ROLE_ORDER, ClassicRosterRules, classic_rules

if TYPE_CHECKING:
    from fantabot.domain.asta.roles import MantraPlayer
    from fantabot.domain.classic.roles import ClassicPlayer


def _satisfiable_ceiling(declared: int, floor: int) -> int:
    """A declared ceiling, read so that its own floor can still be met. Pure.

    A declared ceiling *below its own floor* is a lega contradicting itself (``maxrl[i] <
    minrl[i]``, which the platform has never sent). Read literally it is unsatisfiable, and
    ``optimize_roster`` would say so as an ``InfeasibleRoster`` once per two-second cycle for
    a whole evening. So the floor wins: "exactly this many" is the one reading of a
    contradictory band that can still be bought.

    One function rather than the same ``max`` at both sites, because ``max_goalkeepers()``
    and ``declared_ceiling_total()`` answer the same question — how many players this half
    may supply — and two copies of it can disagree: a total counting a contradictory ceiling
    at its literal value refuses a size the accessors say the band fills.
    """
    return max(declared, floor)


def _binding_ceiling(derived: int, declared: int | None, floor: int) -> int:
    """The tighter of the ceiling the size forces and the one the lega declared. Pure.

    ``declared is None`` means no ``maxrl`` was stated for this half, and then the derivation
    is the whole answer, untouched — including where it is nonsense. A bare
    ``replace(rules, size=25)`` over the built-in ``30/2/28`` reports **-3** keepers; that is
    the trap ``resize_band`` exists to prevent and ``test_asta_resize_band`` pins by name, and
    clamping it here would hide it rather than fix it.
    """
    if declared is None:
        return derived
    return min(derived, _satisfiable_ceiling(declared, floor))


def _declared_ceiling(max_roles: Sequence[int] | None, index: int) -> int | None:
    """One half of a ``maxrl``, or ``None`` where the lega stated nothing for that half. Pure.

    **Per entry, never all-or-nothing.** A ``maxrl`` that is short, or carries a 0 beside a
    real number, used to discard *both* ceilings — which throws away the tighter of the two
    constraints because the other one looked odd, and that is the fail-open direction the
    whole defect is made of. The half that was stated is read; the half that was not derives,
    exactly as every band predating the 2026-09-02 drift does.

    **A 0 is an absence, not a ceiling.** ``domain/lega/parse.py`` renders an absent ``maxrl``
    as ``()`` and never as zeros, and a lega permitting zero goalkeepers would contradict the
    platform's own Mantra minimum of two (``rules/leghe-private.md`` §Mantra). CLAUDE.md
    states the same rule for FantaLab — "a room that states 0 is not stating anything". The
    other reading is not the neutral one either: a literal 0 is clamped up to its floor by
    ``_satisfiable_ceiling``, which pins that half to exactly its minimum for a whole
    auction — a band no lega declared, arrived at from a field it left blank.
    """
    if not max_roles or index >= len(max_roles):
        return None
    ceiling = int(max_roles[index])
    return ceiling if ceiling > 0 else None


@dataclass(frozen=True)
class RosterRules:
    """League roster composition: two super-roles, and the band a lega declares over them.

    Two super-roles (Mantra ``sroles=2``): goalkeepers and everyone else.

    * **The platform's rule.** A Mantra rosa is "minimum 23 players including 2
      goalkeepers, no per-role slot constraints at all" —
      ``rules/leghe-private.md`` §Mantra, from fantacalcio.it/regolamenti/leghe-private.
      Two is the floor for every Mantra league, not a per-league choice.
    * **This league's setting, and it moved under us.** On 2026-08-26 the roster settings
      endpoint returned ``minrl = maxrl = [2, 28]`` over a 30-man rosa for lega 4103937
      (``docs/leghe-api.md``). They were *equal*, so the split was pinned and
      ``max_goalkeepers()`` deriving ``size - min_movement = 2`` was the declared ceiling as
      well as the arithmetic one. **Since 2026-09-02 it reads 25/32 with ``minrl=[2, 23]``
      and ``maxrl=[4, 28]``** (CLAUDE.md, "the lega's roster rules changed under us"): a
      variable size and a real band, where that same derivation gives **9** keepers against
      a declared **4**. This docstring asserted the equal split as current fact until
      2026-09-24, and it is what kept ``maxrl`` unread on the Mantra path for three weeks.

    So a declared ceiling is carried, not derived. ``max_goalkeepers_declared`` /
    ``max_movement_declared`` hold each half of ``maxrl`` when a lega states that half — one
    without the other is a real band — and are ``None`` for "nothing was declared", which is
    what a band read from a room, or built from these defaults, still is. Both constraints are real, so the accessors return whichever binds
    first: a 25-man rosa owing 23 movement players has room for two keepers whatever
    ``maxrl`` allows, and a 32-man one owing 23 has room for nine that ``maxrl`` forbids.

    Nothing downstream catches a ceiling this band gets wrong. ``optimizer._build_mantra``
    and ``reservation.opportunistic_walkaway`` read these two methods and both fail open,
    ``max_bid`` reserves credits and checks no role at all, and ``docs/fantalab/01:142``
    records that the platform's own MAX is client-enforced with no server backstop.

    It stays here as data rather than baked into the algorithm because the *other* lega
    (3584692) is Classic with ``[3, 8, 8, 6]``, a different shape entirely.
    """

    size: int = 30
    goalkeeper_roles: frozenset[str] = frozenset({"POR"})
    min_goalkeepers: int = 2
    min_movement: int = 28
    #: ``maxrl`` as a lega declared it, or ``None`` for "nothing was declared". Optional and
    #: last so every band built without one — a room's, a test's, these defaults — keeps
    #: meaning exactly what it meant.
    max_goalkeepers_declared: int | None = None
    max_movement_declared: int | None = None

    def max_goalkeepers(self) -> int:
        return _binding_ceiling(
            self.size - self.min_movement, self.max_goalkeepers_declared, self.min_goalkeepers
        )

    def max_movement(self) -> int:
        return _binding_ceiling(
            self.size - self.min_goalkeepers, self.max_movement_declared, self.min_movement
        )

    def declared_ceiling_total(self) -> int | None:
        """How many players the *declared* band can supply, or ``None`` if none was declared.

        The Mantra twin of ``sum(rules.max_of(role) for role in rules.roles())``, which
        ``resize_band`` already uses to refuse a Classic growth no band can fill. Both halves
        have to be present: one declared ceiling says nothing about how many the other may
        supply, and since ``rules_for_lega`` reads ``maxrl`` per entry a band really can carry
        one and not the other.

        Each half is counted at ``_satisfiable_ceiling``, the same value the accessors report,
        so a ceiling read up to its floor supplies that many players here too. Counting the
        literal number instead would have this refuse a size ``max_goalkeepers()`` says the
        band fills.
        """
        if self.max_goalkeepers_declared is None or self.max_movement_declared is None:
            return None
        return _satisfiable_ceiling(
            self.max_goalkeepers_declared, self.min_goalkeepers
        ) + _satisfiable_ceiling(self.max_movement_declared, self.min_movement)


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

    **`maxrl` is read on both paths, and the Mantra one dropped it on the floor.** The
    Classic branch has always carried it into the per-role bands; the Mantra return built
    `RosterRules(size, minrl[0], minrl[1])` and there was no field for a declared ceiling to
    land in, so `max_goalkeepers()` fell back to `size - min_movement`: **9** on the live
    `32 / [2, 23] / [4, 28]`, against the 4 this lega permits. Both readers of that method
    fail open and the platform does not backstop the cap, so `asta optimize` planned — and
    `asta bid` would have bought — keepers the rosa may not hold. A lega that declares no
    `maxrl` keeps the derivation and is unchanged.

    **A declared ceiling is read per half and never discarded.** A `maxrl` arriving short, or
    carrying a 0, is a half nobody stated (`_declared_ceiling`): that half derives and **the
    other half is still honoured**. The first version of this read dropped *both* whenever
    one looked odd, which is the fail-open direction twice over.

    **Ceilings that cannot fill `roster_size` bind the total; they do not lose.** `xsltc = 33`
    against a `maxrl` supplying 32 is not a lega contradicting itself — it is one whose
    per-role ceilings run out before its declared maximum does, and 32 is the buyable reading.
    So the size is clamped to what the band supplies. The first version discarded the pair
    instead and still returned `SNAPSHOT_DECLARED`, which reinstates the 9-keeper derivation
    under a provenance saying the lega declared it. The margin was **zero** on the live lega
    (`4 + 28 = 32 = xsltc`) and the test was `<`, so one added to `xsltc`, or one taken off
    either ceiling, turned the whole cap back off in silence — the 2026-08-26 drift again,
    one notch along.
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
    # Read per entry, unlike the Classic `highs` above: that one falls back to `min_roles`
    # wholesale, and a `maxrl` half the Mantra path cannot read must stay *unread* — a ceiling
    # silently equal to the floor would pin the split the 2026-09-02 drift widened.
    rules = RosterRules(
        size=int(roster_size),
        min_goalkeepers=int(min_roles[0]),
        min_movement=int(min_roles[1]),
        max_goalkeepers_declared=_declared_ceiling(max_roles, 0),
        max_movement_declared=_declared_ceiling(max_roles, 1),
    )
    # The ceilings bind the total too. `xsltc` is a *maximum* roster size and `maxrl` caps
    # each half, so a pair supplying fewer than `xsltc` players means the rosa stops where
    # they stop — that is the size to plan against, and `optimize_roster` fills `rules.size`
    # exactly. Clamping rather than refusing because this runs unattended over two numbers
    # the lega itself stated; `resize_band` refuses the same shortfall because there an
    # operator typed `--size` and can be told.
    # Written as a `min` and not as a `size > fillable` test: at equality — which is where
    # the live lega sits — the two are the same object, so the boundary is unobservable and
    # nothing could ever pin which side of it the code is on.
    fillable = rules.declared_ceiling_total()
    if fillable is not None:
        rules = replace(rules, size=min(rules.size, fillable))
    return rules, SNAPSHOT_DECLARED


def rules_for_room(
    *,
    selection: str | None,
    min_goalkeepers: int | None = None,
    min_others: int | None = None,
    target_size: int | None = None,
    classic_band: Mapping[str, int] | None = None,
) -> tuple[RosterRules | ClassicRosterRules, str]:
    """`RosterRules`, derived from what a room actually declares, with a stated provenance.

    **Reading the room too literally is the named risk** (the parity phase's plan §2 —
    archived, not in this checkout). A room under `"no-limit-per-role"` — the common
    case — has no per-role floor to read at all, and the
    room's own `min_player`/`max_player` totals say only "at least this many players," never
    how many of them must be goalkeepers. Deriving `min_goalkeepers=0` from that silence
    would be a room-declared zero-keeper floor no room actually stated, not the honest
    "unknown" it is. **That argument is why the two totals are not parameters here.** They
    were, from 3.3 until 2026-09-24, and in that whole time neither was ever read: a
    signature that takes the room's totals and branches on none of them reads, at both call
    sites, as though the room's shape were being honoured.

    So only `selection == "min-max-goalie-others"` with both halves of the band present
    counts as "the room said something"; everything else — nothing parsed, the wrong
    selection mode, only one half of the band — returns today's default (`RosterRules()`:
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

    **A declared ceiling rides along unchanged, and is refused rather than stretched.**
    `maxrl` is a rule about the rosa, not about its total, so one now above the new size just
    stops binding — `max_movement()` already takes the tighter of it and `size -
    min_goalkeepers`. A size the declared pair cannot *fill* is the Classic refusal below,
    reached by the Mantra route: derived ceilings always leave room for `size` players and a
    declared pair need not, and the alternative is `optimize_roster` running out of legal
    candidates an hour later, inside the bidding loop. **`rules_for_lega` answers that same
    shortfall by clamping the size instead**, and the asymmetry is the point: there a cron run
    reconciles two numbers the lega itself stated and nobody is watching, here an operator
    typed one and can be told which of the two he is fighting.

    So: **keep the floors, and clamp only as far as the new size forces.** On a shrink that
    reproduces `drop_unvaluable`'s floors exactly — 30 → 25 gives `min_movement=23`, which is
    its `28 - 5` — and on a growth it leaves the declared floor alone. (The *ceilings* are
    where the two part company, and deliberately: `drop_unvaluable` shrinks the declared one
    because a slot is consumed, and here nothing is.) Classic goes through
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
    # The Classic refusal above, in Mantra's two-super-role shape, and it can only bite when
    # a lega actually sent `maxrl` — `declared_ceiling_total()` is `None` otherwise and every
    # band that predates the drift resizes exactly as it did.
    declared = rules.declared_ceiling_total()
    if declared is not None and size > declared:
        raise ValueError(
            f"--size {size} cannot be filled: this band allows at most {declared} players"
        )
    return replace(
        rules, size=size, min_movement=min(rules.min_movement, size - rules.min_goalkeepers)
    )


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

    **A declared movement ceiling shrinks with the floor beneath it**, under that same
    assumption: `maxrl` counts the whole rosa and he is in it, so a ceiling left alone would
    let the plan buy its full movement allowance *on top of* him. It is deliberately not what
    `resize_band` does — there the size was wrong and the band was not, and no slot is
    consumed. Feasibility survives either way: both the size and the ceiling fall by the same
    count, so a band that could fill itself still can.

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
        max_movement_declared=(
            None
            if rules.max_movement_declared is None
            else max(0, rules.max_movement_declared - len(dropped))
        ),
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
