"""The player as the lineup builder needs him, and the assembler that produces him. Pure.

`RosterPlayer` carries only what the value model and the matcher use: the fantacalcio id,
the canonical Mantra roles, and the Mantra fvm. Roles and fvm both come from `quotazioni`
(the asta side reads the same table); the application layer builds the two maps and calls
`assemble_roster`, so this stays free of any adapter.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from fantabot.domain.asta.roles import normalize_roles
from fantabot.domain.lineup.errors import RosterIncomplete


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


def assemble_roster(
    roster_ids: Sequence[int],
    *,
    roles_by_id: Mapping[int, Sequence[str]],
    fvmma_by_id: Mapping[int, float],
) -> list[RosterPlayer]:
    """Build one `RosterPlayer` per roster id, in order.

    **Fail closed.** A roster id with no Mantra role cannot be placed in any schema, and a
    guessed role builds a lineup the platform refuses (`LUP0xx`), so a missing or empty role
    list raises `RosterIncomplete` naming the id rather than dropping him. A missing fvm is
    not fatal — it defaults to `0.0`, which simply makes him the last picked.
    """
    roster: list[RosterPlayer] = []
    for player_id in roster_ids:
        codes = roles_by_id.get(player_id) or ()
        roles = normalize_roles(codes)
        if not roles:
            raise RosterIncomplete(player_id)
        roster.append(
            RosterPlayer(id=player_id, roles=roles, fvmma=float(fvmma_by_id.get(player_id, 0.0)))
        )
    return roster
