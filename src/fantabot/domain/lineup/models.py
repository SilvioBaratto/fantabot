"""The player as the lineup builder needs him, and the assembler that produces him. Pure.

`RosterPlayer` carries only what the value model and the matcher use: the fantacalcio id,
the canonical Mantra roles, and the Mantra fvm. Roles and fvm both come from `quotazioni`
(the asta side reads the same table); the application layer builds the two maps and calls
`assemble_roster`, so this stays free of any adapter.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from fantabot.domain.asta.roles import normalize_roles
from fantabot.domain.lineup.errors import RosterIncomplete

#: Fold a player's role codes to a canonical set. `asta.roles.normalize_roles` (the 12 Mantra
#: codes) by default; the Classic path injects `classic.roles.normalize_roles` (P/D/C/A), which
#: is what stops a Classic `C`/`A` being silently accepted as a Mantra code.
RoleNormalizer = Callable[[Sequence[str]], frozenset[str]]


@dataclass(frozen=True)
class RosterPlayer:
    """One owned player: id, canonical Mantra roles, and the value signal the matcher ranks on.

    The `fvmma` field is that value signal; in this phase it carries the platform's
    `indexCompare` rating (the data-source pivot — `quotazioni` ids do not join the roster),
    not the Mantra fvm the name once meant.
    """

    id: int
    roles: frozenset[str]
    fvmma: float


@dataclass(frozen=True)
class PlannedLineup:
    """A submittable formation: the module, the ordered `starts`/`bench`, and the ids the
    `gaming/v1` payload needs. `starts` is 11 (GK first), `bench` is the reserve order."""

    module: str
    starts: tuple[int, ...]
    bench: tuple[int, ...]
    competition: int
    mday: int
    cmday: int
    tid: int
    #: Whether the lineup applies to every competition of the lega (the DTO's `allComp`).
    #: Mirrored from the saved lineup, because a lega with a cup keeps one lineup for all.
    all_comp: bool = False
    #: `capt` — `(captain, vice)` or empty when the lega has no captain (`domain/lineup/extras`).
    captains: tuple[int, ...] = ()
    #: `(swtcA, swtcB)` — a starter and the reserve who replaces him first — or None.
    switch: tuple[int, int] | None = None
    #: `swtcMdl` — the module after the switch fires. None means unchanged (a same-role
    #: switch); a cross-role one (a midfielder for a defender) turns 442 into 352.
    switch_module: str | None = None
    #: P(at least four defenders get a vote), for the modificatore difesa; None when unrated.
    defence_bonus_p: float | None = None
    #: The expected average of the votes the modifier takes, and P * E[bonus] — the points the
    #: back four is credited with when it competes against other modules. None when unrated.
    defence_avg_vote: float | None = None
    defence_bonus_ev: float | None = None
    #: The lega's substitution cap (`subst.ssnum`) the probability was computed under.
    max_subs: int | None = None
    #: E[captain modifier] for `captains` (a lega with `smodcp`); None where there is none.
    captain_bonus_ev: float | None = None


def assemble_roster(
    roster_ids: Sequence[int],
    *,
    roles_by_id: Mapping[int, Sequence[str]],
    fvmma_by_id: Mapping[int, float],
    normalize: RoleNormalizer = normalize_roles,
) -> list[RosterPlayer]:
    """Build one `RosterPlayer` per roster id, in order.

    **Fail closed.** A roster id with no role cannot be placed in any schema, and a guessed
    role builds a lineup the platform refuses (`LUP0xx`), so a missing or empty role list
    raises `RosterIncomplete` naming the id rather than dropping him. A missing fvm is not
    fatal — it defaults to `0.0`, which simply makes him the last picked.

    `normalize` selects the role vocabulary: Mantra's 12 codes by default, or the Classic
    P/D/C/A normalizer for a Classic roster — which is load-bearing, because the Mantra
    normalizer silently *accepts* a Classic `C`/`A` (both are Mantra codes too) while raising
    on `P`/`D`, mis-bucketing two of the four macro roles with no error.
    """
    roster: list[RosterPlayer] = []
    for player_id in roster_ids:
        codes = roles_by_id.get(player_id) or ()
        roles = normalize(codes)
        if not roles:
            raise RosterIncomplete(player_id)
        roster.append(
            RosterPlayer(id=player_id, roles=roles, fvmma=float(fvmma_by_id.get(player_id, 0.0)))
        )
    return roster
