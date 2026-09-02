"""`assemble_roster` — roster ids + their roles and fvmma into `RosterPlayer`s. Pure.

The one rule that matters is fail-closed: a roster id with no Mantra role cannot be placed,
and guessing one builds a lineup the platform rejects, so the assembler refuses the whole
roster and names the offender rather than silently dropping him.
"""

from __future__ import annotations

import pytest

from fantabot.domain.lineup.errors import RosterIncomplete
from fantabot.domain.lineup.models import RosterPlayer, assemble_roster

ROLES = {6482: ["Por"], 2788: ["Dc"], 4179: ["W", "A"]}
FVMMA = {6482: 6.0, 2788: 20.0, 4179: 41.0}


def test_assembles_one_player_per_roster_id_in_order() -> None:
    roster = assemble_roster([6482, 2788, 4179], roles_by_id=ROLES, fvmma_by_id=FVMMA)

    assert [p.id for p in roster] == [6482, 2788, 4179]
    assert roster[2] == RosterPlayer(id=4179, roles=frozenset({"W", "A"}), fvmma=41.0)


def test_roles_are_canonicalised_to_uppercase() -> None:
    roster = assemble_roster([2788], roles_by_id={2788: ["dc"]}, fvmma_by_id={2788: 1.0})

    assert roster[0].roles == frozenset({"DC"})


def test_a_missing_fvmma_defaults_to_zero_not_a_failure() -> None:
    roster = assemble_roster([2788], roles_by_id={2788: ["Dc"]}, fvmma_by_id={})

    assert roster[0].fvmma == 0.0


def test_a_roster_id_with_no_roles_is_refused_by_name() -> None:
    with pytest.raises(RosterIncomplete) as caught:
        assemble_roster([2788, 9999], roles_by_id=ROLES, fvmma_by_id=FVMMA)

    assert "9999" in str(caught.value)


def test_an_empty_role_list_is_also_refused() -> None:
    with pytest.raises(RosterIncomplete):
        assemble_roster([2788], roles_by_id={2788: []}, fvmma_by_id=FVMMA)
