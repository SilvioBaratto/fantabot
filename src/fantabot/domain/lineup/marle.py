"""The lineup's numeric role codes (`marle`) to canonical Mantra letters.

`teamLineup.lineUpInfo` gives each player's role as integers, not the letter codes the
schema and matcher use. This table maps them. It was derived 2026-09-02 by pairing the codes
observed in the roster against players whose letter roles were known from the formazione
page — 11 of the 12 Mantra roles appeared; `B` (Braccetto) did not, so its code is not yet
known and an unmapped code fails closed via `UnknownMarleRole` rather than being guessed.
"""

from __future__ import annotations

from collections.abc import Sequence

from fantabot.domain.lineup.errors import UnknownMarleRole

#: `marle` integer -> Mantra letter code. Derived, not published; extend when a new code
#: (notably `B`) is observed rather than guessing its number.
MARLE_TO_ROLE: dict[int, str] = {
    6: "Por",
    7: "Dd",
    8: "Ds",
    9: "Dc",
    10: "E",
    11: "M",
    12: "C",
    13: "T",
    14: "W",
    15: "A",
    16: "Pc",
}


def roles_from_marle(codes: Sequence[int]) -> list[str]:
    """Map a player's `marle` codes to letter roles, preserving order.

    Raises `UnknownMarleRole` for a code not in the table — a player we cannot place safely
    is surfaced, not silently dropped or mis-roled.
    """
    letters: list[str] = []
    for code in codes:
        role = MARLE_TO_ROLE.get(code)
        if role is None:
            raise UnknownMarleRole(code)
        letters.append(role)
    return letters
