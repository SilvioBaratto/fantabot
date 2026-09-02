"""`schema.slots` — a module code to its ordered slot role-sets, GK first.

The platform's `starts[]` is 11 ids in slot order with the goalkeeper first; the shipped
`mantra_schemi.json` describes the 10 outfield slots. This maps the platform's dashless
module code (`"343"`) onto the schema (`"3-4-3"`) and prepends the implicit GK slot, so the
builder can lay players into `starts[]` positionally and avoid the live `LUP009`.
"""

from __future__ import annotations

import pytest

from fantabot.domain.lineup import schema


def test_the_343_slots_are_gk_first_then_the_ten_outfield_in_order() -> None:
    assert schema.slots("343") == (
        frozenset({"POR"}),
        frozenset({"DC"}),
        frozenset({"DC"}),
        frozenset({"DC", "B"}),
        frozenset({"E"}),
        frozenset({"M", "C"}),
        frozenset({"C"}),
        frozenset({"E"}),
        frozenset({"W", "A"}),
        frozenset({"A", "PC"}),
        frozenset({"W", "A"}),
    )


def test_every_slot_list_has_eleven_slots() -> None:
    for code in schema.modules():
        assert len(schema.slots(code)) == 11, code


def test_all_eleven_platform_modules_resolve() -> None:
    expected = {"3412", "3421", "343", "3511", "352", "4141", "4231", "4312", "433", "4411", "442"}

    assert schema.modules() == expected


def test_an_unknown_module_code_raises() -> None:
    with pytest.raises(ValueError, match="999"):
        schema.slots("999")
