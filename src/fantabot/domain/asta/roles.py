"""Mantra role codes and the player value type L1 matches on. Pure.

The 12 Mantra codes canonicalized to UPPERCASE — the form the ``quotazioni`` table stores
(``DC``/``DS``/``POR``/``PC``). The schemi/compat JSON use mixed case (``Dc``/``Ds``/``Por``),
the rules doc lowercase; ``normalize_role`` folds all three to the canonical form so the
matcher compares a player's DB roles against the JSON matrix without guessing at case.

The 12 codes come from ``mantra_grid.models.ROLE_ORDER`` so there is a single source of
truth for what a Mantra role is.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from fantabot.domain.mantra.models import ROLE_ORDER

#: The 12 Mantra role codes, canonical (uppercase).
MANTRA_ROLES: frozenset[str] = frozenset(code.upper() for code in ROLE_ORDER)


def normalize_role(code: str) -> str:
    """Fold a role code to its canonical uppercase form, or raise if it is not one of the 12."""
    upper = code.strip().upper()
    if upper not in MANTRA_ROLES:
        raise ValueError(f"not a Mantra role code: {code!r}")
    return upper


def normalize_roles(codes: Iterable[str]) -> frozenset[str]:
    """Canonicalize a player's role codes, dropping blanks."""
    return frozenset(normalize_role(code) for code in codes if code.strip())


@dataclass(frozen=True)
class MantraPlayer:
    """One player as L1 needs him: an id and his canonical Mantra roles."""

    id: str
    roles: frozenset[str]


#: The macro bucket for a goalkeeper, in both role systems.
GOALKEEPER_MACRO = "GK"

#: A Mantra code's macro bucket. Wingers and trequartisti get their own, `MID_ATT`, because
#: they fade differently from a `C`. Keyed UPPERCASE, as `quotazioni` stores them.
MANTRA_ROLE_TO_MACRO: dict[str, str] = {
    "POR": GOALKEEPER_MACRO,
    "DC": "DEF", "B": "DEF", "DD": "DEF", "DS": "DEF",
    "M": "MID", "C": "MID", "E": "MID",
    "W": "MID_ATT", "T": "MID_ATT",
    "A": "ATT", "PC": "ATT",
}
#: A Classic letter's macro bucket, lowercase as `quotazioni` stores them.
CLASSIC_ROLE_TO_MACRO: dict[str, str] = {
    "p": GOALKEEPER_MACRO, "d": "DEF", "c": "MID", "a": "ATT",
}


def macro_role(role_code: str, system: str) -> str:
    """The macro bucket of a `quotazioni` role code. Classic codes are one letter, used
    as-is; a Mantra code may be compound (`DC;DD`) and its first component is the primary.
    An unknown code raises `KeyError` rather than guessing a bucket."""
    if system == "classic":
        return CLASSIC_ROLE_TO_MACRO[role_code]
    primary = role_code.split(";")[0]
    return MANTRA_ROLE_TO_MACRO[primary]
