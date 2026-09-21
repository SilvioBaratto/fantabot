"""`schema.slots` — a module code to its ordered slot role-sets, GK first.

The platform's `starts[]` is 11 ids in slot order with the goalkeeper first, and it judges
`starts[i]` against **its own** slot `i`. That order is not `mantra_schemi.json`'s: the
schemi list each schema's slots in the PDF table's row order, and sending `starts[]` in that
order is what drew `LUP009` on 7 of 11 modules (and, where a swapped cell is `-1` rather
than a refusal, a malus nobody would see). The order now comes from
`mantra_starts_order.json`, read from the platform's own bundle (SPEC A22,
`docs/lineup-slot-order.md`).
"""

from __future__ import annotations

import json

import pytest

from fantabot.domain.lineup import schema
from fantabot.domain.lineup.build import lineup_for_module
from fantabot.domain.lineup.models import RosterPlayer
from fantabot.domain.shared.resources import SCHEMI_FILENAME, data_dir

#: The platform's own per-module order, verbatim from `S.schemes.mantra` in
#: leghe.fantacalcio.it's `resources/chunk-Dc2l8Fqx.js` (read 2026-09-21, sha256 421ca595…).
#: A literal, not a read of the shipped file: a pin that loads what it judges pins nothing.
PLATFORM_ORDER = {
    "343": "Por Dc/B Dc Dc E C M/C E W/A A/Pc W/A",
    "3412": "Por Dc/B Dc Dc E C M/C E T A/Pc A/Pc",
    "3421": "Por Dc/B Dc Dc E M/C M E/W T/A T A/Pc",
    "352": "Por Dc/B Dc Dc E C M M/C E/W A/Pc A/Pc",
    "442": "Por Ds Dc Dc Dd E C M/C E/W A/Pc A/Pc",
    "433": "Por Ds Dc Dc Dd C M M/C W/A A/Pc W/A",
    "4312": "Por Ds Dc Dc Dd C M M/C T A/Pc T/A/Pc",
    "3511": "Por Dc/B Dc Dc E/W M C M E/W T/A A/Pc",
    "4141": "Por Ds Dc Dc Dd M W T C/T E/W A/Pc",
    "4411": "Por Ds Dc Dc Dd E/W C M E/W T/A A/Pc",
    "4231": "Por Ds Dc Dc Dd M/C M W/A T T/W A/Pc",
}


def _role_sets(labels: str) -> tuple[frozenset[str], ...]:
    return tuple(frozenset(role.upper() for role in label.split("/")) for label in labels.split())


def _schemi_row_order(module_code: str) -> tuple[frozenset[str], ...]:
    """GK + `mantra_schemi.json`'s slots — the order the builder sent before the fix."""
    raw = json.loads((data_dir() / SCHEMI_FILENAME).read_text(encoding="utf-8"))
    for entry in raw["schemi"]:
        if entry["nome"].replace("-", "") == module_code:
            outfield = (frozenset(role.upper() for role in slot) for slot in entry["slots"])
            return (frozenset({"POR"}), *outfield)
    raise ValueError(module_code)


def test_the_343_slots_are_the_platforms_order_not_the_pdf_rows() -> None:
    """Changed on purpose — the Criterion 7 exception SPEC A18 names.

    This test pinned the PDF row order (`Dc Dc Dc/B … M/C C …`) until 2026-09-21. The
    platform reads position 1 as `Dc/B` and position 5 as `C`, so that order put an M-only
    player in a pure-C slot: accepted, with a -1 nobody would have seen.
    """
    assert schema.slots("343") == (
        frozenset({"POR"}),
        frozenset({"DC", "B"}),
        frozenset({"DC"}),
        frozenset({"DC"}),
        frozenset({"E"}),
        frozenset({"C"}),
        frozenset({"M", "C"}),
        frozenset({"E"}),
        frozenset({"W", "A"}),
        frozenset({"A", "PC"}),
        frozenset({"W", "A"}),
    )


@pytest.mark.parametrize("code", sorted(PLATFORM_ORDER))
def test_every_module_follows_the_platforms_positional_order(code: str) -> None:
    assert schema.slots(code) == _role_sets(PLATFORM_ORDER[code])


@pytest.mark.parametrize("code", ["343", "3412", "3421", "352", "3511"])
def test_a_three_back_puts_dc_or_b_at_position_one(code: str) -> None:
    """SPEC A22: `Dc/B`, as the page validates it — not the lineup assistant's `Dc`.

    The chunk holds a second table whose position 1 is plain `Dc`. If the server judged by
    that one, a B-only player here would be refused (LUP009), never given a silent -1.
    """
    assert schema.slots(code)[1] == frozenset({"DC", "B"})


@pytest.mark.parametrize("code", sorted(PLATFORM_ORDER))
def test_the_order_is_a_permutation_of_the_schemas_slots(code: str) -> None:
    """Same slots, different positions: the fix may move a slot, never change one."""
    assert sorted(schema.slots(code), key=sorted) == sorted(_schemi_row_order(code), key=sorted)


def _deep_roster() -> list[RosterPlayer]:
    """Three single-role players per Mantra role, every value distinct."""
    roles = ["POR", "DD", "DS", "DC", "B", "E", "M", "C", "T", "W", "A", "PC"]
    return [
        RosterPlayer(
            id=100 * r + k, roles=frozenset({role}), fvmma=float((37 * (100 * r + k)) % 101)
        )
        for r, role in enumerate(roles, start=1)
        for k in range(3)
    ]


@pytest.mark.parametrize("code", sorted(PLATFORM_ORDER))
def test_a_modules_best_total_is_unchanged_by_the_reorder(code: str) -> None:
    """The matching's value is permutation-invariant; only where each player is sent moves."""
    roster = _deep_roster()
    value = {p.id: p.fvmma for p in roster}

    new = lineup_for_module(roster, code, value=value)
    old = lineup_for_module(roster, code, value=value, slots_provider=_schemi_row_order)

    assert new is not None and old is not None
    assert sum(value[pid] for pid in new) == sum(value[pid] for pid in old)


def test_every_slot_list_has_eleven_slots() -> None:
    for code in schema.modules():
        assert len(schema.slots(code)) == 11, code


def test_all_eleven_platform_modules_resolve() -> None:
    expected = {"3412", "3421", "343", "3511", "352", "4141", "4231", "4312", "433", "4411", "442"}

    assert schema.modules() == expected


def test_an_unknown_module_code_raises() -> None:
    with pytest.raises(ValueError, match="999"):
        schema.slots("999")
